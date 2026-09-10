"""Issue #25: a bridged corpus item and the same public article corroborate each
other in ranking without merging, so private text never crosses the fence."""

from __future__ import annotations

from lib import fusion, health, render, schema

SECRET = "PRIVATE-CORPUS-SENTINEL"
ARTICLE = "https://www.example.com/news/agent-memory?utm_source=feed"


def _web(item_id: str, url: str, title: str, relevance: float = 0.7, source: str = "web") -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=title,
        url=url,
        published_at="2026-09-01",
        relevance_hint=relevance,
        snippet=f"public snippet for {title}",
        metadata={"local_relevance": relevance, "freshness": 80, "engagement_score": 5, "source_quality": 0.7},
    )


def _corpus(canonical: str | None = ARTICLE, item_id: str = "C1") -> schema.SourceItem:
    metadata = {"relative_path": "feeds/travel/agent-memory.md", "local_only": True, "local_relevance": 0.9, "freshness": 80, "source_quality": 0.75}
    if canonical:
        metadata["canonical_url"] = canonical
    return schema.SourceItem(
        item_id=item_id,
        source="corpus",
        title="agent memory (bridged)",
        body=f"bridged body {SECRET}",
        url=f"corpus://{item_id}",
        published_at="2026-09-01",
        relevance_hint=0.9,
        snippet=f"bridged snippet {SECRET}",
        metadata=metadata,
    )


def _plan(sources: list[str], labels: tuple[str, ...] = ("primary",)) -> schema.QueryPlan:
    return schema.QueryPlan(
        intent="concept",
        freshness_mode="balanced_recent",
        cluster_mode="none",
        raw_topic="agent memory",
        subqueries=[schema.SubQuery(label, "agent memory", "agent memory", sources) for label in labels],
        source_weights={source: 1.0 for source in sources},
    )


def _fuse(streams, plan=None):
    plan = plan or _plan(["web", "corpus"])
    return fusion.weighted_rrf(streams, plan, pool_limit=20, range_from="2026-08-10", range_to="2026-09-09")


def test_same_article_boosts_the_public_candidate_and_keeps_the_private_one_separate():
    streams = {("primary", "web"): [_web("W1", ARTICLE, "Agent memory article")], ("primary", "corpus"): [_corpus()]}

    candidates = _fuse(streams)

    by_source = {c.source: c for c in candidates}
    assert set(by_source) == {"web", "corpus"}  # never merged
    public, private = by_source["web"], by_source["corpus"]
    assert public.rrf_score == 1.0 / 61 + min(private.rrf_score, fusion.CORPUS_CORROBORATION_CAP)
    assert public.metadata["corroborated_by_corpus"] == 1
    assert private.metadata["corroborates"] == [public.candidate_id]
    assert SECRET not in public.snippet and SECRET not in public.title
    assert all(item.source == "web" for item in public.source_items)
    assert private.url.startswith("corpus://")


def test_corroboration_is_visible_in_ranking():
    other = "https://example.org/other-article"
    streams = {
        ("primary", "web"): [_web("W1", other, "Other article", 0.8), _web("W2", ARTICLE, "Agent memory article", 0.8)],
        ("primary", "corpus"): [_corpus()],
    }

    candidates = _fuse(streams)

    public_order = [c.item_id for c in candidates if c.source == "web"]
    assert public_order == ["W2", "W1"]  # rank 2 plus the corpus vote beats rank 1


def test_boost_is_capped_at_one_top_rank_vote():
    streams = {
        ("a", "web"): [_web("W1", ARTICLE, "Agent memory article")],
        ("a", "corpus"): [_corpus()],
        ("b", "corpus"): [_corpus()],  # same file voted in two subqueries: rrf 2/61
    }

    candidates = _fuse(streams, _plan(["web", "corpus"], ("a", "b")))

    public = next(c for c in candidates if c.source == "web")
    assert public.metadata["corroboration_boost"] == fusion.CORPUS_CORROBORATION_CAP
    assert public.rrf_score == 1.0 / 61 + fusion.CORPUS_CORROBORATION_CAP


def test_no_boost_without_a_matching_url_or_without_a_canonical_url():
    streams = {
        ("primary", "web"): [_web("W1", "https://example.org/unrelated", "Unrelated")],
        ("primary", "corpus"): [_corpus(), _corpus(None, "C2")],
    }

    candidates = _fuse(streams)

    public = next(c for c in candidates if c.source == "web")
    assert public.rrf_score == 1.0 / 61 and "corroborated_by_corpus" not in public.metadata
    assert all("corroborates" not in c.metadata for c in candidates if c.source == "corpus")


def _report(candidates: list[schema.Candidate]) -> schema.Report:
    for index, candidate in enumerate(candidates):
        candidate.final_score = 90 - index
        candidate.cluster_id = "cluster-1"
    items_by_source: dict[str, list[schema.SourceItem]] = {}
    for candidate in candidates:
        for item in candidate.source_items:
            items_by_source.setdefault(item.source, []).append(item)
    return schema.Report(
        topic="agent memory",
        range_from="2026-08-10",
        range_to="2026-09-09",
        generated_at="2026-09-09T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime("local", "mock", "mock"),
        query_plan=_plan(["web", "corpus"]),
        clusters=[schema.Cluster("cluster-1", f"Cluster {SECRET}", [c.candidate_id for c in candidates], [candidates[0].candidate_id], ["web", "corpus"], 90)],
        ranked_candidates=candidates,
        items_by_source=items_by_source,
        errors_by_source={},
        source_status={source: schema.SourceOutcome(source, health.OK, len(items)) for source, items in items_by_source.items()},
    )


def test_exports_keep_the_public_article_and_lose_every_private_trace():
    streams = {("primary", "web"): [_web("W1", ARTICLE, "Agent memory article")], ("primary", "corpus"): [_corpus()]}
    report = _report(_fuse(streams))

    exported = schema.to_agent_export(report, corpus_in_export=False)
    text = str(exported)

    assert [r["source"] for r in exported["results"]] == ["web"]
    assert SECRET not in text and "corroborated_by_corpus" not in text and "corroboration_boost" not in text and "corpus://" not in text
    clean = schema.without_sources(report, {"corpus"})
    public = clean.ranked_candidates[0]
    assert public.rrf_score > 1.0 / 61  # the lift survives; only the marker is gone
    assert "corroborated_by_corpus" not in public.metadata


def test_local_render_marks_the_public_article_and_keeps_the_file_in_the_private_block():
    streams = {("primary", "web"): [_web("W1", ARTICLE, "Agent memory article")], ("primary", "corpus"): [_corpus()]}
    report = _report(_fuse(streams))

    rendered = render.render_compact(report)

    assert "in your files" in rendered
    assert "## From your files" in rendered and SECRET in rendered
    public_lines = [line for line in rendered.splitlines() if "Agent memory article" in line]
    assert public_lines and all(SECRET not in line for line in public_lines)


def test_malformed_frontmatter_url_never_aborts_the_run():
    streams = {("primary", "web"): [_web("W1", ARTICLE, "Agent memory article")], ("primary", "corpus"): [_corpus("http://[bad")]}

    candidates = _fuse(streams)

    assert {c.source for c in candidates} == {"web", "corpus"}
    assert all("corroborated_by_corpus" not in c.metadata for c in candidates)


def test_http_feed_link_corroborates_the_https_public_copy():
    streams = {("primary", "web"): [_web("W1", ARTICLE, "Agent memory article")], ("primary", "corpus"): [_corpus("http://example.com/news/agent-memory")]}

    candidates = _fuse(streams)

    public = next(c for c in candidates if c.source == "web")
    assert public.metadata["corroborated_by_corpus"] == 1


def test_two_files_naming_one_article_share_the_cap():
    streams = {
        ("primary", "web"): [_web("W1", ARTICLE, "Agent memory article")],
        ("primary", "corpus"): [_corpus(ARTICLE, "C1"), _corpus(ARTICLE, "C2")],
    }

    candidates = _fuse(streams)

    public = next(c for c in candidates if c.source == "web")
    assert public.metadata["corroborated_by_corpus"] == 2
    assert public.metadata["corroboration_boost"] == fusion.CORPUS_CORROBORATION_CAP
    assert public.rrf_score == 1.0 / 61 + fusion.CORPUS_CORROBORATION_CAP


def test_persisted_renders_never_carry_the_marker_but_screen_renders_do():
    streams = {("primary", "web"): [_web("W1", ARTICLE, "Agent memory article")], ("primary", "corpus"): [_corpus()]}
    report = _report(_fuse(streams))

    assert "in your files" in render.render_compact(report)  # the screen render the model reads
    # The saved markdown is what library.scan_library, the library brief publish
    # and the feed read; their strippers only remove the private block.
    assert "in your files" not in render.render_full(report)
    assert "in your files" not in render.render_for_html(report)
    sanitized = schema.without_sources(report, {"corpus"})
    for renderer in (render.render_compact, render.render_context, render.render_brief, render.render_full, render.render_for_html):
        assert "in your files" not in renderer(sanitized) and SECRET not in renderer(sanitized)
