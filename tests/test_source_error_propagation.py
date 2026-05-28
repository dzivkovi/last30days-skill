"""End-to-end regression tests for source-module error propagation.

Source modules catch ``HTTPError`` inline instead of re-raising. Without the
pipeline reading their ``error`` envelope, ``bundle.errors_by_source`` stayed
empty and the renderer's ``## Source Errors`` block had nothing to render --
silent failure that looked identical to "0 items, API healthy."

These tests pin the contract:
    source_module returns {"items": [], "error": "..."} envelope
      -> pipeline._swallowed_error_artifact extracts {"error": "..."}
        -> post-future routing populates bundle.errors_by_source[source]
          -> renderer's ## Source Errors block surfaces it

The AST-based ``SourceErrorEnvelopeConventionTests`` enforces the AGENTS.md
rule across every ``lib/*.py`` source-entry-point function so a future
contributor cannot reintroduce the silent-swallow bug class.
"""

import ast
import pathlib
import unittest
from unittest.mock import patch

from lib import http, perplexity, pipeline, render, schema


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "skills" / "last30days" / "scripts" / "lib"


class SwallowedErrorArtifactTests(unittest.TestCase):
    """Unit tests for the pipeline-side normalization helper."""

    def test_dict_with_error_returns_envelope(self):
        result = {"items": [], "error": "HTTPError: HTTP 400: Bad Request"}
        self.assertEqual(
            pipeline._swallowed_error_artifact(result),
            {"error": "HTTPError: HTTP 400: Bad Request"},
        )

    def test_dict_without_error_returns_empty(self):
        self.assertEqual(pipeline._swallowed_error_artifact({"items": [{}]}), {})

    def test_dict_with_none_error_returns_empty(self):
        self.assertEqual(
            pipeline._swallowed_error_artifact({"items": [], "error": None}),
            {},
        )

    def test_dict_with_empty_string_error_returns_empty(self):
        self.assertEqual(
            pipeline._swallowed_error_artifact({"items": [], "error": ""}),
            {},
        )

    def test_none_returns_empty(self):
        self.assertEqual(pipeline._swallowed_error_artifact(None), {})

    def test_non_dict_returns_empty(self):
        self.assertEqual(pipeline._swallowed_error_artifact([]), {})
        self.assertEqual(pipeline._swallowed_error_artifact("string"), {})

    def test_non_string_error_is_coerced_to_str(self):
        """A source emitting a non-string error must not break the str-typed
        ``bundle.errors_by_source[source]`` contract."""
        result = {"items": [], "error": RuntimeError("boom")}
        envelope = pipeline._swallowed_error_artifact(result)
        self.assertIn("error", envelope)
        self.assertIsInstance(envelope["error"], str)
        self.assertIn("boom", envelope["error"])

    def test_helper_does_not_mutate_input(self):
        """Helper must not mutate its input -- pipeline pops on the artifact
        copy at the routing site, not the source's returned envelope."""
        result = {"items": [], "error": "HTTPError: HTTP 400"}
        snapshot = dict(result)
        pipeline._swallowed_error_artifact(result)
        self.assertEqual(result, snapshot)


class PerplexityErrorEnvelopeTests(unittest.TestCase):
    """U1: perplexity.search must include 'error' in artifact on failure."""

    def test_http_401_includes_error_envelope(self):
        err = http.HTTPError("HTTP 401 Unauthorized", status_code=401)
        with patch.object(perplexity.http, "post", side_effect=err) as mock_post:
            items, artifact = perplexity.search(
                "test query", ("2026-04-28", "2026-05-28"), {"OPENROUTER_API_KEY": "fake-key"}
            )
        mock_post.assert_called_once()
        self.assertEqual(items, [])
        self.assertIn("error", artifact)
        self.assertTrue(artifact["error"].startswith("HTTPError:"))
        self.assertIn("401", artifact["error"])

    def test_http_429_includes_error_envelope(self):
        err = http.HTTPError("HTTP 429 Too Many Requests", status_code=429)
        with patch.object(perplexity.http, "post", side_effect=err) as mock_post:
            items, artifact = perplexity.search(
                "test query", ("2026-04-28", "2026-05-28"), {"OPENROUTER_API_KEY": "fake-key"}
            )
        mock_post.assert_called_once()
        self.assertEqual(items, [])
        self.assertIn("error", artifact)
        self.assertTrue(artifact["error"].startswith("HTTPError:"))
        self.assertIn("429", artifact["error"])

    def test_generic_exception_includes_error_envelope(self):
        with patch.object(perplexity.http, "post", side_effect=ConnectionError("network down")) as mock_post:
            items, artifact = perplexity.search(
                "test query", ("2026-04-28", "2026-05-28"), {"OPENROUTER_API_KEY": "fake-key"}
            )
        mock_post.assert_called_once()
        self.assertEqual(items, [])
        self.assertIn("error", artifact)
        self.assertIn("ConnectionError", artifact["error"])
        self.assertIn("network down", artifact["error"])

    def test_no_api_key_returns_no_error_envelope(self):
        """No API key = source-not-configured, NOT a failure. No error key."""
        items, artifact = perplexity.search(
            "test query", ("2026-04-28", "2026-05-28"), {}
        )
        self.assertEqual(items, [])
        self.assertNotIn("error", artifact)


class SourceErrorsRenderingTests(unittest.TestCase):
    """U5: populated errors_by_source must surface in rendered output."""

    def _minimal_report(self, errors_by_source: dict[str, str]) -> schema.Report:
        return schema.Report(
            topic="test topic",
            range_from="2026-04-28",
            range_to="2026-05-28",
            generated_at="2026-05-28T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="gemini",
                planner_model="gemini-3.1-flash-lite",
                rerank_model="gemini-3.1-flash-lite",
            ),
            query_plan=schema.QueryPlan(
                intent="news",
                freshness_mode="balanced_recent",
                cluster_mode="story",
                raw_topic="test topic",
                subqueries=[schema.SubQuery(
                    label="primary",
                    search_query="test topic",
                    ranking_query="What happened with test topic?",
                    sources=["grounding"],
                )],
                source_weights={"grounding": 1.0},
            ),
            clusters=[],
            ranked_candidates=[],
            items_by_source={},
            errors_by_source=errors_by_source,
        )

    def test_threads_http_400_surfaces_in_source_errors_block(self):
        """A simulated Threads HTTP 400 -- the failure mode that motivated this fix."""
        report = self._minimal_report(
            {"threads": "HTTPError: HTTP 400: Bad Request"}
        )
        text = render.render_compact(report)
        self.assertIn("## Source Errors", text)
        self.assertIn("- Threads: HTTPError: HTTP 400: Bad Request", text)

    def test_perplexity_401_surfaces_in_source_errors_block(self):
        report = self._minimal_report(
            {"perplexity": "HTTPError: Invalid OpenRouter API key (401)"}
        )
        text = render.render_compact(report)
        self.assertIn("## Source Errors", text)
        self.assertIn("- Perplexity: HTTPError: Invalid OpenRouter API key (401)", text)

    def test_bluesky_credentials_missing_surfaces_in_source_errors_block(self):
        report = self._minimal_report(
            {"bluesky": "Bluesky credentials not configured"}
        )
        text = render.render_compact(report)
        self.assertIn("## Source Errors", text)
        self.assertIn("- Bluesky: Bluesky credentials not configured", text)

    def test_multiple_errors_render_in_alphabetical_order(self):
        report = self._minimal_report({
            "threads": "HTTPError: HTTP 400: Bad Request",
            "perplexity": "HTTPError: Invalid OpenRouter API key (401)",
            "bluesky": "Bluesky credentials not configured",
        })
        text = render.render_compact(report)
        # Anchor on full per-source bullet lines so a future summary line that
        # mentions error substrings doesn't shift these indexes.
        bluesky_line = "- Bluesky: Bluesky credentials not configured"
        perplexity_line = "- Perplexity: HTTPError: Invalid OpenRouter API key (401)"
        threads_line = "- Threads: HTTPError: HTTP 400: Bad Request"
        self.assertIn(bluesky_line, text)
        self.assertIn(perplexity_line, text)
        self.assertIn(threads_line, text)
        self.assertLess(text.index(bluesky_line), text.index(perplexity_line))
        self.assertLess(text.index(perplexity_line), text.index(threads_line))

    def test_no_errors_omits_source_errors_section(self):
        """Sanity: empty errors_by_source must NOT render a stray header."""
        report = self._minimal_report({})
        text = render.render_compact(report)
        self.assertNotIn("## Source Errors", text)


# ---------------------------------------------------------------------------
# Convention enforcement: AST scan over lib/*.py for missing error envelopes.
# ---------------------------------------------------------------------------


def _qualified_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _catches_httperror(handler: ast.ExceptHandler) -> bool:
    """True iff the except clause names HTTPError (with or without module
    qualifier, e.g. ``http.HTTPError`` / ``urllib.error.HTTPError``).

    Narrower than catching ``Exception`` -- broad exception handlers in inner
    loops (xquik, reddit_public) intentionally log-and-fall-through and are
    out of scope for the envelope convention this test enforces."""
    if handler.type is None:
        return False
    candidates = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for cand in candidates:
        name = _qualified_name(cand)
        if name and name.split(".")[-1] == "HTTPError":
            return True
    return False


def _toplevel_handlers(fn: ast.FunctionDef) -> list[ast.ExceptHandler]:
    """Collect except handlers in ``fn`` but stop at nested function defs.

    Nested helpers (e.g. ``_auth_and_search`` inside ``search_bluesky``)
    return error info via tuple to the outer scope, which then wraps into the
    envelope -- their inner handlers are not the contract boundary."""
    handlers: list[ast.ExceptHandler] = []

    def _walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue  # don't descend into nested functions
            if isinstance(child, ast.ExceptHandler):
                handlers.append(child)
            _walk(child)

    _walk(fn)
    return handlers


def _returns_envelope(handler: ast.ExceptHandler) -> bool:
    """The except body re-raises OR every Return inside it carries an
    ``"error"`` key. Log-and-fall-through (no Return in the handler body) is
    out of scope -- callers that return successfully with partial results are
    a different pattern this convention doesn't address."""
    returns: list[ast.Return] = []
    has_raise = False
    for child in ast.walk(handler):
        if isinstance(child, ast.Raise):
            has_raise = True
        elif isinstance(child, ast.Return):
            returns.append(child)
    if has_raise and not returns:
        return True
    if not returns:
        return True  # log-and-fall-through: out of scope, not a violation
    return all(_value_mentions_error_key(r.value) for r in returns)


def _value_mentions_error_key(value: ast.AST | None) -> bool:
    if value is None:
        return False
    # A Call (e.g., return _build_envelope(e)) gets a pass -- AST can't inspect
    # cross-function helpers and would otherwise force every helper call to
    # inline its dict literal at the call site.
    if isinstance(value, ast.Call):
        return True
    for child in ast.walk(value):
        if isinstance(child, ast.Constant) and child.value == "error":
            return True
    return False


def _iter_source_modules() -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for path in LIB_DIR.rglob("*.py"):
        if "vendor" in path.parts:
            continue
        if path.name == "__init__.py":
            continue
        paths.append(path)
    return sorted(paths)


def _toplevel_search_functions(tree: ast.Module) -> list[ast.FunctionDef]:
    """Functions named ``search_*`` defined at module top level (not nested,
    not enrichment helpers like ``fetch_captions``).

    ``search_*`` is the pipeline's dispatch contract -- ``_retrieve_stream``
    calls these; their returns flow into ``_swallowed_error_artifact`` and
    on to ``bundle.errors_by_source``. ``fetch_*`` enrichment helpers (e.g.
    ``fetch_captions``, ``fetch_post_comments``) operate on already-retrieved
    items and never reach the envelope routing site."""
    return [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("search_")
    ]


class SourceErrorEnvelopeConventionTests(unittest.TestCase):
    """Codebase-level enforcement of the AGENTS.md error-envelope contract."""

    def test_search_entry_points_catching_httperror_return_envelope(self):
        """Every top-level ``search_*`` in lib/*.py whose body catches
        ``HTTPError`` and RETURNS from inside that handler must return a value
        carrying the ``"error"`` key so the pipeline can route the failure
        into ``bundle.errors_by_source``. See AGENTS.md "error envelope"
        convention."""
        violations: list[str] = []
        for path in _iter_source_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for fn in _toplevel_search_functions(tree):
                for handler in _toplevel_handlers(fn):
                    if not _catches_httperror(handler):
                        continue
                    if not _returns_envelope(handler):
                        violations.append(
                            f"{path.relative_to(REPO_ROOT)}:{handler.lineno} "
                            f"(in {fn.name})"
                        )
        if violations:
            self.fail(
                "Search-entry-point HTTPError handlers must return a value "
                "carrying the 'error' key (or re-raise) so the pipeline can "
                "route the failure via _swallowed_error_artifact into "
                "bundle.errors_by_source. See AGENTS.md. Violations:\n  - "
                + "\n  - ".join(violations)
            )


if __name__ == "__main__":
    unittest.main()
