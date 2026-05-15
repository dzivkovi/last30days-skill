# `dashboards/` — Datasette dashboards over `research.db`

A nine-panel [datasette-dashboards](https://github.com/rclement/datasette-dashboards) YAML on top of the `--store` SQLite file at `~/.local/share/last30days/research.db`. Designed for a non-BI marketing user — every chart is a navigation widget into the underlying findings, not a standalone report.

## Design philosophy

This dashboard follows established information-design principles. Read time matters more than chart count, so the layout is organized around the **glance → scan → elaborate** hierarchy familiar to readers of *The Big Book of Dashboards* (Wexler, Shaffer, Cotgreave) and *Storytelling with Data* (Knaflic):

- **A Big-Ass Number (BAN) sits in the top-left** as the orientation metric. One look tells you whether to keep scrolling.
- **Visuals occupy the upper half**, ranked by how often the user reaches for them. The word cloud earned the row-2 slot because callers actively seek vocabulary signal before drill-down detail.
- **Text tables are demoted below visuals** because reading them is high-cognitive-cost; their job is drilldown after a visual has narrowed the scope.
- **Chart titles state behavior honestly** — for example, the lifecycle bubble is named `"Lifecycle map — early cadence / emerging activity (matures with weekly cadence)"` because a Y=999 sentinel dominates the chart until two weeks of cadence accumulate. Titles that lie about what the data can show, do not belong in production dashboards.

These principles produce a dashboard you can read in 30 seconds for triage and re-read in 5 minutes for detail.

## Launch

```bash
PYTHONUTF8=1 datasette "$HOME/.local/share/last30days/research.db" \
  -m dashboards/trends.yaml \
  --port 8002
```

Then open http://localhost:8002/-/dashboards/last30days-trends.

> **Windows note.** Substitute `C:/Users/<you>/.local/share/last30days/research.db` for `$HOME/.local/share/...` if your shell doesn't expand `~`. The `PYTHONUTF8=1` prefix is required on Windows — datasette reads YAML through Python's default codec (cp1252 on Windows), which crashes on non-ASCII characters (em-dash, smart quotes, etc.). UTF-8 mode treats the file as the editor wrote it.

> **Port.** The `last30days` CLI's `--launch` flag uses port 8001. This dashboard uses 8002 so the two can coexist if you ever launch both.

## Filters and click-to-rescope

Three cascading filters are wired across the panels:

- **Topic** — drop-down sourced from the `topics` table; `id != 2` excludes the "test topic" left over from May-04 fixture testing.
- **From date** — defaults to `2026-05-01`. Applies to the time-aware panels (Buzz by Day, Source Mix, Word Cloud).
- **Keyword** — text input. When set, every content panel except `gone-quiet` scopes to findings whose `source_title` contains the keyword (substring, case-insensitive). Click any cloud word, or any "explore" link in the resurfacing/top-posts tables, to populate this filter from the URL (`?keyword=<word>`). Clear the input + Apply to escape the scope. Full contract documented in [`DESIGN.md`](DESIGN.md) — "The click-to-rescope contract" section.

Filters use datasette's `[[ AND ... ]]` *optional-WHERE block* syntax — when a parameter is unset, the clause is dropped entirely.

## The 9 panels (in render order, top-to-bottom)

| Row | Panel | Question answered in 5 s | Library |
|---|---|---|---|
| 1 (left) | Findings this week | "Did anything happen?" | `metric` |
| 1 (right) | Buzz by day | "Heating or cooling, per topic?" | `vega-lite` (line) |
| 2 | Engagement-weighted word cloud | "What vocabulary dominates?" | `vega` (text marks + wordcloud transform, clickable) |
| 3 | Lifecycle map | "Where is each topic along its lifecycle?" | `vega-lite` (bubble) |
| 4 | Source mix over time | "Where's the conversation moving?" | `vega-lite` (stacked area) |
| 5 | Trend matrix | "Which topic is hot *this week*?" | `vega-lite` (scatter) |
| 6 | Resurfacing content | "What stories keep coming back?" | `table` (10-row cap) |
| 7 | Top posts | "Show me the actual content" | `table` (10-row cap) |
| 8 | Gone quiet | "Where did the conversation stop?" | `table` |

The word cloud sits at row 2 because it is the most-reached-for visual; text tables sit at rows 6-8 because they serve drilldown, not orientation. The lifecycle map and trend matrix share growth-ratio math but plot different X-axes — see [`DESIGN.md`](DESIGN.md) for the side-by-side rationale.

### Wordcloud renderer: `library: vega` + wordcloud transform (clickable)

The original synthesis (see `work/2026-05-05/17-trends-dashboard-synthesis-three-agents.md`) called for `library: wordcloud`, but **datasette-dashboards 0.8.0 ships only five renderers** (`vega`, `vega-lite`, `metric`, `table`, `map`) and no wordcloud shorthand. The pattern that works in v0.8.0: `library: vega` with the bundled vega's wordcloud transform applied as a mark transform, referencing the auto-injected `table` data source rather than redeclaring `data:` (which would clobber the query results).

To make each word clickable in this combo, set `interactive: true` at the mark level and bind `href: { field: href }` in `enter` and `update`. Vega installs the click handler at runtime via JS — the rendered SVG has zero `<a>` wrappers, which is **expected** and not a bug. Hover tooltips and `cursor: pointer` work the same way (runtime, not via SVG attrs). The diagnostic warning + canonical YAML pattern + verification recipe are captured in [`docs/solutions/design-patterns/vega-text-mark-runtime-click-handlers-2026-05-07.md`](../docs/solutions/design-patterns/vega-text-mark-runtime-click-handlers-2026-05-07.md) — read that before debugging "the cloud renders but my clicks do nothing."

### Week-2-meaningful and week-3-meaningful panels

Three panels are intentionally sparse on day 1 because the underlying signal needs time to accumulate:

- **Lifecycle map** (row 3) and **Trend matrix** (row 5) both compute a week-over-week growth ratio with a `999` sentinel value when last week's count is zero. Until every topic has data in BOTH last week's window and this week's window, the Y-axis is dominated by sentinels rather than real growth ratios. The lifecycle map's title labels this honestly — "early cadence / emerging activity" is what the chart actually shows in week 1; "lifecycle truth" is what it shows from week 3 onward.
- **Gone quiet** (row 8) compares the latest run for each topic to the previous run. It's empty until a topic has at least two `research_runs` rows AND the latest one shows < 30% of the prior run's findings. Worth eyeballing weekly from week 3 onward.

Don't be alarmed if these panels look bare in the first few days — that's by design. The way to populate them is to set `topics.schedule` to `weekly` on the topics you care about and run them on a regular cadence.

## Engine-jargon stopwords in the word cloud

The current `summary` field occasionally contains engine-internal text — `fallback-local-score` (the placeholder when relevance scoring fails) appears in 58 of 259 findings as of writing — plus reasoning words like "irrelevant", "factual", "specific", "provides". The word-cloud SQL filters these via an inline `NOT IN` clause. Tune the list by editing `dashboards/trends.yaml` directly; SQLite re-runs the query on every page load.

## Customizing

For the design rationale (why these 9 panels, the SQL contract every panel must follow, a 5-step recipe to add a new panel, how to tune the tag-cloud font sizes, where to look first when something breaks) see [`DESIGN.md`](DESIGN.md). That doc is the customization handbook; this README is the launch sheet. The Roadmap chapter at the bottom of `DESIGN.md` tracks the next iteration boundary — storytelling titles, color discipline, audience separation.

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
2. All 9 panel container `id`s render in the DOM (no panel silently dropped).
3. The page loads with no `error`-level console messages.
4. The word-cloud panel contains ≥ 10 distinct word elements.
5. The resurfacing-content table contains at least one resurfacing row from the active corpus.
6. The top-posts table has at least 5 rows whose `Link` cell is an `<a href>` anchor.
7. A full-page screenshot is saved to `work/YYYY-MM-DD/screenshots/dashboard-rendered.png`.

## Roadmap — toward design discipline

This dashboard is a strong v1 that adopts the high-leverage information-design principles (hierarchy, BAN, honest titles, visual-first layout). Three established practices remain unimplemented and should be considered as the work matures:

1. **Titles-as-takeaways.** Static titles describe the chart; great titles state the insight (e.g. "Toronto real estate spiked May 9 — biggest day this month" instead of "Buzz by day"). Implementing this requires dynamic titles computed from the data, likely as a separate "today's headline" metric panel rather than mutable chart titles.
2. **Color discipline.** Current defaults give every topic a unique color, producing a "bowling-alley" effect where lots of color carries little signal. A grayscale baseline with one accent color reserved for the metric you want the eye drawn to — the practice favored by Knaflic — would convert color from decoration into emphasis.
3. **Audience separation.** A single dashboard serving two audiences (e.g. client work + ecosystem tracking) splits its design attention between them. Wexler's Big Book recommends one dashboard per audience, each tuned for its specific scenario. Worth considering once the data substantiates two distinct read patterns.

Each item above maps to a chapter in either Wexler/Shaffer/Cotgreave or Knaflic. The current state is sufficient for daily research operation; these iterations move it toward executive-grade communication.

## Related

- `work/2026-05-02/datasette-metadata.yaml` — the earlier 4-panel sample dashboard. Kept as a reference of the simpler version; not edited.
- `work/2026-05-05/16-datasette-dashboard-revisit-real-data.md` — data-state snapshot at the time this dashboard was designed.
- `work/2026-05-05/17-trends-dashboard-synthesis-three-agents.md` — research synthesis from three parallel agents that informed the panel selection.
- `work/2026-05-05/18-overnight-dark-factory-primer.md` — the primer that guided the build (includes the original SQL specs).
- [`scripts/watchlist.py`](../scripts/watchlist.py) and [`scripts/briefing.py`](../scripts/briefing.py) — the tools that populate `research.db`.
