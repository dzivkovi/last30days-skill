# ruff: noqa: E402
"""Tests for the three silent-failure warnings in planner._sanitize_plan.

Regression coverage for the 2026-05-09 / 2026-05-10 silent-fallback bugs:
1. Invalid `intent` strings get silently reclassified to `_infer_intent(topic)`
2. Subquery counts above the per-intent cap get silently truncated
3. Subqueries missing required `ranking_query` field cause every subquery to be
   skipped, triggering `_fallback_plan()` that DISCARDS the user's
   intent/freshness_mode/cluster_mode choices entirely

All three paths now emit `[Planner] WARNING:` lines on stderr. These tests assert
both the behavior (reclassification / truncation / fallback) AND the warning text.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "skills" / "last30days" / "scripts"))

from lib import planner


def _sanitize(raw: dict, topic: str = "Toronto real estate"):
    err = io.StringIO()
    with redirect_stderr(err):
        plan = planner._sanitize_plan(
            raw,
            topic=topic,
            available_sources=["reddit", "x", "youtube", "tiktok", "instagram"],
            requested_sources=None,
            depth="standard",
        )
    return plan, err.getvalue()


def _valid_subquery(label: str, query: str) -> dict:
    return {
        "label": label,
        "search_query": query,
        "ranking_query": f"What is happening with {query}?",
        "weight": 1.0,
    }


class InvalidIntentReclassificationTests(unittest.TestCase):
    """WARNING 1: invalid intent string → silent reclassification (now warned)."""

    def test_invalid_intent_emits_warning(self):
        plan, stderr = _sanitize({
            "intent": "news",
            "subqueries": [_valid_subquery("primary", "Toronto real estate")],
        })
        self.assertIn("WARNING:", stderr)
        self.assertIn("intent='news' not in ALLOWED_INTENTS", stderr)
        self.assertNotEqual(plan.intent, "news")

    def test_invalid_intent_reclassifies_via_infer(self):
        plan, _ = _sanitize({
            "intent": "trending",
            "subqueries": [_valid_subquery("primary", "Toronto real estate")],
        })
        self.assertIn(plan.intent, planner.ALLOWED_INTENTS)

    def test_omitted_intent_does_not_warn(self):
        """Missing intent is normal (defaults to inferred); only INVALID strings warn."""
        _, stderr = _sanitize({
            "subqueries": [_valid_subquery("primary", "Toronto real estate")],
        })
        self.assertNotIn("WARNING: intent=", stderr)


class ExcessSubqueriesTruncationTests(unittest.TestCase):
    """WARNING 2: too many subqueries for the intent's cap → silent truncation (now warned)."""

    def test_excess_subqueries_emits_warning(self):
        plan, stderr = _sanitize({
            "intent": "concept",
            "subqueries": [_valid_subquery(f"q{i}", f"foo{i}") for i in range(5)],
        })
        self.assertIn("WARNING:", stderr)
        self.assertIn("5 subqueries", stderr)
        self.assertIn("capping to 2", stderr)

    def test_excess_subqueries_truncates_to_cap(self):
        plan, _ = _sanitize({
            "intent": "concept",
            "subqueries": [_valid_subquery(f"q{i}", f"foo{i}") for i in range(5)],
        })
        self.assertEqual(len(plan.subqueries), 2)

    def test_at_cap_does_not_warn(self):
        _, stderr = _sanitize({
            "intent": "concept",
            "subqueries": [_valid_subquery(f"q{i}", f"foo{i}") for i in range(2)],
        })
        self.assertNotIn("capping to", stderr)


class FullPlanFallbackTests(unittest.TestCase):
    """WARNING 3: subqueries fail validation → _fallback_plan() discards user fields (now warned).

    This is the 2026-05-10 Toronto real estate bug: a plan with `intent="breaking_news"`,
    `freshness_mode="balanced_recent"`, `cluster_mode="story"`, 5 subqueries (each missing
    `ranking_query`) was silently demoted to `intent=concept, freshness=evergreen_ok,
    cluster=none, subqueries=1`. Quantitative impact: 43% item drop, 87% TRREB news drop.
    """

    def test_missing_ranking_query_emits_warning(self):
        # Reproduce the exact 2026-05-10 plan shape (no ranking_query)
        plan_without_ranking = {
            "intent": "breaking_news",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "story",
            "subqueries": [
                {"label": "primary", "search_query": "Toronto real estate", "weight": 1.0},
                {"label": "condo", "search_query": "Toronto condo market", "weight": 0.9},
            ],
        }
        plan, stderr = _sanitize(plan_without_ranking)
        self.assertIn("WARNING:", stderr)
        self.assertIn("ALL were dropped during validation", stderr)
        self.assertIn("ranking_query", stderr)
        self.assertIn("DISCARDED", stderr)

    def test_fallback_discards_user_intent_freshness_cluster(self):
        """The dangerous part of the bug: valid intent/freshness/cluster are LOST."""
        plan, _ = _sanitize({
            "intent": "breaking_news",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "story",
            "subqueries": [
                {"label": "p", "search_query": "x"},  # missing ranking_query
            ],
        })
        # The bug: user said breaking_news + balanced_recent + story, all discarded.
        self.assertNotEqual(plan.freshness_mode, "balanced_recent")
        self.assertNotEqual(plan.cluster_mode, "story")

    def test_empty_subqueries_does_not_warn(self):
        """A plan with NO subqueries falls back without warning (nothing to discard)."""
        _, stderr = _sanitize({"intent": "concept", "subqueries": []})
        self.assertNotIn("ALL were dropped", stderr)


class HappyPathSilentTests(unittest.TestCase):
    """A valid plan must produce zero warnings on stderr."""

    def test_valid_plan_emits_no_warnings(self):
        plan, stderr = _sanitize({
            "intent": "breaking_news",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "story",
            "subqueries": [
                _valid_subquery("primary", "Toronto real estate"),
                _valid_subquery("condo", "Toronto condo market"),
            ],
        })
        self.assertNotIn("WARNING:", stderr)
        self.assertEqual(plan.intent, "breaking_news")
        self.assertEqual(plan.freshness_mode, "balanced_recent")
        self.assertEqual(plan.cluster_mode, "story")
        self.assertEqual(len(plan.subqueries), 2)


if __name__ == "__main__":
    unittest.main()
