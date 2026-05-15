# `dashboards/` — design notes & customization guide

Companion to [`README.md`](README.md). The README tells you *how to launch* the dashboard; this file tells you *why it looks like this* and *how to change it without breaking the contract*.

Audience: future-you (or a teammate / client) wanting to add a panel, swap a chart, prune the stopwords, or borrow this pattern for a different topic. Assumes you can read SQL and YAML; does not assume you've read the upstream `datasette-dashboards` source.

---

## Mental model — every chart is a navigation widget

The original framing came from research on commercial trend-sensing tools (Brandwatch, Talkwalker, Sprout Social, Brand24, Meltwater). Their dashboards converged on six visual idioms, and the unifying observation was: **every chart is an entry point into the underlying findings, not a standalone report**. Click a spike on the volume line → see the posts that caused it. Click a word in the cloud → filter the table to findings using that word. Click a topic in the matrix → see why it's hot.

That's the right mental model for a non-BI marketing user. They aren't reading the dashboard; they're using it as *a table of contents for the text*. Every panel earns its place by answering one question in five seconds and offering a click into specifics.

If you're adding a new panel, the test is: **can a marketer answer a specific question in 5 seconds and click into more detail?** If not, it doesn't belong here — it belongs in a SQL notebook or a longer-form digest.

---

## Why these 9 panels (and not others)

Panels are listed in render order, top-to-bottom. Order matters: the YAML layout block controls intra-row pairing only — row sequence follows chart-definition order in the YAML.

| Row | Panel | Question answered in 5s | Why it earns its slot |
|---|---|---|---|
| 1 (L) | Findings this week | "Did anything happen?" | The BAN (Big-Ass Number) at the top-left. Orientation only. One look tells you whether to keep scrolling. |
| 1 (R) | Buzz by day | "Heating or cooling, per topic?" | Volume-over-time is the spine of every commercial dashboard. Multi-line by topic so you see relative shape at a glance, paired with the BAN to anchor row 1. |
| 2 | Engagement-weighted word cloud | "What vocabulary dominates?" | Promoted to row 2 because callers reach for vocabulary signal before drill-down. Sized by `SUM(engagement)`, not raw frequency — high-engagement words are what's worth amplifying. Clickable. |
| 3 | Lifecycle map ⭐ | "Where is each topic along its lifecycle?" | Talkwalker-style bubble chart. X = total findings ever (Emerging → Established), Y = week-over-week growth ratio (Falling → Rising), size = avg sighting_count (stickiness). Four-quadrant lifecycle view that matures as cadence accumulates. Title labels current state honestly: "early cadence / emerging activity (matures with weekly cadence)." |
| 4 | Source mix over time | "Where's the conversation moving?" | Stacked area answers "is this trend Reddit-specific, or is it everywhere?" Platform-shift signal. |
| 5 | Trend matrix | "Which topic is hot *this week*?" | 2D scatter, this-week volume × growth ratio. Same growth-ratio math as the lifecycle map (one mental model for the 999 sentinel) but plots THIS week's findings on X — a current-snapshot complement to the lifecycle map's history-aware view. Quadrant labels: top-right hot, top-left emerging, bottom-right established, bottom-left fading. |
| 6 | Resurfacing content | "What stories keep coming back?" | `sighting_count > 1` rows, capped to 10. Same article showing up across feeds = compounding signal. Drill-down for the visual-first panels above. |
| 7 | Top posts | "Show me the actual content" | The drill terminus, capped to 10. Every other panel leads here. Sorted by `sighting_count, engagement` — recurring content first, viral one-hits second. |
| 8 | Gone quiet | "Where did the conversation stop?" | Surprise contribution from the research pass. Silence ahead of an announcement is sometimes the strongest signal a marketer can act on. Becomes meaningful at week 3. |

Three things this panel set deliberately *doesn't* include and why:

- **Pie chart of source breakdown.** No time axis = no signal. Replaced with stacked area over time (row 4).
- **Top 10 by raw engagement.** Engagement alone surfaces one-hit viral noise. The drill terminus (row 7) sorts by sighting first, engagement second — recurrence is a stronger signal than peak engagement.
- **Sentiment chart.** Out of scope until the engine writes a sentiment field. Don't add a panel that requires data the schema doesn't carry.

### Lifecycle map vs Trend matrix — when to use which

Both charts use the same growth-ratio math (the same 999 sentinel for last_week=0) but plot a different X-axis. They answer related questions but are NOT redundant:

| Dimension | Lifecycle map (row 3) | Trend matrix (row 5) |
|---|---|---|
| X-axis | **Total findings EVER** (cumulative since first scan) | **Findings THIS WEEK only** |
| Y-axis | Week-over-week growth ratio (same formula) | Same |
| Bubble size | Avg sighting_count (lifecycle depth) | This-week volume (matches X) |
| Topics shown | Filtered to `total_findings > 0` | All topics, including zero-volume smoke tests |
| Question it answers | "Where is each topic in its overall lifecycle?" | "How big and how growing is each topic this week?" |

Keep both. The lifecycle map captures history-aware position; the trend matrix captures current-week snapshot. Either one alone leaves a blind spot.

Three things this panel set deliberately *doesn't* include and why:

- **Pie chart of source breakdown.** No time axis = no signal. Replaced with stacked area over time (panel 3).
- **Top 10 by engagement.** Engagement alone surfaces one-hit viral noise. The drill terminus (panel 7) sorts by sighting first, engagement second — recurrence is a stronger signal than peak engagement.
- **Sentiment chart.** Out of scope until the engine writes a sentiment field. Don't add a panel that requires data the schema doesn't carry.

---

## Anatomy of a panel

Every panel in `trends.yaml` has the same skeleton:

```yaml
        <chart-id-in-kebab-case>:
          title: <human-readable>
          description: |          # optional; appears under the title
            <one-paragraph caveat or context>
          db: research            # always research (the only db in this YAML)
          query: |
            <SQL — use [[ AND foo = :param ]] for optional filters>
          library: <metric|vega-lite|table|map|vega>
          display:
            <library-specific keys>
```

Four hard rules every panel must follow:

1. **Filter the test-topic noise.** Every WHERE clause must include `AND topic_id != 2 AND dismissed = 0` to skip the May-04 fixture topic and soft-deleted rows. The dropdown filter excludes topic_id=2 too, but a panel that skips this WHERE clause still shows it when no filter is set.
2. **Use `[[ AND ... ]]` for optional filters.** Datasette drops the entire bracketed block when the named parameter isn't bound, so `[[ AND topic_id = :topic ]]` becomes nothing on cold-start and becomes `AND topic_id = 1` when the dropdown is set.
3. **Sentinel-row UNION for any table that might be empty.** `library: table` calls `Object.keys(data.rows[0])` to introspect columns and crashes (`TypeError`) when the result is empty. Every table panel that *could* return zero rows wraps the underlying query in a CTE and UNIONs a placeholder row. See `gone-quiet` for the canonical pattern.
4. **Escape user-supplied content before HTML concatenation.** The table renderer assigns `innerHTML = col`, so a `summary` field containing `<script>` would execute. Strip `<` `>` `\` from free-text fields and `replace(field, '"', '%22')` for URLs before building anchors. The word-cloud panel and the resurfacing/top-posts anchor cells show both patterns.

These rules also live verbatim in [`CLAUDE.md`](../CLAUDE.md) under "Dashboards conventions" so future agents don't re-discover them.

---

## The 5 chart libraries (and what each is for)

`datasette-dashboards` 0.8.0 ships exactly five renderers. There is **no `wordcloud` library** — see the README's substitution note for the workaround.

| Library | Use it for | Display block expects |
|---|---|---|
| `metric` | A single hero number. Orientation only, no drill-down. | `field: <column>`, optional `prefix`, `suffix` |
| `vega-lite` | Almost every chart. Line, bar, area, scatter, stacked area, faceted small-multiples. | `mark` + `encoding` (vega-lite spec format) |
| `vega` | Anything vega-lite can't express (custom layouts, transforms, the wordcloud transform). | Full vega spec keys (data, scales, marks, signals) |
| `table` | Tabular drill terminus, sorted lists, sized HTML spans. | No display block needed; columns come from SQL `AS` aliases |
| `map` | Geographic point/region overlays via Leaflet. | Latitude/longitude columns; not used in this dashboard |

If you're tempted to reach for a chart that doesn't fit one of these — first check whether vega-lite's `mark: text` + manual layout solves it (a "tag list" is `mark: text` with `encoding.size`). If not, full vega will handle it but redeclare `data` with care because `renderVegaChart` spread-merges and your `data` array overrides the auto-injected one.

---

## How to add a new panel — 5 steps

Worked example: add a "**Top authors**" panel showing the most-frequent authors across recent findings.

**Step 1 — Write the SQL** against research.db, applying the 4 hard rules:

```sql
SELECT author,
       COUNT(*) AS posts,
       ROUND(SUM(engagement_score), 1) AS total_engagement,
       date(MAX(first_seen)) AS most_recent
FROM findings
WHERE author IS NOT NULL
  AND topic_id != 2 AND dismissed = 0       -- rule 1
  [[ AND topic_id = :topic ]]                -- rule 2 (optional filter)
  [[ AND first_seen >= date(:date_start) ]]
GROUP BY author
HAVING posts > 1                             -- skip one-shot authors
ORDER BY total_engagement DESC
LIMIT 20
```

**Step 2 — Run the dry-run script** to confirm SQL is valid:

```bash
python dashboards/scripts/sql-dryrun.py
```

The script (1) parses `dashboards/trends.yaml`, (2) strips the `[[ AND ... ]]` blocks, (3) executes each panel's SQL, (4) reports row counts. If your new SQL has a syntax error or returns zero rows, you'll see it before launching datasette.

**Step 3 — Add the panel block** to `trends.yaml`'s `charts:` section:

```yaml
        top-authors:
          title: Top authors (by engagement, last 30 days)
          db: research
          query: |
            SELECT author, COUNT(*) AS posts, ROUND(SUM(engagement_score), 1) AS total_engagement, date(MAX(first_seen)) AS most_recent
            FROM findings
            WHERE author IS NOT NULL
              AND topic_id != 2 AND dismissed = 0
              [[ AND topic_id = :topic ]]
              [[ AND first_seen >= date(:date_start) ]]
            GROUP BY author
            HAVING posts > 1
            ORDER BY total_engagement DESC
            LIMIT 20
          library: table
```

If the result might be empty, use the `gone-quiet` UNION-sentinel pattern (rule 3).

**Step 4 — Add the panel to the layout grid AND reorder chart definitions if needed:**

```yaml
      layout:
        - [findings-this-week, findings-this-week, buzz-by-day, buzz-by-day, buzz-by-day, buzz-by-day]
        - [word-cloud, word-cloud, word-cloud, word-cloud, word-cloud, word-cloud]
        - [lifecycle-bubble, lifecycle-bubble, lifecycle-bubble, lifecycle-bubble, lifecycle-bubble, lifecycle-bubble]
        - [source-mix, source-mix, source-mix, source-mix, source-mix, source-mix]
        - [trend-matrix, top-authors, top-authors, top-authors, trend-matrix, trend-matrix]  # NEW panel paired with trend-matrix
        - [resurfacing, resurfacing, resurfacing, resurfacing, resurfacing, resurfacing]
        - [top-posts, top-posts, top-posts, top-posts, top-posts, top-posts]
        - [gone-quiet, gone-quiet, gone-quiet, gone-quiet, gone-quiet, gone-quiet]
```

The grid is 6 columns wide; each row of the YAML is a row of the dashboard, repeating an id N times to make a panel N columns wide. `[a, a, a, b, b, b]` = two equal-width panels in one row.

**Important plugin quirk**: datasette-dashboards 0.8.0 honors the `layout` block for **intra-row pairing** (which charts share a row) but **renders rows in chart-definition order**, not in layout-array order. To move a panel to a different ROW, you must also reorder its definition in the `charts:` block. The `layout` block alone is not sufficient — verify by hitting `/-/metadata.json` if a layout edit appears to do nothing.

**Step 5 — Smoke-test the rendered dashboard:**

```bash
datasette "$HOME/.local/share/last30days/research.db" -m dashboards/trends.yaml --port 8002
```

Open http://localhost:8002/-/dashboards/last30days-trends, hit F12 → Console, refresh. Zero error-level entries means the panel rendered. If you see `TypeError: Cannot convert undefined or null to object`, you forgot rule 3 (sentinel-row UNION).

That's it. Total time: ~10 minutes for a straightforward panel, longer if the SQL needs iteration.

---

## The click-to-rescope contract (URL keyword pattern)

Every clickable element in the dashboard navigates the browser to the same URL with `?keyword=<word>` appended. Datasette interprets that as a binding for the `:keyword` parameter, which is referenced by every panel's optional-WHERE block. The dashboard re-renders with all panels scoped to findings whose `source_title` matches that word (substring, case-insensitive).

Three places consume the contract — keep them in lockstep when adding new panels:

1. **Filter declaration** (`filters.keyword:` block at the top of the dashboard): `name: Keyword`, `type: text`. Without this block, the URL parameter is read but no input renders for clearing/escaping the scope.
2. **Optional-WHERE on each panel** (rule 2 in this doc): `[[ AND source_title LIKE '%' || :keyword || '%' ]]`. Add this to every new panel that should respect the scope. `gone-quiet` is exempt because it operates on run-level (not finding-level) data; a title-substring filter would lose its meaning.
3. **Click sources** (the things that build the URL):
   - **Wordcloud**: rendered via `library: vega` + the wordcloud transform with `interactive: true` set explicitly at the mark level. The SQL emits three columns — `word`, `weight`, `href` — and the encoding binds `href: { field: href }` in both `enter` and `update`. Vega installs a runtime click handler at the View instance level; clicking a `<text>` element fires the handler which navigates to the item's `href`. Hover tooltips work the same way (set `tooltip: { signal: "..." }` on `enter`). Because vega's expression language doesn't expose `encodeURIComponent`, the URL is pre-built in SQL with `replace()` chains escaping `#` `&` `?` and space. Important diagnostic note: the rendered SVG has zero `<a>` wrappers — that's expected. The clicks are runtime JS, not parse-time DOM. Don't use a static DOM probe (Playwright's `evaluate` looking for `<a>` elements) to test clickability; use `browser_click` on a `text` element and verify the URL changes.
   - **Table cells** (resurfacing, top-posts): an extra column built in SQL — see the `keyword_src` CTE in those panels for the canonical pattern. Strip the leading article ("The "/"A "/"An "), lowercase, take the first word, percent-escape URL-unsafe chars (`#` `&` `?` space), wrap in `<a href="?keyword=…">explore</a>`. Pop the resulting cell out as a separate column instead of replacing the title — the brainstorm explicitly favors a discoverable "explore" action over making the entire title cell click-rescope, so the existing "open" external-source link stays visible.

Two things to know that aren't obvious:

- **Filters do not compose across clicks.** Vega builds the navigation URL from the literal `href` field — `topic` and `date_start` reset to defaults every time a user clicks. Filter preservation requires either a vega signal that reads `window.location.search` and merges, or building the merged URL into the SQL `href` column with the current params already encoded. Both deferred. Mention this in the dashboard description so users aren't surprised.
- **Empty result sets crash table panels.** When `:keyword` matches zero findings, the table renderer crashes on `Object.keys(data.rows[0])`. Every new `library: table` panel that respects the keyword filter MUST handle this — `resurfacing` and `top-posts` use a `shaped` CTE + `UNION ALL` sentinel row guarded by `WHERE (SELECT COUNT(*) FROM shaped) = 0`. Vega/vega-lite chart panels (`word-cloud`, `buzz-by-day`, `source-mix`, `trend-matrix`) handle empty data gracefully and don't need sentinels — vega just renders nothing, no crash.

---

## How to modify the stopword list

The word-cloud panel inlines a `NOT IN (...)` stopword list at `dashboards/trends.yaml`. The list is split into three labeled groups by SQL comment:

```sql
-- English glue / common short words ----------------------
'about','above','after','again', ...
-- Source names (mirrors skills/last30days/scripts/lib/sources)
'reddit','tiktok','youtube', ...
-- Engine-jargon: relevance/scoring tokens leaked from prompt outputs
-- (mirrors skills/last30days/scripts/lib/relevance.* prompts; review
-- when prompt wording changes since stale entries silently regress).
'fallback','local','score','irrelevant','offtopic', ...
```

When to add a word:

- **It dominates the cloud and isn't a domain word.** Run the dashboard, eyeball the top 10, ask "would a marketer click on this?" If no, add to the relevant group.
- **A new source is added to the engine.** Add the source name (e.g. `'farcaster'`) to the second group.
- **A prompt is reworded** (e.g. relevance prompts switch from "irrelevant" to "tangential"). Add the new vocabulary to the third group.

When **not** to add a word:

- It's a domain word that legitimately matters (`'condo'`, `'mortgage'`, `'rates'`). The cloud is supposed to surface those.
- It's transiently noisy because the corpus is small. Wait for 2-3 weeks of data before pruning aggressively.

There's no automated test that catches drift; this is on you. A reasonable cadence: re-eyeball the cloud monthly, prune any new noise, document the prune in a commit message.

---

## Tuning the tag-cloud font sizes (worked example)

The current word-cloud SQL uses a fixed cap:

```sql
'<span style="font-size:' ||
  (12 + CAST(min(weight * 0.4, 36.0) AS INTEGER)) ||
  'px; ...">' ||
  word || '</span>'
```

At today's corpus (256 findings) **every** word in the top 80 has `weight >= 90`, which means `weight * 0.4 >= 36`, which means `min(weight*0.4, 36) = 36`, which means **every word is 48px**. The cap eats all the differentiation.

Two ways to fix:

**A) Proportional scaling within the result set** (recommended):

```sql
WITH ranked AS (
  SELECT word, weight, frequency,
         (SELECT MAX(weight) FROM counted) AS max_w,
         (SELECT MIN(weight) FROM counted ORDER BY weight DESC LIMIT 80) AS min_w
  FROM (SELECT word, weight, frequency FROM counted ORDER BY weight DESC LIMIT 80)
)
SELECT GROUP_CONCAT(
  '<span style="font-size:' ||
    (12 + CAST(36.0 * (weight - min_w) / NULLIF(max_w - min_w, 0) AS INTEGER)) ||
    'px;...">' || word || '</span>',
  ''
) FROM ranked
```

The biggest word is always 48px (12+36), the smallest is always 12px, and the rest scale linearly between. Independent of corpus size.

**B) Logarithmic scaling** (better when weight distribution has a long tail):

```sql
12 + CAST(36.0 * log(weight) / log(max_w) AS INTEGER)
```

SQLite's `log()` is natural log; ratios are what matter so the base doesn't.

This isn't a P0 — the words are still readable today, just not visually differentiated. Worth fixing once the corpus reaches ~5x current size and the weights actually spread.

---

## Smoke test before committing

Three layers of smoke test, in order of cost:

1. **SQL dry-run** (~1 second): `python dashboards/scripts/sql-dryrun.py`. Catches syntax errors and zero-row tables.
2. **Curl smoke** (~5 seconds): `datasette ... --port 8002 &; curl -sS -L http://localhost:8002/-/dashboards/last30days-trends | grep -c chart-`. Should match the panel count. Catches "panel id missing from rendered HTML."
3. **Visual smoke** (~30 seconds): open http://localhost:8002/-/dashboards/last30days-trends in your browser, F12 → Console, refresh. Zero errors. Catches the things HTTP 200 misses — empty-table crashes, vega-lite encoding bugs, JS lifecycle bugs.

The dark-factory overnight run that built this dashboard found two P0 bugs at layer 3 that layer 2 missed (sentinel-row UNION + tokenization bug). Don't skip layer 3 when you change SQL. Layer 1 + 2 is fine for cosmetic changes (renaming a column, tweaking a layout grid).

---

## Three places to look first when something breaks

| Symptom | Likely cause | Fix |
|---|---|---|
| Whole panel shows a JS console `TypeError: Cannot convert undefined or null to object` | Table panel returned 0 rows; renderer crashed on `Object.keys(data.rows[0])` | Add a sentinel-row UNION (rule 3) |
| Word cloud renders a single word that's a full sentence | The `replace(s, ' ', '","')` step in the wordcloud SQL got skipped | Re-check the `cleaned` and `arr` CTEs; the space → `","` replace must come *after* punct stripping |
| Anchor cells render as broken HTML (`>open<` showing as text) | A `"` survived in `source_url` and broke the surrounding `<a href="...">` | Add `replace(source_url, '"', '%22')` to the cell expression |
| Filter dropdown changes don't propagate to a panel | Panel SQL forgot the `[[ AND topic_id = :topic ]]` block | Add the optional-WHERE block to the WHERE clause |
| Datasette won't see new findings without a restart | Rare on Linux/macOS, occasional on Windows when SQLite locking gets weird | Stop datasette (`taskkill //F //PID <pid>`) and relaunch |
| YAML edits to `dashboards/trends.yaml` don't take effect after save | Datasette caches metadata at startup — the running process is using the YAML it loaded when it launched | Kill the datasette process and relaunch. Verify the new metadata is live by hitting `http://localhost:8002/-/metadata.json` — if your changes don't appear there, the old process is still serving |
| Datasette crashes on startup with `UnicodeDecodeError: 'charmap' codec can't decode byte` (Windows) | Python defaults to the system codec (cp1252 on Windows) when reading the metadata YAML. Any non-ASCII character — em-dash, smart quotes, arrows, accented letters — triggers this | Set `PYTHONUTF8=1` before the datasette command: `PYTHONUTF8=1 datasette ... -m dashboards/trends.yaml --port 8002`. As a fallback, keep the YAML strictly ASCII (use ` - ` instead of `—`, `<-` instead of `←`, etc.) |
| Reordering rows in the `layout:` block has no effect on row order | datasette-dashboards 0.8.0 uses `layout:` for intra-row pairing only; row sequence follows chart-definition order in the `charts:` block | Reorder the chart definitions themselves in the YAML to match the desired visual row order. The `layout:` array still controls which charts share a row (and column widths within a row) |
| Today's findings show up under tomorrow's date | SQLite `datetime('now')` is **UTC**, not local; the engine stores UTC. An 8 PM EDT ingestion stamps `2026-05-07 00:00 UTC` and gets bucketed to "tomorrow" if the panel uses bare `date(first_seen)` | Wrap every date column in `date(col, 'localtime')` for display; compare with `date('now', 'localtime', '-N days')` on **both** sides of the comparison. Storage stays UTC (correct as a canonical convention); display localizes |
| Clicking a word in the cloud goes nowhere | The wordcloud uses `library: vega` + the wordcloud transform with `interactive: true` at the mark level — vega installs the click handler at runtime via JS, not via SVG `<a>` wrappers, so the rendered SVG stays clean (just `<text>` elements). If your DOM inspector shows zero `<a>` tags inside the cloud, that's normal — clicks still fire. Real symptom: the URL doesn't change when you click. Likely cause: the `href` channel isn't bound to the data column, or `interactive: true` was dropped from the mark | Confirm `marks[0].interactive: true` is set, the SQL emits a `href` column (build it with `replace()` chains in SQL — vega's expression language doesn't expose `encodeURIComponent`), and the encode block has `href: { field: href }` in both `enter` and `update`. Click test in a real browser (NOT a static DOM probe — Playwright's headless DOM inspection won't see vega's runtime click handlers) |
| `?keyword=fakekeyword_no_results` crashes the `resurfacing` or `top-posts` panel with `TypeError: Cannot convert undefined or null to object` | The keyword filter narrowed the result set to zero rows; the table renderer crashed on `Object.keys(data.rows[0])` (rule 3 again — the same crash mode that hits `gone-quiet`). Pre-keyword, these panels couldn't return zero rows so the sentinel pattern wasn't needed | Wrap the main SELECT in a `shaped` CTE and `UNION ALL` a sentinel row guarded by `WHERE (SELECT COUNT(*) FROM shaped) = 0`. Mirror the column count + types of the real query so the renderer sees the same column-key surface |
| Clicking a word resets the `topic` and `From date` filters | The vega `href` signal builds a fresh URL (`'?keyword=' + ...`) instead of merging into the current `window.location.search`, so other filters reset to defaults | Known v1 limitation. Filter preservation needs a vega signal that reads `window.location.search` and re-emits the merged query string — deferred to a future iteration. Workaround: the user re-applies their `topic` / `date_start` after a click |

---

## Roadmap — toward design discipline

This dashboard is a strong v1. It implements the high-leverage information-design principles that separate working dashboards from data dumps: a BAN metric in the orientation slot, glance-scan-elaborate hierarchy, visual-first row order, honest titles that don't oversell the data, sparse-by-design panels labeled as such. Three established practices remain unimplemented and are tracked here as the next iteration boundary.

### Next chapter: storytelling with data

Static chart titles describe what the chart plots. Great titles state what the reader should take away from the plot. The difference looks small in writing and large in practice:

| Title style | Example |
|---|---|
| Descriptive (current) | "Buzz by day" |
| Storytelling (next) | "Toronto real estate spiked May 9 — biggest single-day count this month" |

Implementing storytelling titles requires the title to update with the data, which means computing the headline phrase in SQL and binding it to a metric panel near the top of the dashboard. The pattern is not trivial — it requires deciding what counts as "the story" for any given week — but the payoff is a dashboard that tells the marketer what to act on without requiring them to read every chart.

A reasonable first step: add a `daily-headline` metric panel between row 1 and row 2, whose query selects the most notable (topic, day) pair from the last 7 days and renders it as `"<topic> spiked <date> — <volume> findings"`. The SQL is straightforward; the editorial judgment about what "notable" means is the real work.

### Next chapter: color discipline

The current dashboard uses Vega's default categorical palette — every topic gets a distinct color. This produces visual noise: lots of color, little signal. The discipline favored by Knaflic and others is grayscale baseline with one accent color reserved for the metric or topic you want the reader's eye drawn to. Apply this by:

1. Setting an explicit `color: { value: "#888" }` (gray) as the default in vega-lite encodings
2. Conditionally overriding to an accent color when a topic crosses a threshold (e.g. `topic == 'Toronto real estate'` for the dashboard's primary beat, or `growth_ratio < 0.5` for falling topics)

This converts color from decoration into emphasis — a single red bubble in a sea of gray reads louder than 9 differently-colored bubbles.

### Next chapter: audience separation

A dashboard serving two audiences splits its design attention between them. The current dashboard tracks both client-domain research (real estate beats) and ecosystem signal (the skill's own discoverability). These two audiences read the dashboard differently and respond to different metrics. The principled split is to clone `trends.yaml` to `client-research.yaml` and `ecosystem-tracking.yaml`, each tuned to its scenario with different filter defaults, different panel emphasis, and different titles. This is straightforward mechanically but requires resolving which panels go to which dashboard — worth doing once usage patterns substantiate two distinct read habits.

### Deferred technical work

These are tracked in [PR #2 open questions](https://github.com/last30days-skill/pull/2) for when the data demands them:

1. **Wordcloud SQL at 50k findings.** The 5-CTE chain + `json_each` explosion will be the tail-latency outlier. The fix is a materialized `findings_words` precompute table refreshed on insert. Worth doing once the corpus hits ~10k findings and you can measure actual slowness.
2. **Index on `findings(dismissed, first_seen)`.** Six of nine panels do range scans on `first_seen`. The schema is owned by the engine (`scripts/store.py`), not the dashboard. Worth a separate engine-side PR.
3. **`gone-quiet` correlated-subquery rewrite.** Four correlated subqueries per row → straight GROUP BY + JOINs. ~30 lines of SQL refactor, not blocking at current corpus size.
4. **Stopword list drift detector.** No automated check links the inline source/jargon stopwords to the engine modules they mirror. A 5-line python test could parse the YAML and assert the source-name list ⊆ `lib/sources` enum. Worth adding when any other Python testing is added.
5. **Cross-panel filter parameters.** Clicking a word in the cloud should compose with the existing `topic` / `date_start` filters rather than resetting them. Currently each click rebuilds the URL from scratch.

---

## Related

- [`README.md`](README.md) — launch + filter + panel descriptions (user-facing)
- [`../CLAUDE.md`](../CLAUDE.md) — `Dashboards conventions` block (agent-facing)
- [`scripts/sql-dryrun.py`](scripts/sql-dryrun.py) — automated SQL smoke test
- *The Big Book of Dashboards* (Wexler, Shaffer, Cotgreave, Wiley 2017) — the source for the BAN, glance-scan-elaborate, and quadrant-scatter patterns used in this dashboard
- *Storytelling with Data: Let's Practice!* (Knaflic, Wiley) — the source for the title-as-takeaway, color discipline, and clutter-elimination practices in the Roadmap
- [datasette-dashboards 0.8.0](https://github.com/rclement/datasette-dashboards) — upstream plugin
