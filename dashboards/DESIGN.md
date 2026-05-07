# `dashboards/` — design notes & customization guide

Companion to [`README.md`](README.md). The README tells you *how to launch* the dashboard; this file tells you *why it looks like this* and *how to change it without breaking the contract*.

Audience: future-you (or a teammate / client) wanting to add a panel, swap a chart, prune the stopwords, or borrow this pattern for a different topic. Assumes you can read SQL and YAML; does not assume you've read the upstream `datasette-dashboards` source.

---

## Mental model — every chart is a navigation widget

The original framing came from research on commercial trend-sensing tools (Brandwatch, Talkwalker, Sprout Social, Brand24, Meltwater). Their dashboards converged on six visual idioms, and the unifying observation was: **every chart is an entry point into the underlying findings, not a standalone report**. Click a spike on the volume line → see the posts that caused it. Click a word in the cloud → filter the table to findings using that word. Click a topic in the matrix → see why it's hot.

That's the right mental model for a non-BI marketing user. They aren't reading the dashboard; they're using it as *a table of contents for the text*. Every panel earns its place by answering one question in five seconds and offering a click into specifics.

If you're adding a new panel, the test is: **can a marketer answer a specific question in 5 seconds and click into more detail?** If not, it doesn't belong here — it belongs in a SQL notebook or a longer-form digest.

---

## Why these 8 panels (and not others)

| # | Panel | Question answered in 5s | Why it earns its slot |
|---|---|---|---|
| 1 | Findings this week | "Did anything happen?" | Hero metric. Orientation. One look tells you whether to keep scrolling. |
| 2 | Buzz by day | "Heating or cooling, per topic?" | Volume-over-time is the spine of every commercial dashboard. Multi-line by topic so you see relative shape. |
| 3 | Source mix over time | "Where's the conversation moving?" | Stacked area answers "is this trend Reddit-specific, or is it everywhere?" Clip a slice in your head; it's the platform-shift signal. |
| 4 | Resurfacing content | "What stories keep coming back?" | `sighting_count > 1` rows. Same article showing up across feeds = compounding signal. The single highest-leverage panel for a marketer deciding what to amplify. |
| 5 | Trend matrix ⭐ | "Which topic is *emerging*?" | 2D scatter (volume × growth-rate). Quadrant model: top-right hot, top-left emerging, bottom-right established, bottom-left fading. Becomes meaningful at week 2. |
| 6 | Engagement-weighted top words | "What vocabulary dominates?" | Clickable navigation into the language of the corpus. Sized by `SUM(engagement)`, not raw frequency — high-engagement words are what's worth amplifying. |
| 7 | Top posts | "Show me the actual content" | The drill terminus. Every other panel leads here. Sorted by `sighting_count, engagement` — recurring content first, viral one-hits second. |
| 8 | Gone quiet | "Where did the conversation stop?" | Codex's surprise contribution. Silence ahead of an announcement is sometimes the strongest signal a marketer can act on. Becomes meaningful at week 3. |

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

**Step 4 — Add the panel to the layout grid:**

```yaml
      layout:
        - [findings-this-week, findings-this-week, buzz-by-day, buzz-by-day, buzz-by-day, buzz-by-day]
        - [source-mix, source-mix, source-mix, source-mix, source-mix, source-mix]
        - [resurfacing, resurfacing, resurfacing, trend-matrix, trend-matrix, trend-matrix]
        - [top-authors, top-authors, top-authors, word-cloud, word-cloud, word-cloud]   # NEW row
        - [top-posts, top-posts, top-posts, top-posts, top-posts, top-posts]
        - [gone-quiet, gone-quiet, gone-quiet, gone-quiet, gone-quiet, gone-quiet]
```

The grid is 6 columns wide; each row of the YAML is a row of the dashboard, repeating an id N times to make a panel N columns wide. `[a, a, a, b, b, b]` = two equal-width panels in one row.

**Step 5 — Smoke-test the rendered dashboard:**

```bash
datasette "$HOME/.local/share/last30days/research.db" -m dashboards/trends.yaml --port 8002
```

Open http://localhost:8002/-/dashboards/last30days-trends, hit F12 → Console, refresh. Zero error-level entries means the panel rendered. If you see `TypeError: Cannot convert undefined or null to object`, you forgot rule 3 (sentinel-row UNION).

That's it. Total time: ~10 minutes for a straightforward panel, longer if the SQL needs iteration.

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

---

## Out of scope (deferred architectural items)

These are tracked in [PR #2 open questions](https://github.com/dzivkovi/last30days-skill/pull/2). They're listed here so a future maintainer doesn't think they're forgotten — they're explicit deferrals waiting for the data to demand them:

1. **Wordcloud SQL at 50k findings.** The 5-CTE chain + `json_each` explosion will be the tail-latency outlier. The fix is a materialized `findings_words` precompute table refreshed on insert. Worth doing once the corpus hits ~10k findings and you can measure actual slowness.
2. **Index on `findings(dismissed, first_seen)`.** Six of eight panels do range scans on `first_seen`. The schema is owned by the engine (`scripts/store.py`), not the dashboard. Worth a separate engine-side PR.
3. **`gone-quiet` correlated-subquery rewrite.** Four correlated subqueries per row → straight GROUP BY + JOINs. ~30 lines of SQL refactor, not blocking at 259 findings.
4. **Stopword list drift detector.** No automated check links the inline source/jargon stopwords to the engine modules they mirror. A 5-line python test could parse the YAML and assert the source-name list ⊆ `lib/sources` enum. Worth adding when any other Python testing is added.
5. **Cross-panel filter parameters.** Clicking a word in the cloud should filter top-posts to findings containing that word. Currently each panel has independent state.

---

## Related

- [`README.md`](README.md) — launch + filter + panel descriptions (user-facing)
- [`../CLAUDE.md`](../CLAUDE.md) — `Dashboards conventions` block (agent-facing)
- [`scripts/sql-dryrun.py`](scripts/sql-dryrun.py) — automated SQL smoke test
- `../work/2026-05-05/17-trends-dashboard-synthesis-three-agents.md` — research that informed the panel selection
- `../work/2026-05-05/18-overnight-dark-factory-primer.md` — the spec the build executed against
- [datasette-dashboards 0.8.0](https://github.com/rclement/datasette-dashboards) — upstream plugin
