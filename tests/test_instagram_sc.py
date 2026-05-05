"""Tests for instagram.py — ScrapeCreators Instagram search module."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "last30days" / "scripts"))

from lib import instagram
from lib.relevance import tokenize as _tokenize


class TestTokenize(unittest.TestCase):
    """Tests for tokenize() from relevance module."""

    def test_strips_stopwords(self):
        tokens = _tokenize("how to use the AI tools")
        self.assertNotIn("how", tokens)
        self.assertNotIn("the", tokens)
        self.assertNotIn("to", tokens)

    def test_expands_synonyms(self):
        tokens = _tokenize("ai tools")
        self.assertTrue("artificial" in tokens or "intelligence" in tokens)

    def test_removes_single_char(self):
        tokens = _tokenize("a b c python")
        self.assertNotIn("a", tokens)
        self.assertNotIn("b", tokens)
        self.assertIn("python", tokens)

    def test_lowercases(self):
        tokens = _tokenize("Python REACT")
        self.assertIn("python", tokens)
        self.assertIn("react", tokens)

    def test_strips_punctuation(self):
        tokens = _tokenize("hello, world!")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)


class TestComputeRelevance(unittest.TestCase):
    """Tests for _compute_relevance()."""

    def test_exact_match_high(self):
        rel = instagram._compute_relevance("claude code", "Claude Code tricks and tips")
        self.assertGreaterEqual(rel, 0.8)

    def test_partial_match_lower(self):
        rel = instagram._compute_relevance("claude code tips", "Best AI tools for coding")
        self.assertLess(rel, 0.5)

    def test_hashtag_boost(self):
        base = instagram._compute_relevance("claude code", "random video about stuff")
        boosted = instagram._compute_relevance("claude code", "random video about stuff", ["claudecode", "ai"])
        self.assertGreater(boosted, base)

    def test_no_match_returns_zero(self):
        rel = instagram._compute_relevance("quantum physics", "cat dancing video")
        self.assertEqual(rel, 0.0)

    def test_empty_query_returns_default(self):
        rel = instagram._compute_relevance("", "Some video title")
        self.assertEqual(rel, 0.5)


class TestInstagramDepthConfig(unittest.TestCase):
    """Tests for DEPTH_CONFIG."""

    def test_all_depths_exist(self):
        for depth in ("quick", "default", "deep"):
            self.assertIn(depth, instagram.DEPTH_CONFIG)

    def test_required_keys(self):
        for depth, config in instagram.DEPTH_CONFIG.items():
            self.assertIn("results_per_page", config)
            self.assertIn("max_captions", config)

    def test_deep_has_more_results(self):
        self.assertGreater(
            instagram.DEPTH_CONFIG["deep"]["results_per_page"],
            instagram.DEPTH_CONFIG["quick"]["results_per_page"],
        )


class TestHashtagFormCollapse(unittest.TestCase):
    """Tests for _to_hashtag_form() — the multi-word retry workaround."""

    def test_collapses_spaces(self):
        self.assertEqual(instagram._to_hashtag_form("toronto real estate"), "torontorealestate")

    def test_lowercases(self):
        self.assertEqual(instagram._to_hashtag_form("Toronto REAL Estate"), "torontorealestate")

    def test_idempotent_on_single_word(self):
        self.assertEqual(instagram._to_hashtag_form("ozempic"), "ozempic")

    def test_handles_extra_whitespace(self):
        self.assertEqual(instagram._to_hashtag_form("  toronto   real  estate  "), "torontorealestate")


class TestSearchRetryOn500(unittest.TestCase):
    """Tests for the multi-word -> hashtag retry on SC's flaky 500 path.

    SC's /v2/instagram/reels/search wraps Google Search and is documented
    to be unreliable on multi-token queries. The retry collapses to a
    hashtag form which hits the stable hashtag-page lookup path.
    """

    def _mock_response(self, status_code, json_payload=None):
        m = MagicMock()
        m.status_code = status_code
        m.json.return_value = json_payload or {}
        if status_code >= 400:
            m.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
        else:
            m.raise_for_status.return_value = None
        return m

    def test_multiword_500_triggers_retry_with_hashtag_form(self):
        """Multi-word query 500 -> retry with collapsed hashtag form."""
        first = self._mock_response(500)
        second = self._mock_response(200, {"reels": []})
        with patch.object(instagram, "_requests") as mock_requests:
            mock_requests.get.side_effect = [first, second]
            instagram.search_instagram(
                "toronto real estate", "2026-04-01", "2026-05-04",
                depth="default", token="fake-token",
            )
            self.assertEqual(mock_requests.get.call_count, 2)
            # First call: original multi-word query
            first_params = mock_requests.get.call_args_list[0].kwargs["params"]
            self.assertEqual(first_params["query"], "toronto real estate")
            # Second call: collapsed hashtag form
            second_params = mock_requests.get.call_args_list[1].kwargs["params"]
            self.assertEqual(second_params["query"], "torontorealestate")

    def test_singleword_500_does_not_retry(self):
        """Single-word query 500 has no spaces to collapse - no retry."""
        only = self._mock_response(500)
        with patch.object(instagram, "_requests") as mock_requests:
            mock_requests.get.return_value = only
            result = instagram.search_instagram(
                "ozempic", "2026-04-01", "2026-05-04",
                depth="default", token="fake-token",
            )
            self.assertEqual(mock_requests.get.call_count, 1)
            self.assertIn("error", result)
            self.assertEqual(result["items"], [])

    def test_first_call_succeeds_no_retry(self):
        """200 on first call -> retry path is never entered."""
        ok = self._mock_response(200, {"reels": []})
        with patch.object(instagram, "_requests") as mock_requests:
            mock_requests.get.return_value = ok
            instagram.search_instagram(
                "toronto real estate", "2026-04-01", "2026-05-04",
                depth="default", token="fake-token",
            )
            self.assertEqual(mock_requests.get.call_count, 1)

    def test_no_token_short_circuits(self):
        """No SCRAPECREATORS_API_KEY -> error returned without HTTP call."""
        with patch.object(instagram, "_requests") as mock_requests:
            result = instagram.search_instagram(
                "toronto real estate", "2026-04-01", "2026-05-04",
                depth="default", token=None,
            )
            mock_requests.get.assert_not_called()
            self.assertIn("error", result)
            self.assertIn("SCRAPECREATORS_API_KEY", result["error"])


class TestTranscriptTimeoutConfig(unittest.TestCase):
    """Tests for LAST30DAYS_TRANSCRIPT_TIMEOUT configuration.

    SC's /v2/instagram/media/transcript endpoint regularly takes >15s,
    so the timeout must be configurable. Default is DEFAULT_TRANSCRIPT_TIMEOUT
    (30s); the env var or per-call kwarg overrides it.
    """

    def setUp(self):
        # Snapshot any pre-existing env so we don't leak across tests
        self._saved_env = os.environ.pop("LAST30DAYS_TRANSCRIPT_TIMEOUT", None)

    def tearDown(self):
        os.environ.pop("LAST30DAYS_TRANSCRIPT_TIMEOUT", None)
        if self._saved_env is not None:
            os.environ["LAST30DAYS_TRANSCRIPT_TIMEOUT"] = self._saved_env

    def _ok_response(self):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"transcripts": [{"text": "hello world"}]}
        return m

    def _video_item(self, vid="abc123"):
        return {
            "video_id": vid,
            "url": f"https://www.instagram.com/reel/{vid}/",
            "text": "",
        }

    def test_default_timeout_is_30s_when_nothing_set(self):
        """No env var, no kwarg -> request uses 30s, not the legacy 15s."""
        items = [self._video_item()]
        with patch.object(instagram, "_requests") as mock_requests:
            mock_requests.get.return_value = self._ok_response()
            instagram.fetch_captions(items, token="fake-token")
            kwargs = mock_requests.get.call_args.kwargs
            self.assertEqual(kwargs["timeout"], 30.0)

    def test_env_var_override(self):
        """LAST30DAYS_TRANSCRIPT_TIMEOUT='60' -> request uses 60s."""
        os.environ["LAST30DAYS_TRANSCRIPT_TIMEOUT"] = "60"
        items = [self._video_item()]
        with patch.object(instagram, "_requests") as mock_requests:
            mock_requests.get.return_value = self._ok_response()
            instagram.fetch_captions(items, token="fake-token")
            kwargs = mock_requests.get.call_args.kwargs
            self.assertEqual(kwargs["timeout"], 60.0)

    def test_explicit_timeout_kwarg_wins_over_env(self):
        """Explicit timeout= kwarg trumps the env var."""
        os.environ["LAST30DAYS_TRANSCRIPT_TIMEOUT"] = "60"
        items = [self._video_item()]
        with patch.object(instagram, "_requests") as mock_requests:
            mock_requests.get.return_value = self._ok_response()
            instagram.fetch_captions(items, token="fake-token", timeout=10)
            kwargs = mock_requests.get.call_args.kwargs
            self.assertEqual(kwargs["timeout"], 10.0)

    def test_config_dict_fallback_when_env_unset(self):
        """config={'LAST30DAYS_TRANSCRIPT_TIMEOUT': '45'} -> request uses 45s."""
        items = [self._video_item()]
        with patch.object(instagram, "_requests") as mock_requests:
            mock_requests.get.return_value = self._ok_response()
            instagram.fetch_captions(
                items,
                token="fake-token",
                config={"LAST30DAYS_TRANSCRIPT_TIMEOUT": "45"},
            )
            kwargs = mock_requests.get.call_args.kwargs
            self.assertEqual(kwargs["timeout"], 45.0)

    def test_invalid_env_value_falls_back_to_default(self):
        """Garbage env var doesn't crash; falls back to 30s."""
        os.environ["LAST30DAYS_TRANSCRIPT_TIMEOUT"] = "not-a-number"
        items = [self._video_item()]
        with patch.object(instagram, "_requests") as mock_requests:
            mock_requests.get.return_value = self._ok_response()
            instagram.fetch_captions(items, token="fake-token")
            kwargs = mock_requests.get.call_args.kwargs
            self.assertEqual(kwargs["timeout"], 30.0)


class TestSearchRetryOn500Urllib(unittest.TestCase):
    """Lock in the urllib-path 500-retry. Pre-fix the retry was dead code on
    the urllib branch because it checked `getattr(e, 'status', None)` while
    `http.HTTPError` exposes the code as `status_code`. Caught by code-review
    on 2026-05-04 (REL-001 / ADV-001, two reviewers at 0.97/0.98 confidence).
    """

    def setUp(self):
        # Force urllib path by making instagram._requests look absent
        self._saved_requests = instagram._requests
        instagram._requests = None

    def tearDown(self):
        instagram._requests = self._saved_requests

    def test_urllib_500_on_multiword_triggers_retry_with_hashtag_form(self):
        from lib import http as http_module
        first_error = http_module.HTTPError("HTTP 500: Server Error", 500, "")
        second_payload = {"reels": []}
        # http.get is called twice: first raises HTTPError(500), second returns dict
        with patch.object(http_module, "get") as mock_http_get:
            mock_http_get.side_effect = [first_error, second_payload]
            instagram.search_instagram(
                "toronto real estate", "2026-04-01", "2026-05-04",
                depth="default", token="fake-token",
            )
            self.assertEqual(mock_http_get.call_count, 2)
            # First call URL contains the original multi-word query
            first_url = mock_http_get.call_args_list[0].args[0]
            self.assertIn("query=toronto+real+estate", first_url)
            # Second call URL contains the collapsed hashtag form
            second_url = mock_http_get.call_args_list[1].args[0]
            self.assertIn("query=torontorealestate", second_url)

    def test_urllib_500_on_singleword_does_not_retry(self):
        from lib import http as http_module
        only_error = http_module.HTTPError("HTTP 500: Server Error", 500, "")
        with patch.object(http_module, "get") as mock_http_get:
            mock_http_get.side_effect = only_error
            result = instagram.search_instagram(
                "ozempic", "2026-04-01", "2026-05-04",
                depth="default", token="fake-token",
            )
            self.assertEqual(mock_http_get.call_count, 1)
            self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
