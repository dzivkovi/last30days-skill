---
title: Vega text-mark click handlers fire at runtime, not via SVG `<a>` wrappers
date: 2026-05-07
category: design-patterns
module: dashboards
problem_type: design_pattern
component: tooling
severity: medium
applies_when:
  - making any vega text mark clickable in datasette-dashboards 0.8.0
  - "verifying clickability of any vega chart with Playwright (use `browser_click`, not static DOM inspection)"
  - extending the wordcloud or any other vega panel with hover tooltips or `cursor: pointer`
  - debugging "the cloud renders but my clicks do nothing" reports
tags:
  - vega
  - datasette-dashboards
  - wordcloud
  - playwright
  - dashboard-clickability
related_components:
  - dashboard
  - testing_framework
---

# Vega text-mark click handlers fire at runtime, not via SVG `<a>` wrappers

## Context

When PR #4 (`feat(dashboards): clickable wordcloud + dashboard-wide keyword rescope`) added per-word clickability to the trends dashboard's wordcloud, the verification path used `mcp__playwright__browser_evaluate` to count `<a>` SVG anchor elements wrapping the rendered `<text>` marks. The probe found zero — and concluded the bundled vega 6.2.0 in datasette-dashboards 0.8.0 was filtering out the `href` channel on text marks. That diagnosis was wrong, but it triggered a 30-minute pivot away from `library: vega` to `library: table` + GROUP_CONCAT'd HTML anchors. The fallback was clickable, but lost vega's spatial wordcloud layout — words flowed inline instead of packing in 2D. The user noticed the visual regression and asked to revert. A correct verification (a real browser click) revealed vega's clicks had been firing all along — at runtime, not via static SVG attributes.

This doc captures the canonical pattern for clickable vega text marks in this codebase, plus the diagnostic warning that catches the next contributor who would otherwise re-walk the same dead end.

## Guidance

To make vega text marks clickable in datasette-dashboards 0.8.0, you need three things working together: the YAML config, a SQL-built `href` column, and a verification recipe that uses real browser interaction instead of static DOM inspection.

### YAML config (canonical pattern)

```yaml
word-cloud:
  db: research
  query: |
    -- ... CTE chain producing (word, weight, href) ...
    -- href is pre-built in SQL because vega's expression language
    -- doesn't expose encodeURIComponent.
  library: vega
  display:
    height: 320
    scales:
      - name: color
        type: ordinal
        domain: { data: table, field: word }
        range: ["#d5a928", "#652c90", "#939597"]
    marks:
      - type: text
        interactive: true              # ← required at mark level
        from: { data: table }
        encode:
          enter:
            text: { field: word }
            fill: { scale: color, field: word }
            cursor: { value: pointer } # hover affordance
            href: { field: href }      # bind to SQL-built column
            tooltip: { signal: "'click to rescope to ' + datum.word" }
          update:
            href: { field: href }      # also in update — covers reactive re-renders
          hover:
            fillOpacity: { value: 0.5 }
        transform:
          - type: wordcloud
            size: [{ signal: width }, { signal: height }]
            text: { field: word }
            font: "Helvetica Neue, Arial"
            fontSize: { field: datum.weight }
            fontWeight: "300"
            fontSizeRange: [12, 56]
            padding: 2
```

### SQL emits the `href` column with URL-safe escapes

Vega's sandboxed expression language doesn't expose `encodeURIComponent`. SQLite has no native URL encoder. Build the URL string in SQL with `replace()` chains escaping the chars that break query strings: `#` (fragment delimiter), `&`, `?`, and space.

```sql
SELECT b.value AS word,
       SUM(arr.engagement_score) AS weight,
       '?keyword=' ||
         replace(replace(replace(replace(
           b.value,
         '#', '%23'), '&', '%26'), '?', '%3F'), ' ', '%20') AS href
FROM arr, json_each(arr.a) b
-- ...
```

### Verification recipe

```text
✓  mcp__playwright__browser_click on a `text` element inside the chart's SVG.
   Read Page URL after the click — it should change to the item's `href` value.

✗  mcp__playwright__browser_evaluate searching for `<a>` elements inside the SVG.
   Will return zero. Vega does NOT wrap clickable marks in SVG anchors;
   it installs JS click handlers at the View instance level at runtime.
   Static DOM probes can't see runtime event listeners.
```

The same trap applies to `tooltip` (vega's hover plugin renders into a separate floating div, not via SVG `<title>` attrs) and `cursor` (set on the chart container element, not on individual marks). A static DOM probe will report "tooltip absent" even when a real browser shows it on hover.

## Why This Matters

The cost of getting it wrong on this PR was ~30 minutes of YAML pivoting plus a confusing commit history (the failed `library: table` approach lives at commit `876e8ab`; the correct `library: vega` + `interactive: true` approach lands at `148f7e7`). The user caught it visually before merge — without that visual check, we would have shipped a degraded cloud. The next contributor adding click rescope to a different vega panel (buzz-by-day spikes? source-mix legend? trend-matrix dots?) is a coin-flip away from the same dead end if they trust a static DOM probe.

There's also a deeper lesson: **headless DOM tools verify the parse-time DOM. Visual / event-driven correctness needs a different verification path** — `browser_click` for clicks, `browser_hover` + screenshot for tooltips, console-message inspection for runtime errors. Don't conflate "the SVG has the right elements" with "the chart works."

## When to Apply

- Making any vega text mark clickable (wordclouds, label clouds, chip lists rendered as marks).
- Adding hover tooltips to vega panels — set `tooltip: { signal: "..." }` in `enter`, NOT a static `<title>` SVG attr.
- Changing `cursor` based on data — set `cursor: { value: pointer }` in `enter`, accept that the SVG output won't show it; the chart container handles cursor.
- Reviewing a contributor's "I made the cloud clickable but it doesn't work" PR — first ask whether they tested with a real browser click or only with static DOM inspection.
- Extending `dashboards/trends.yaml` with new vega panels that need any user interaction.

## Examples

### Before (commit 876e8ab — the wrong pivot)

The wordcloud was rebuilt in `library: table` to get real `<a>` wrappers visible in static DOM:

```yaml
word-cloud:
  query: |
    -- ... ranked CTE ...
    SELECT COALESCE(
             GROUP_CONCAT(
               '<a href="?keyword=' || ... || '">' || word || '</a>',
               ' '
             ),
             '<em>(no words match this keyword)</em>'
           ) AS "Word cloud"
    FROM ranked
  library: table
```

Result: real `<a>` tags, real clicks, but a flowing inline list of words — no spatial packing, no wordcloud aesthetic.

### After (commit 148f7e7 — the correct pattern)

`library: vega` restored with `interactive: true` + `href: { field: href }` + `tooltip` + `cursor`. SQL emits a clean `(word, weight, href)` shape. Rendered SVG has 100 `<text>` elements, **zero `<a>` wrappers** — and clicks fire correctly through vega's runtime handler. Hover shows "click to rescope to <word>" tooltip.

### Verification before/after

```text
Wrong:
  evaluate(() => document.querySelectorAll('#chart-word-cloud svg a').length)
  → 0
  Conclusion: "vega href is broken in this bundle" ❌

Right:
  browser_click(target: '#chart-word-cloud svg text:first-of-type')
  → Page URL: http://localhost:8002/.../?keyword=condo
  Conclusion: "vega click handler fires correctly at runtime" ✓
```

## Related

- Pull request: [#4 — feat(dashboards): clickable wordcloud + dashboard-wide keyword rescope](https://github.com/dzivkovi/last30days-skill/pull/4) (commits `876e8ab` failed pivot, `148f7e7` correct pattern).
- Sibling docs in this repo:
  - `dashboards/DESIGN.md` — "Three places to look first when something breaks" troubleshooting table has a row pointing here.
  - `CLAUDE.md` — "Dashboards conventions" block summarizes the pattern in one bullet.
- Adjacent failure modes documented in DESIGN.md but not (yet) in this folder:
  - Empty-result-set table panel crash (`Object.keys(data.rows[0])` TypeError) — handled by `shaped` CTE + `UNION ALL` sentinel pattern in `resurfacing` and `top-posts`.
  - Filter-not-composing-across-clicks — clicking a cloud word resets `topic` and `date_start` because the `href` field is a fresh URL, not a merge of `window.location.search`. Deferred to a future iteration.
