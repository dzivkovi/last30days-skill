---
date: 2026-05-06
topic: clickable-cloud-rescope-dashboard
---

# Clickable wordcloud + dashboard rescope on click

## Problem Frame

The trends dashboard is currently a *read-only* report. Daniel (and a future client like Jasmina) sees the wordcloud and intuits "I want to click `toronto` and see only those findings" — the original "table of contents for the text" mental model — but no panel is clickable today. The cloud is a vocabulary reference; the resurfacing/top-posts tables drill *outside* the dashboard via `<a href>` to source URLs. Nothing drills *inward* into the dashboard's own data.

Goal: make the dashboard a navigation widget set, not a report. A click on a cloud word (and analogous clicks on table rows) re-scopes the entire dashboard to findings whose `source_title` matches that word. The same dashboard, narrower lens.

Affects: anyone using `dashboards/trends.yaml` over the SQLite store at `~/.local/share/last30days/research.db`. Most directly, Daniel — and the client persona he's building this for, who wants to explore "what's everyone saying about X" without writing SQL.

## Requirements

**Keyword filter (the rescope mechanism)**

- R1. A new `keyword` filter is added to the dashboard, alongside the existing `topic` (select) and `date_start` (date) filters. Type: `text`. Default: empty (no scope).
- R2. When `keyword` is set, every panel that displays content (findings-this-week, buzz-by-day, source-mix, resurfacing, trend-matrix's `this_week`/`prev_week` counts, word-cloud, top-posts) restricts to findings where `source_title LIKE '%' || :keyword || '%'` (case-insensitive). `gone-quiet` is exempt — it operates on run-level data, not finding-level.
- R3. Clearing the keyword input + clicking Apply restores the unscoped view.
- R4. The keyword filter is fully shareable via URL: `?keyword=toronto&date_start=2026-05-01` is a valid bookmark.

**Click sources (what is clickable)**

- R5. Every word in the wordcloud is a hyperlink. Clicking it navigates the dashboard to itself with `?keyword=<word>` appended (preserving any other filters already set).
- R6. Each row in the resurfacing-content table has a clickable cell linking back to the dashboard with `?keyword=<word-from-title>`. The "open" anchor for the source URL stays as-is; this is an additional, separate link cell ("explore" or similar).
- R7. Each row in the top-posts table gets the same dashboard-rescope anchor as resurfacing.
- R8. Vega-lite charts (buzz-by-day, source-mix, trend-matrix) are NOT made clickable in this iteration — see Scope Boundaries.

**Escape and discoverability**

- R9. The keyword filter input must be visible at all times (not collapsed) so a user can clear it without re-finding the dropdown.
- R10. When a keyword is active, the dashboard's title or a banner-like cue reflects the active scope (e.g., the existing dashboard description text mentions the active keyword).
- R11. The free-text keyword input also works as a search box — typing `mortgage` + Apply scopes the dashboard the same way as clicking `mortgage` in the cloud.

## Success Criteria

- A user clicks a word in the cloud and sees the dashboard re-render with that word as the active keyword, no SQL knowledge required.
- The number reported by `findings-this-week` after a click matches `SELECT COUNT(*) FROM findings WHERE source_title LIKE '%<word>%' AND first_seen >= date('now', 'localtime', '-7 days') AND topic_id != 2 AND dismissed = 0`.
- After clicking, the wordcloud regenerates from titles that contain the clicked word (showing co-occurring vocabulary) — confirming the cloud is now scoped, not the same as before.
- Clearing the keyword input restores the unscoped dashboard within one Apply click.
- Bookmarking a `?keyword=<word>` URL restores the same scoped view in a new browser session.

## Scope Boundaries

- No custom JavaScript. Vega's native `href` encoding on text marks + datasette's URL-driven filter passing handle everything.
- No FTS5. The `keyword` filter uses `LIKE '%...%'` substring match on `source_title` only, not `summary`. Substring is good enough at the current corpus size; FTS5 is a future performance/precision upgrade and lives in the planning phase if it ever happens.
- No click handlers on the vega-lite charts (buzz-by-day, source-mix, trend-matrix). Vega-lite signals can do this, but require more YAML and produce edge cases (clicking a stacked-area slice — what does that mean?). Deferred.
- No row-click on resurfacing/top-posts that hijacks the existing "open" external-source anchor. The new dashboard-rescope link is a *separate* cell; the existing source-URL drill stays.
- No multi-keyword AND/OR composition. One keyword at a time. Compound search is FTS5 territory.
- `gone-quiet` does not respect the keyword. It's a "which topics quieted across runs" panel; rescoping it by title-substring would lose its meaning.

## Key Decisions

- **Rescope-this-dashboard over open-table-view.** The recommended option from brainstorm. Keeps the user inside the dashboard's mental model rather than dumping them into datasette's raw-table viewer. Daniel selected this option explicitly.
- **Match against `source_title` only, not `summary`.** Titles are content-rich; summaries contain engine-reasoning prose ("provides relevant", "off-topic", "fallback-local-score") that polluted the wordcloud earlier. Matching titles also matches what's *visible* in the cloud — clicking a word the user can see produces predictable results.
- **`LIKE '%...%'` over FTS5.** Substring is good enough at <10k findings. The `findings_fts` virtual table exists but introducing it adds an implementation surface (FTS5 syntax escaping, MATCH operator quirks). Substring is dumb-and-correct.
- **Click sources limited to the cloud + resurfacing + top-posts in v1.** These three are the easiest to make clickable (text marks + table HTML cells). Adding vega-lite chart-click is a non-trivial UX design problem (what does clicking a stacked-area slice mean?) and shouldn't gate v1.
- **Keyword filter input is text + Apply, not text + auto-submit-on-keystroke.** datasette-dashboards' filter UI is built around explicit Apply. Don't fight the framework.

## Dependencies / Assumptions

- Datasette-dashboards 0.8.0 is the installed version. Vega's `href` field on text marks works in vega 5.x (the version bundled with the plugin). [Verified: vega.min.js bundled in v0.8.0 includes the wordcloud transform; href is a documented standard vega encoding.]
- The `findings.source_title` column is non-null for the panels we're scoping. Verified at the current corpus state — most rows have a title; the cleaned CTE in the wordcloud already handles `WHERE source_title IS NOT NULL`.
- Datasette renders dashboard URL parameters as `:param` bindings into the SQL. Verified via the existing `:topic` and `:date_start` filters working today.

## Outstanding Questions

### Resolve Before Planning

*(none — the brainstorm produced enough product clarity to proceed to planning.)*

### Deferred to Planning

- [Affects R6/R7][Technical] What word do we use for the rescope anchor in resurfacing/top-posts? The first noun in the title? The most-engagement word from the cloud that appears in this row's title? A picker dropdown? **Default proposal**: extract the first 1-2 capitalized tokens from the title and use the longest as the keyword. The planner should validate this against the corpus.
- [Affects R10][Design] What's the visible cue that a keyword is active? A banner above the panels? An update to the dashboard description? Tinting the panels? Datasette-dashboards may not support all of these natively. The planner should pick the lowest-friction option that's visible without being garish.
- [Affects R5][Needs research] When a wordcloud word contains URL-unsafe characters (`#torontorealestate` includes `#`), does vega's `href` encoding URL-encode them automatically, or do we need to wrap with `encodeURIComponent` in the signal expression? Test before shipping.
- [Affects R2][Performance] At 10k+ findings, does `LIKE '%toronto%'` on `source_title` (no index) slow the dashboard meaningfully? If yes, the engine-side `findings_fts` virtual table becomes load-bearing for this feature. Measure before deciding.

## Visual aid — click flow

```
   ┌──────────────────────────────────────────────────────────────┐
   │  Dashboard URL: /-/dashboards/last30days-trends              │
   │  ?date_start=2026-05-01  (no keyword set)                    │
   │                                                               │
   │  [ Topic: All ▾ ]  [ From date: 2026-05-01 ]  [ Keyword: __ ]│
   │                                                               │
   │   Findings: 274     Buzz: ███▆▃     Source mix: ▒▓▒▓▒        │
   │   Resurfacing: 20 rows   Trend matrix: scatter               │
   │                                                               │
   │   ┌─── word cloud ────────────────────────────────────────┐  │
   │   │   toronto  condo  market  REAL  ESTATE  ◀── click    │  │
   │   │   prices  #torontorealtor  fees  buyers  buying       │  │
   │   └────────────────────────────────────────────────────────┘  │
   │   Top posts: 20 rows                                          │
   └──────────────────────────────────────────────────────────────┘
                                │
                                ▼   click "toronto"
                                │
                ?keyword=toronto&date_start=2026-05-01
                                │
                                ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  Dashboard URL: /-/dashboards/last30days-trends              │
   │  ?keyword=toronto&date_start=2026-05-01                      │
   │                                                               │
   │  [ Topic: All ▾ ]  [ From date: 2026-05-01 ]                 │
   │  [ Keyword: toronto ] ◀── filled in, can clear to escape    │
   │                                                               │
   │   Findings: 95 (titles containing "toronto")                 │
   │   Buzz:   ▆▃           ◀── only toronto-titled findings     │
   │   Source mix: ▒▓        ◀── narrower; tiktok dominant        │
   │   Resurfacing: 8 rows   ◀── all toronto-titled               │
   │   Trend matrix: scatter ◀── this_week/prev_week scoped       │
   │                                                               │
   │   ┌─── word cloud ────────────────────────────────────────┐  │
   │   │   condo  market  fees  estate  real  prices ──        │  │
   │   │   ▲ co-occurring vocabulary AROUND "toronto"           │  │
   │   └────────────────────────────────────────────────────────┘  │
   │   Top posts: 20 rows (toronto-titled)                         │
   └──────────────────────────────────────────────────────────────┘
```

## Next Steps

`-> /ce-plan` for structured implementation planning. Pass this requirements doc as the spec.
