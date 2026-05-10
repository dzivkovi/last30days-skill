# Forking and customizing /last30days

When you're developing changes against the engine — testing a fix, building a private variant, or staging a contribution upstream — you want the `/last30days:last30days` slash command to invoke **your** code, not the marketplace-installed version. Claude Code's native plugin commands handle this without manual file copying.

**Mental model:** the marketplace name `last30days-skill` is your local handle — like `localhost`. It doesn't change. What changes is which repo it routes to (upstream or your fork). Your fork is your daily driver; you re-route only when you genuinely need pristine upstream (rare).

The example below uses `dzivkovi/last30days-skill@daniel/personal` (the fork [PR #344](https://github.com/mvanhorn/last30days-skill/pull/344) was developed on). Substitute your own `<owner>/<repo>@<branch>`.

## One-time setup — register your fork as the daily driver

```text
/plugin uninstall last30days
/plugin marketplace add dzivkovi/last30days-skill@daniel/personal
/plugin install last30days
/reload-plugins
```

## Iterating on your fork — push, then reinstall to pick up code changes

```bash
git push origin daniel/personal
```

```text
/plugin uninstall last30days
/plugin marketplace update last30days-skill
/plugin install last30days
/reload-plugins
```

**The `/plugin uninstall` step is required, not optional, for engine code (`lib/*.py`, `last30days.py`) changes to take effect.** Claude Code installs the plugin to a versioned cache dir at `~/.claude/plugins/cache/last30days-skill/last30days/<version>/` and runs from there. `/plugin marketplace update` only refreshes the marketplace clone at `~/.claude/plugins/marketplaces/last30days-skill/`; `/reload-plugins` re-imports modules from the cache. Neither command syncs the marketplace clone *into* the cache. Only uninstall+install rebuilds the cache.

For prompt-only edits (`SKILL.md` text changes with no Python touched), the lighter `marketplace update` + `reload-plugins` pair *may* be sufficient depending on Claude Code's cache rules — but if you're not sure, do the full reinstall. It's under a second.

## Verify the active version after reinstall

```bash
python -c "
import json, os
data = json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))
for k, v in data['plugins'].items():
    if 'last30days' in k.lower():
        print(json.dumps({k: v}, indent=2))
"
```

`gitCommitSha` is the truth: it's the commit the cache install was actually built from. Compare to your fork's `git rev-parse HEAD` — if they don't match, the install didn't propagate. Two failure shapes to recognize:

- **Stale `installedAt`** — the reinstall didn't trigger; rerun the four-command block.
- **Fresh `installedAt` + stale `gitCommitSha`** — the marketplace clone wasn't pulled. `/plugin marketplace add` on an already-registered marketplace is silently idempotent (prints "Successfully added" but does NOT `git pull`); make sure the iteration block uses `marketplace update`, not `add`. Quick manual fix without re-running everything: `git -C ~/.claude/plugins/marketplaces/last30days-skill pull`, then redo just `/plugin uninstall` + `/plugin install` + `/reload-plugins`.

**Why this matters:** "did the engine pick up my fix?" is otherwise unanswerable from inside Claude Code. The skill's bash invocation hard-codes `SKILL_ROOT="/c/Users/<you>/.claude/plugins/cache/last30days-skill/last30days/<version>/skills/last30days"` — that path is printed in the skill's own bash output, so a quick scroll-up after a run confirms which version actually ran.

## Re-routing to upstream pristine (rare; for adversarial testing)

For "did my patch regress something?" checks:

```text
/plugin uninstall last30days
/plugin marketplace remove last30days-skill
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
/reload-plugins
```

To come back: same five lines, with your fork's `<owner>/<repo>@<branch>` in step 3. Both upstream and your fork register under the same internal marketplace name, so only one is active at a time. This is intentional: keep the install command (`last30days`) constant and re-route the source. The CLI auto-cleans the marketplace cache on `remove`, so no manual cleanup is needed.

## Offline alternative (rare; iterating without pushing)

Point the marketplace at a local clone path instead of the GitHub URL — `/plugin marketplace add /absolute/path/to/your/cloned/repo`. Every `git commit` is then visible to `/plugin marketplace update` without a push.

---

## See also

- [`CONFIGURATION.md`](CONFIGURATION.md) — env vars, API keys, per-run flags, per-client patterns.
- [`SKILL.md`](skills/last30days/SKILL.md) — the runtime contract the engine follows.
- [`docs/solutions/`](docs/solutions/) — documented past problems and design patterns.
