# `dashboards/` — Datasette dashboards over `research.db`

Experimental [datasette-dashboards](https://github.com/rclement/datasette-dashboards) YAML on top of the `--store` SQLite file at `~/.local/share/last30days/research.db`. Designed for a non-BI marketing user — every chart is a navigation widget into the underlying findings, not a standalone report.

## Launch

```bash
datasette "$HOME/.local/share/last30days/research.db" \
  -m dashboards/trends.yaml \
  --port 8002
```

Then open http://localhost:8002/-/dashboards/last30days-trends.

> **Windows note.** Substitute `C:/Users/<you>/.local/share/last30days/research.db` for `$HOME/.local/share/...` if your shell doesn't expand `~`.

> **Port.** The `last30days` CLI's `--launch` flag uses port 8001. This dashboard uses 8002 so the two can coexist if you ever launch both.

## Topic and date filters

Two cascading filters are wired across the panels:

- **Topic** — drop-down sourced from the `topics` table; `id != 2` excludes the "test topic" left over from May-04 fixture testing.
- **From date** — defaults to `2026-05-01`. Only applies to the time-aware panels (Buzz by Day, Source Mix, Word Cloud).

Filters use datasette's `[[ AND ... ]]` *optional-WHERE block* syntax — when a parameter is unset, the clause is dropped entirely.

## The 8 panels

| # | Panel | Question answered in 5 s | Library |
|---|---|---|---|
| 1 | Findings this week | "Did anything happen?" | `metric` |
| 2 | Buzz by day | "Heating or cooling, per topic?" | `vega-lite` (line) |
| 3 | Source mix over time | "Where's the conversation moving?" | `vega-lite` (stacked area) |
| 4 | Resurfacing content | "What stories keep coming back?" | `table` |
| 5 | Trend matrix | "Which topic is *emerging*?" | `vega-lite` (scatter) |
| 6 | Engagement-weighted top words | "What vocabulary dominates?" | `table` (sized HTML spans) |
| 7 | Top posts | "Show me the actual content" | `table` |
| 8 | Gone quiet | "Where did the conversation stop?" | `table` |

### Substitution: word cloud → sized-span table

The original synthesis (see `work/2026-05-05/17-trends-dashboard-synthesis-three-agents.md`) called for `library: wordcloud`, but **datasette-dashboards 0.8.0 ships only five renderers** (`vega`, `vega-lite`, `metric`, `table`, `map`) and no wordcloud library. The bundled `vega.min.js` does include a wordcloud transform, but `renderVegaChart` spread-merges its own `data: [{name: 'table', values: ...}]` last, so a full vega spec that redeclares `data` would clobber the injected query results.

The pragmatic fix: render the cloud as a `library: table` whose first column is `<span style="font-size:Npx">word</span>`. The table renderer assigns `innerHTML = col` (see `dashboards.js:154`), so inline span styling renders verbatim. Same insight as a circular cloud — vocabulary by engagement — without the layout dependency.

If a future plugin version adds a real `wordcloud` renderer, swap the `library: table` line back to `library: wordcloud` and remove the `<span>` wrapping in the SQL.

### Week-2-meaningful and week-3-meaningful panels

Two panels are intentionally sparse on day 1 because the underlying signal needs time to accumulate:

- **Trend matrix** (panel 5) compares this week's findings count to last week's. It only produces a meaningful scatter once two weeks of data exist. On day 1 every topic sits at growth_ratio = 0 (no prior week).
- **Gone quiet** (panel 8) compares the latest run for each topic to the previous run. It's empty until a topic has at least two `research_runs` rows AND the latest one shows < 30% of the prior run's findings. Worth eyeballing weekly from week 3 onward.

Don't be alarmed if these panels look bare in the first few days — that's by design.

## Engine-jargon stopwords in the word cloud

The current `summary` field occasionally contains engine-internal text — `fallback-local-score` (the placeholder when relevance scoring fails) appears in 58 of 259 findings as of writing — plus reasoning words like "irrelevant", "factual", "specific", "provides". The word-cloud SQL filters these via an inline `NOT IN` clause. Tune the list by editing `dashboards/trends.yaml` directly; SQLite re-runs the query on every page load.

## Customizing

This file ships in the **fork** (`dzivkovi/last30days-skill@daniel/personal`), not upstream. The fork-vs-upstream switching pattern lives in [`CONFIGURATION.md`](../CONFIGURATION.md) Section 5.

To make this YAML the default for the `/last30days:last30days` plugin, point your marketplace at the fork branch:

```text
/plugin uninstall last30days
/plugin marketplace add dzivkovi/last30days-skill@daniel/personal
/plugin install last30days
/reload-plugins
```

Then re-launch datasette with `-m dashboards/trends.yaml` whenever you want to look at the data.

## Validation

The Definition of Done for this dashboard requires:

1. `datasette ... -m dashboards/trends.yaml` returns HTTP 200 on the dashboard route.
2. All 8 panel container `id`s render in the DOM (no panel silently dropped).
3. The page loads with no `error`-level console messages.
4. The word-cloud panel contains ≥ 10 distinct word elements.
5. The resurfacing-content table contains a row matching `Toronto Condo Fees` or `Toronto Condo Graveyard` (known-good resurfacing rows in the May-05 corpus).
6. The top-posts table has at least 5 rows whose `Link` cell is an `<a href>` anchor.
7. A full-page screenshot is saved to `work/YYYY-MM-DD/screenshots/dashboard-rendered.png`.

The `feat/trends-dashboard-yaml` PR opens against the fork's `daniel/personal` branch with this validation evidence; halt before merging for human review.

## Related

- `work/2026-05-02/datasette-metadata.yaml` — the earlier 4-panel sample dashboard. Kept as a reference of the simpler version; not edited.
- `work/2026-05-05/16-datasette-dashboard-revisit-real-data.md` — data-state snapshot at the time this dashboard was designed.
- `work/2026-05-05/17-trends-dashboard-synthesis-three-agents.md` — research synthesis from three parallel agents that informed the panel selection.
- `work/2026-05-05/18-overnight-dark-factory-primer.md` — the primer that guided the build (includes the original SQL specs).
- [`scripts/watchlist.py`](../scripts/watchlist.py) and [`scripts/briefing.py`](../scripts/briefing.py) — the tools that populate `research.db`.
