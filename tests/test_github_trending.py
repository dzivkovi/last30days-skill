"""GitHub trending lane (lib/github_trending.py): risers from github.com/trending
and new-in-topic repositories from /search/repositories, two labeled halves.
Fixture-driven; no test touches the network."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from lib import github, github_trending as gt, normalize, pipeline, signals

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "github"
TRENDING = (FIXTURES / "trending_weekly.html").read_text(encoding="utf-8")
SEARCH = json.loads((FIXTURES / "search_repositories.json").read_text(encoding="utf-8"))


@pytest.fixture
def offline(monkeypatch):
    """Serve the fixtures for both halves and forbid anything else."""
    monkeypatch.setattr(gt.http, "get_text", lambda url, **kw: TRENDING if url.startswith(gt.TRENDING_URL) else None)
    monkeypatch.setattr(github, "_fetch_json", lambda url, token=None, timeout=15, failure_out=None: SEARCH)


# ---- risers ----


def test_parse_trending_page_maps_rank_repo_counts_and_language():
    rows = gt.parse_trending_page(TRENDING)

    assert [r["repo"] for r in rows] == ["example/agent-memory", "someone/kitchen-timer", "labs/agentic-marketing-kit"]
    first = rows[0]
    assert first["description"].startswith("Long-term memory for AI agents")
    assert first["language"] == "Python" and first["stars"] == 12_340 and first["forks"] == 890 and first["stars_week"] == 4_210
    assert rows[2]["language"] == "" and rows[2]["forks"] == 0 and rows[2]["stars_week"] == 311


def test_risers_are_filtered_by_topic_unless_the_question_is_global():
    fetch = lambda url: TRENDING  # noqa: E731

    scoped = gt.fetch_risers("AI agent memory", limit=10, observed_at="2026-09-09", fetch_text=fetch)
    assert [i["title"] for i in scoped] == ["example/agent-memory", "labs/agentic-marketing-kit"]
    assert all(i["metadata"]["half"] == "risers" and i["date"] == "2026-09-09" for i in scoped)

    everything = gt.fetch_risers("trending this week", limit=10, fetch_text=fetch)
    assert len(everything) == 3 and everything[1]["title"] == "someone/kitchen-timer"


def test_risers_unreachable_or_drifted_page_is_reported():
    errors: list[str] = []
    assert gt.fetch_risers("agents", limit=5, fetch_text=lambda url: None, errors=errors) == []
    assert gt.fetch_risers("agents", limit=5, fetch_text=lambda url: "<html><body>nothing</body></html>", errors=errors) == []
    assert errors == ["trending page unreachable", "trending page markup not recognized"]


# ---- new in topic ----


def test_new_repos_query_uses_created_since_and_never_pushed(monkeypatch):
    seen: list[str] = []

    def fake_fetch(url, token=None, timeout=15, failure_out=None):
        seen.append(url)
        return SEARCH

    monkeypatch.setattr(github, "_fetch_json", fake_fetch)

    items = gt.fetch_new_repos("AI agent memory is:issue", "2026-08-10", limit=20)

    assert "created%3A%3E%3D2026-08-10" in seen[0] and "pushed" not in seen[0] and "is%3Aissue" not in seen[0]
    assert "sort=stars" in seen[0]
    assert [i["title"] for i in items] == ["newco/agent-memory-server", "example/agent-memory"]  # 3-star repo under the floor
    assert items[0]["date"] == "2026-08-17" and items[0]["engagement"] == {"stars": 485, "forks": 31, "stars_week": 0}
    assert items[0]["metadata"]["grounding_exempt"] is True and items[0]["metadata"]["half"] == "new"


def test_new_repos_skipped_for_a_global_question(monkeypatch):
    monkeypatch.setattr(github, "_fetch_json", lambda *a, **k: pytest.fail("no search for a global question"))

    assert gt.fetch_new_repos("trending", "2026-08-10", limit=20) == []


# ---- combined search ----


def test_search_merges_halves_and_marks_repos_seen_in_both(offline):
    result = gt.search_github_trending("AI agent memory", "2026-08-10", "2026-09-09", depth="default")

    by_title = {i["title"]: i for i in result["items"]}
    assert "error" not in result and "warning" not in result
    assert by_title["example/agent-memory"]["metadata"]["half"] == "both"
    assert by_title["example/agent-memory"]["engagement"]["stars_week"] == 4_210
    assert by_title["example/agent-memory"]["date"] == "2026-08-20"  # creation date beats the observation stamp
    assert by_title["labs/agentic-marketing-kit"]["date"] == "2026-09-09"
    assert by_title["newco/agent-memory-server"]["metadata"]["half"] == "new"
    assert by_title["labs/agentic-marketing-kit"]["metadata"]["half"] == "risers"
    assert "kitchen-timer" not in " ".join(by_title)


def test_search_reports_error_only_when_both_halves_fail(monkeypatch):
    monkeypatch.setattr(gt.http, "get_text", lambda url, **kw: None)
    monkeypatch.setattr(github, "_fetch_json", lambda url, token=None, timeout=15, failure_out=None: (failure_out.append("HTTP 403: rate limited") if failure_out is not None else None))

    both_fail = gt.search_github_trending("AI agent memory", "2026-08-10", "2026-09-09")
    assert both_fail["items"] == [] and "unreachable" in both_fail["error"] and "403" in both_fail["error"]

    monkeypatch.setattr(gt.http, "get_text", lambda url, **kw: TRENDING)
    one_fails = gt.search_github_trending("AI agent memory", "2026-08-10", "2026-09-09")
    assert one_fails["items"] and "error" not in one_fails and "403" in one_fails["warning"]


# ---- wiring ----


def test_normalize_carries_half_engagement_and_dates(offline):
    result = gt.search_github_trending("AI agent memory", "2026-08-10", "2026-09-09")

    items = normalize.normalize_source_items("github_trending", gt.parse_github_trending_response(result), "2026-08-10", "2026-09-09")

    by_title = {i.title: i for i in items}
    riser = by_title["labs/agentic-marketing-kit"]
    assert riser.source == "github_trending" and riser.published_at == "2026-09-09" and riser.date_confidence == "low"
    assert riser.engagement["stars_week"] == 311 and "rising" in (riser.container or "")
    new = by_title["newco/agent-memory-server"]
    assert new.published_at == "2026-08-17" and new.date_confidence == "high" and new.metadata["grounding_exempt"] is True
    assert "memory" in new.body and new.author == "newco"


def test_signals_know_the_source():
    assert signals.source_quality("github_trending") == 0.8
    assert [name for name, _ in signals.ENGAGEMENT_WEIGHTS["github_trending"]] == ["stars_week", "stars", "forks"]


def test_source_is_opt_in_only():
    assert "github_trending" not in pipeline.available_sources({}, local_only=True)
    assert "github_trending" in pipeline.available_sources({"INCLUDE_SOURCES": "github_trending"}, local_only=True)
    assert "github_trending" in pipeline.available_sources({}, ["github", "github_trending"], local_only=True)
    assert pipeline.MAX_SOURCE_FETCHES["github_trending"] == 1


def test_pipeline_dispatch_returns_items_and_outcome(offline):
    items, artifact = pipeline._retrieve_stream_impl(
        topic="AI agent memory",
        subquery=mock.Mock(search_query="AI agent memory", label="main", ranking_query="AI agent memory"),
        source="github_trending",
        config={},
        depth="quick",
        date_range=("2026-08-10", "2026-09-09"),
        runtime=mock.Mock(),
        mock=False,
    )

    assert {i["title"] for i in items} >= {"example/agent-memory", "newco/agent-memory-server"}
    assert artifact == {}
