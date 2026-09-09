"""Review-driven hardening of the GitHub trending lane. Each test pins one finding
from the PR #27 review: the "both" merge keeps the real creation date, non-ASCII
and question-shaped topics are handled, partial markup drift is refused, a
warning reaches the outcome artifact, the planner keeps the source, and the
parser survives nested svg, a truncated page, and the risers cap."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from lib import github, github_trending as gt, normalize, pipeline, planner, schema

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "github"
TRENDING = (FIXTURES / "trending_weekly.html").read_text(encoding="utf-8")
SEARCH = json.loads((FIXTURES / "search_repositories.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _no_gh_subprocess(monkeypatch):
    monkeypatch.setattr(github, "resolve_token", lambda token=None: token)


def _search_json(monkeypatch, payload=SEARCH, failures=None):
    def fake(url, token=None, timeout=15, failure_out=None):
        if failures and failure_out is not None:
            failure_out.extend(failures)
            return None
        return payload

    monkeypatch.setattr(github, "_fetch_json", fake)


# ---- topic handling ----


@pytest.mark.parametrize("query", ["what is trending", "what's trending on GitHub this week", "top repos", ""])
def test_question_shaped_topics_are_global(query):
    assert gt.is_global_trending_query(query)
    assert gt._topic_terms(query) == ""


@pytest.mark.parametrize("query,terms", [("机器学习 评测", "机器学习 评测"), ("what's new on GitHub for agents", "agents"), ("AI agent memory user:evil", "ai agent memory")])
def test_non_ascii_and_question_topics_keep_their_subject(query, terms):
    assert not gt.is_global_trending_query(query)
    assert gt._topic_terms(query) == terms


def test_non_ascii_topic_filters_risers_instead_of_flooding():
    kept = gt.fetch_risers("机器学习 评测", limit=10, fetch_text=lambda url: TRENDING)

    assert kept == []  # nothing on the page matches; the page is not dumped in as "global"


# ---- risers ----


def test_nested_svg_and_sponsor_markup_still_parse():
    rows = gt.parse_trending_page(TRENDING)

    assert "<svg" in TRENDING and "<path" in TRENDING
    assert [r["stars_week"] for r in rows] == [4_210, 980, 311] and rows[0]["stars"] == 12_340


def test_truncated_page_still_yields_the_last_row():
    cut = TRENDING[: TRENDING.rfind("</article>")]

    assert [r["repo"] for r in gt.parse_trending_page(cut)][-1] == "labs/agentic-marketing-kit"


def test_risers_limit_keeps_the_highest_ranked_match():
    kept = gt.fetch_risers("AI agent memory", limit=1, fetch_text=lambda url: TRENDING)

    assert [i["title"] for i in kept] == ["example/agent-memory"]


def test_partial_markup_drift_is_refused():
    drifted = TRENDING.replace("float-sm-right", "float-sm-left")
    errors: list[str] = []

    assert gt.fetch_risers("agents", limit=5, fetch_text=lambda url: drifted, errors=errors) == []
    assert errors == ["trending page markup drifted: weekly star delta missing"]


def test_daily_page_labels_read_correctly():
    item = gt._item({"repo": "a/b", "description": "", "language": "", "stars": 1, "forks": 0, "stars_week": 5}, half="risers", rank=0, relevance=1.0, since="daily", observed_at="2026-09-09")

    assert item["container"] == "GitHub trending: rising today" and item["why_relevant"].startswith("5 stars today")


def test_hostile_repo_hrefs_are_dropped():
    page = TRENDING.replace('href="/someone/kitchen-timer"', 'href="https://evil.example/x"')

    assert "someone/kitchen-timer" not in [r["repo"] for r in gt.parse_trending_page(page)]
    assert all("/" in r["repo"] and not r["repo"].startswith("http") for r in gt.parse_trending_page(page))


# ---- new half ----


def test_new_half_uses_inclusive_created_since_and_a_star_floor(monkeypatch):
    seen: list[str] = []

    def fake(url, token=None, timeout=15, failure_out=None):
        seen.append(url)
        return SEARCH

    monkeypatch.setattr(github, "_fetch_json", fake)

    items = gt.fetch_new_repos("AI agent memory", "2026-08-10", limit=20)

    assert "created%3A%3E%3D2026-08-10" in seen[0]
    assert [i["title"] for i in items] == ["newco/agent-memory-server", "example/agent-memory"]  # 3-star repo dropped


# ---- merge and dates ----


def test_both_merge_keeps_the_creation_date_and_high_confidence(monkeypatch):
    monkeypatch.setattr(gt.http, "get_text", lambda url, **kw: TRENDING)
    _search_json(monkeypatch)

    result = gt.search_github_trending("AI agent memory", "2026-08-10", "2026-09-09")
    items = normalize.normalize_source_items("github_trending", gt.parse_github_trending_response(result), "2026-08-10", "2026-09-09")

    by_title = {i.title: i for i in items}
    both = by_title["example/agent-memory"]
    assert both.metadata["half"] == "both" and both.published_at == "2026-08-20" and both.date_confidence == "high"
    assert both.metadata["observed_at"] == "2026-09-09" and both.engagement["stars_week"] == 4_210
    riser = by_title["labs/agentic-marketing-kit"]
    assert riser.published_at == "2026-09-09" and riser.date_confidence == "low"


# ---- pipeline and planner ----


def test_warning_from_one_half_becomes_a_partial_outcome(monkeypatch):
    monkeypatch.setattr(gt.http, "get_text", lambda url, **kw: TRENDING)
    _search_json(monkeypatch, failures=["HTTP 403: rate limited or forbidden"])

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

    assert items and artifact["_source_outcome"]["state"] == schema.PARTIAL
    assert "403" in artifact["_source_outcome"]["detail"]


def test_depth_caps_fit_the_per_stream_limit():
    for depth, (risers, new) in gt.DEPTH_CONFIG.items():
        assert risers + new <= pipeline.DEPTH_SETTINGS[depth]["per_stream_limit"]


def test_planner_keeps_the_source_for_comparison_intents():
    assert "github_trending" in planner.SOURCE_CAPABILITIES
    assert "github_trending" in planner._default_sources_for_intent("comparison", ["github", "github_trending"])
