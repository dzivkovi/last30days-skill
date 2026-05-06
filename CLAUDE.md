# last30days Skill

Claude Code skill for researching any topic across Reddit, X, YouTube, and web.
Python scripts with multi-source search aggregation.

## Structure
- `skills/last30days/SKILL.md` — canonical skill definition
- `skills/last30days/scripts/last30days.py` — main research engine
- `skills/last30days/scripts/lib/` — search, enrichment, rendering modules
- `skills/last30days/scripts/lib/vendor/bird-search/` — vendored X search client
- `dashboards/` — datasette-dashboards YAML over `~/.local/share/last30days/research.db`

## Commands
```bash
python3 skills/last30days/scripts/last30days.py "test query" --emit=compact
bash skills/last30days/scripts/sync.sh
datasette "$HOME/.local/share/last30days/research.db" -m dashboards/trends.yaml --port 8002
```

## Rules
- `lib/__init__.py` must be bare package marker (comment only, NO eager imports)
- After edits: run `bash skills/last30days/scripts/sync.sh` to deploy
- Git remote: origin = public (`mvanhorn/last30days-skill`)

## Dashboards conventions

- **datasette-dashboards 0.8.0 ships only 5 chart libraries**: `vega`, `vega-lite`, `metric`, `table`, `map`. There is **no `wordcloud` renderer** — emulate via `library: table` with HTML `<span style="font-size:Npx">…</span>` cells (the table renderer assigns `innerHTML = col`, so inline styles render). See `dashboards/trends.yaml` word-cloud panel for the canonical pattern.
- Every panel that filters on `findings` MUST include `AND topic_id != 2 AND dismissed = 0` to skip the "test topic" noise + soft-deleted rows.
- Tables that may return 0 rows MUST UNION a sentinel row — `renderTableChart` calls `Object.keys(data.rows[0])` and crashes when `data.rows` is empty.
- Inline HTML in SQL cells (anchors, styled spans) is intentional and depends on `innerHTML` rendering. Always escape `"` in user-supplied URL-style fields via `replace(col, '"', '%22')` and strip `<` / `>` from any field that might contain HTML before concatenation.

## Beta channel

Experimental changes get tested on `mvanhorn/last30days-skill-private`, which installs as a parallel `/last30days-beta` slash command. Beta-only changes never ship to public without a review PR here. Workflow guide lives at `BETA.md` in the private repo. Plan that established this setup: `docs/plans/2026-04-17-005-feat-beta-skill-from-private-repo-plan.md`.
