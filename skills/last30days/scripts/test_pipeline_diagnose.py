"""Unit tests for pipeline.available_sources() and pipeline.diagnose().

Covers two regressions caught during the 2026-05-07 silent-skip audit:

1. INCLUDE_SOURCES.split(",") used to drop entries with surrounding
   whitespace, so `INCLUDE_SOURCES=reddit, perplexity` silently parsed
   "perplexity" as " perplexity" and the source-availability check failed.

2. The diagnose() output didn't surface configured-but-missing-key gaps
   (e.g. perplexity in INCLUDE_SOURCES with no OPENROUTER_API_KEY, or
   reasoning_provider=auto resolving silently to LOCAL).

Run with: ``python -m unittest scripts/test_pipeline_diagnose.py``
or via pytest if available.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Make `lib` importable when run directly: skills/last30days/scripts is the
# canonical run-dir for this style of test (matches test_device_auth.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import pipeline  # noqa: E402


def _base_config(**overrides):
    """Minimal config with no keys set — every override is explicit."""
    config = {
        "OPENAI_AUTH_STATUS": "missing",
    }
    config.update(overrides)
    return config


class IncludeSourcesWhitespaceTest(unittest.TestCase):
    """Regression: INCLUDE_SOURCES split must strip whitespace per entry."""

    def test_perplexity_recognized_with_no_spaces(self):
        config = _base_config(
            OPENROUTER_API_KEY="dummy",
            INCLUDE_SOURCES="reddit,perplexity",
        )
        self.assertIn("perplexity", pipeline.available_sources(config))

    def test_perplexity_recognized_with_leading_space(self):
        # The bug we just fixed: ", perplexity" used to produce " perplexity".
        config = _base_config(
            OPENROUTER_API_KEY="dummy",
            INCLUDE_SOURCES="reddit, perplexity",
        )
        self.assertIn("perplexity", pipeline.available_sources(config))

    def test_perplexity_recognized_with_surrounding_spaces(self):
        config = _base_config(
            OPENROUTER_API_KEY="dummy",
            INCLUDE_SOURCES=" reddit , perplexity , tiktok ",
        )
        self.assertIn("perplexity", pipeline.available_sources(config))

    def test_perplexity_skipped_when_not_in_include_sources(self):
        config = _base_config(
            OPENROUTER_API_KEY="dummy",
            INCLUDE_SOURCES="reddit,tiktok",
        )
        self.assertNotIn("perplexity", pipeline.available_sources(config))

    def test_perplexity_skipped_when_no_key(self):
        config = _base_config(INCLUDE_SOURCES="reddit,perplexity")
        self.assertNotIn("perplexity", pipeline.available_sources(config))

    def test_uppercase_perplexity_recognized(self):
        # split-then-lower preserves case-insensitivity that pre-existed.
        config = _base_config(
            OPENROUTER_API_KEY="dummy",
            INCLUDE_SOURCES="Reddit,PERPLEXITY",
        )
        self.assertIn("perplexity", pipeline.available_sources(config))


class DiagnoseGapsTest(unittest.TestCase):
    """Regression: diagnose() must surface configured-but-missing-key gaps."""

    def test_gaps_field_exists(self):
        diag = pipeline.diagnose(_base_config())
        self.assertIn("gaps", diag)
        self.assertIsInstance(diag["gaps"], list)

    def test_optional_sources_field_exists(self):
        diag = pipeline.diagnose(_base_config())
        self.assertIn("optional_sources", diag)

    def test_auto_resolved_provider_field_exists(self):
        diag = pipeline.diagnose(_base_config())
        self.assertIn("auto_resolved_provider", diag)

    def test_auto_resolves_to_local_with_no_keys(self):
        diag = pipeline.diagnose(_base_config())
        self.assertEqual(diag["auto_resolved_provider"], "local")
        self.assertTrue(
            any("LOCAL" in g for g in diag["gaps"]),
            f"Expected LOCAL gap warning, got gaps={diag['gaps']}",
        )

    def test_auto_resolves_to_gemini_with_google_key(self):
        diag = pipeline.diagnose(_base_config(GOOGLE_API_KEY="dummy"))
        self.assertEqual(diag["auto_resolved_provider"], "gemini")

    def test_perplexity_key_without_include_sources_warns(self):
        diag = pipeline.diagnose(
            _base_config(OPENROUTER_API_KEY="dummy", INCLUDE_SOURCES="reddit")
        )
        self.assertTrue(
            any("perplexity" in g.lower() for g in diag["gaps"]),
            f"Expected perplexity-not-in-INCLUDE-SOURCES gap, got gaps={diag['gaps']}",
        )

    def test_whitespace_in_include_sources_warns(self):
        diag = pipeline.diagnose(
            _base_config(
                OPENROUTER_API_KEY="dummy",
                INCLUDE_SOURCES="reddit, perplexity",
            )
        )
        self.assertTrue(
            any("whitespace" in g.lower() for g in diag["gaps"]),
            f"Expected whitespace gap, got gaps={diag['gaps']}",
        )
        # And the source still resolves correctly thanks to the strip() fix.
        self.assertIn("perplexity", diag["available_sources"])

    def test_no_web_backend_warns(self):
        diag = pipeline.diagnose(_base_config(GOOGLE_API_KEY="dummy"))
        self.assertTrue(
            any("web search backend" in g.lower() for g in diag["gaps"]),
            f"Expected no-web-backend gap, got gaps={diag['gaps']}",
        )

    def test_brave_key_silences_web_backend_warning(self):
        diag = pipeline.diagnose(
            _base_config(GOOGLE_API_KEY="dummy", BRAVE_API_KEY="dummy")
        )
        self.assertFalse(
            any("web search backend" in g.lower() for g in diag["gaps"]),
            f"Did not expect no-web-backend gap, got gaps={diag['gaps']}",
        )
        self.assertEqual(diag["native_web_backend"], "brave")

    def test_optional_sources_lists_truthsocial_xquik(self):
        diag = pipeline.diagnose(_base_config())
        sources = {entry["source"] for entry in diag["optional_sources"]}
        self.assertIn("truthsocial", sources)
        self.assertIn("xquik", sources)


class XaiXErrorPathTest(unittest.TestCase):
    """Regression: xai_x.parse_x_response handling an error response used to
    raise AttributeError because xai_x.py:144 referenced http.DEBUG, which
    doesn't exist on lib.http. Fix: use log.is_debug() instead.

    See: work/2026-05-07/17-diagnose-enrichment-and-include-sources-fix-changeset.md
    """

    def test_error_response_does_not_raise(self):
        from lib import xai_x
        result = xai_x.parse_x_response({"error": {"message": "fake error"}})
        self.assertEqual(result, [])

    def test_error_response_with_debug_off_does_not_raise(self):
        # LAST30DAYS_DEBUG explicitly off — the gated branch must short-circuit.
        from lib import xai_x
        prior = os.environ.pop("LAST30DAYS_DEBUG", None)
        try:
            result = xai_x.parse_x_response({"error": "string-form error"})
            self.assertEqual(result, [])
        finally:
            if prior is not None:
                os.environ["LAST30DAYS_DEBUG"] = prior

    def test_error_response_with_debug_on_does_not_raise(self):
        # LAST30DAYS_DEBUG=1 — the gated branch executes the json.dumps()
        # path, but must still complete cleanly.
        from lib import xai_x
        prior = os.environ.get("LAST30DAYS_DEBUG")
        os.environ["LAST30DAYS_DEBUG"] = "1"
        try:
            result = xai_x.parse_x_response({"error": {"code": 500, "message": "x"}})
            self.assertEqual(result, [])
        finally:
            if prior is None:
                os.environ.pop("LAST30DAYS_DEBUG", None)
            else:
                os.environ["LAST30DAYS_DEBUG"] = prior


class LogIsDebugTest(unittest.TestCase):
    """log.is_debug() must reflect runtime os.environ, not a frozen import-time snapshot."""

    def test_is_debug_false_by_default(self):
        from lib import log
        prior = os.environ.pop("LAST30DAYS_DEBUG", None)
        try:
            self.assertFalse(log.is_debug())
        finally:
            if prior is not None:
                os.environ["LAST30DAYS_DEBUG"] = prior

    def test_is_debug_picks_up_runtime_set(self):
        from lib import log
        prior = os.environ.get("LAST30DAYS_DEBUG")
        os.environ["LAST30DAYS_DEBUG"] = "1"
        try:
            self.assertTrue(log.is_debug())
        finally:
            if prior is None:
                os.environ.pop("LAST30DAYS_DEBUG", None)
            else:
                os.environ["LAST30DAYS_DEBUG"] = prior

    def test_legacy_DEBUG_attribute_still_works(self):
        # Backwards-compat for any code that imports `log.DEBUG` directly.
        from lib import log
        prior = os.environ.get("LAST30DAYS_DEBUG")
        os.environ["LAST30DAYS_DEBUG"] = "1"
        try:
            self.assertTrue(log.DEBUG)
        finally:
            if prior is None:
                os.environ.pop("LAST30DAYS_DEBUG", None)
            else:
                os.environ["LAST30DAYS_DEBUG"] = prior


if __name__ == "__main__":
    unittest.main()
