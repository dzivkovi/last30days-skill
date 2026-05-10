# Configuration

Everything you can tune in `/last30days` without editing the engine source.
Three layers, in order of how often you'll touch them:

1. **Per-run flags** - what you pass on the command line.
2. **Environment variables and `.env`** - what's enabled across all runs.
3. **Optional trend-monitoring stack** - SQLite store, watchlist, briefings.

Per-client patterns and the experimental beta channel are at the bottom.

> Skip ahead: [Where output is saved](#where-output-is-saved) - [API keys](#api-keys-env) - [Reasoning provider](#reasoning-provider-priority) - [Web search backend](#web-search-backend-priority) - [Trend monitoring](#trend-monitoring-store--watchlist--briefings) - [Per-client patterns](#per-client-patterns) - [Beta channel](#beta-channel)

## Why this document exists

This is a focused **configuration reference** maintained alongside the engine. The runtime contract (the voice rules, the planner protocol, the LAWs the synthesizing model follows) lives in [`skills/last30days/SKILL.md`](skills/last30days/SKILL.md) - that file is authoritative when the two ever differ. This file's job is narrower: surface every knob a user or operator can turn, in one place, kept current with the code so client-facing setups stay reliable. New configuration knobs added to the engine should be reflected here in the same PR.

---

## Where output is saved

| Platform | Default path | Override |
|---|---|---|
| Linux / macOS | `~/Documents/Last30Days/` | `LAST30DAYS_MEMORY_DIR=/path` |
| Windows | `C:\Users\<you>\Documents\Last30Days\` | `LAST30DAYS_MEMORY_DIR=C:\path` |

Each run produces one file per topic, slug-named:
`<slug>-raw[-suffix].md`. Same topic + same suffix on the same day overwrites; same topic + same suffix on different days appends a date stamp.

**Per-run overrides:**
- `--save-dir <path>` - one-off output location.
- `--save-suffix <name>` - distinguish runs of the same topic (e.g. per client: `--save-suffix=acme`).

The footer line `📎 Raw results saved to ~/Documents/Last30Days/<slug>-raw.md` is the canonical pointer; if it shows backslashes on Windows update past v3.1.1.

---

## API keys (`.env`)

The skill reads keys from a `.env` file. Two locations are supported, in priority order:

1. **`.claude/last30days.env`** in the current project directory (project-scoped) - takes precedence when present.
2. **`~/.config/last30days/.env`** at the user level (global default) - the fallback.

Override the global location with `LAST30DAYS_CONFIG_DIR=/path` (or `LAST30DAYS_CONFIG_DIR=""` for no-config mode). File permissions should be `600` on POSIX hosts - the engine warns on every run if they aren't.

The project-scoped file is the cleanest pattern for **per-client setups**: drop a `.claude/last30days.env` into each client folder (`SCRAPECREATORS_API_KEY`, `INCLUDE_SOURCES`, `LAST30DAYS_MEMORY_DIR`, `BSKY_HANDLE`, etc), `cd` into that folder, and the skill picks up that client's configuration automatically. No wrapper scripts needed for the common case.

**Source-by-source** - what each key unlocks:

| Source | Key(s) | Required for | Free tier |
|---|---|---|---|
| Reddit (public) | none | always on | yes |
| Hacker News | none | always on | yes |
| Polymarket | none | always on | yes |
| GitHub | `gh` CLI installed (uses your GitHub auth) | always on if `gh` present | yes |
| YouTube | `yt-dlp` CLI installed | always on if `yt-dlp` present | yes |
| X / Twitter | one of: `AUTH_TOKEN` + `CT0` (browser cookies, Bird CLI), `XAI_API_KEY`, `SCRAPECREATORS_API_KEY`, or `FROM_BROWSER` (cookie-jar auth) | X items in results | cookie-jar / Bird = free; xAI / ScrapeCreators = paid |
| TikTok | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `tiktok` | TikTok items | 10K free calls |
| Instagram | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `instagram` | Instagram Reels | 10K free calls; raise `LAST30DAYS_TRANSCRIPT_TIMEOUT` (default 30s) if SC is slow on your network |
| Threads | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `threads` | Threads items | 10K free calls |
| Pinterest | `SCRAPECREATORS_API_KEY` + `INCLUDE_SOURCES` contains `pinterest` | Pinterest items | 10K free calls |
| Bluesky | `BSKY_HANDLE` + `BSKY_APP_PASSWORD` | Bluesky items | yes (app password at bsky.app) |
| TruthSocial | `TRUTHSOCIAL_TOKEN` | TruthSocial items | yes (browser dev-tools session bearer) |
| XQuik (X alt) | `XQUIK_API_KEY` | XQuik X-search alternative | paid (xquik.com) |
| Xiaohongshu | `XIAOHONGSHU_API_BASE` + `--sources xiaohongshu` flag | Xiaohongshu (Little Red Book) items | depends on endpoint |
| Web search | one of: `BRAVE_API_KEY`, `EXA_API_KEY`, `SERPER_API_KEY`, `PARALLEL_API_KEY` | `--auto-resolve` and Step 2 supplements | Brave has a free tier; native WebSearch on Claude Code / Codex / Gemini works as a fallback |
| Perplexity Sonar / Deep Research | `OPENROUTER_API_KEY` **and** `INCLUDE_SOURCES` contains `perplexity` | Perplexity items in pipeline runs (also unlocks `--deep-research` flag, ~$0.90/query) | no |
| Apify (alternate scraper) | `APIFY_API_TOKEN` | fallback for Reddit/TikTok/Instagram when ScrapeCreators is exhausted | yes (limited) |

**Note on X / Twitter:** the engine integrates with **xAI's** Grok-based search of X (`api.x.ai`, key `XAI_API_KEY`), NOT with Twitter's Developer Platform v2 API (`api.twitter.com`, bearer tokens). If you have a Twitter Developer Platform key, the engine ignores it. The supported X paths are: xAI search, bird-search scraper (`AUTH_TOKEN`+`CT0` cookies — `bird_authenticated: true` in `--diagnose`), `SCRAPECREATORS_API_KEY`, or `XQUIK_API_KEY`.

**Example `.env` skeleton** (placeholders only - replace with your own values):

```bash
# Reasoning + planning (one provider; see priority below)
GOOGLE_API_KEY=<your-gemini-key>

# Web search backend (one is enough; Brave is the cheapest)
BRAVE_API_KEY=<your-brave-key>

# Optional sources
SCRAPECREATORS_API_KEY=<your-scrapecreators-key>
INCLUDE_SOURCES=tiktok,instagram

# X authentication (one option only)
XAI_API_KEY=<your-xai-key>
# OR cookie-jar (no key needed; logs in via your browser session)
# FROM_BROWSER=firefox

# Bluesky
BSKY_HANDLE=<your-handle>.bsky.social
BSKY_APP_PASSWORD=<your-app-password>
```

After editing: `chmod 600 ~/.config/last30days/.env` (or `chmod 600 .claude/last30days.env` if using the project-scoped variant).

**Troubleshooting:** if a source you expected to see isn't appearing in results, run `python3 scripts/last30days.py --diagnose`. The output is a JSON report covering:

- `available_sources` — sources currently active for a run
- `optional_sources` — sources you could enable, with the env var(s) you'd need to set
- `gaps` — human-readable warnings about configured-but-broken state (e.g. "OPENROUTER_API_KEY is set but 'perplexity' is not in INCLUDE_SOURCES — Perplexity Sonar/Deep Research won't run")
- `auto_resolved_provider` — when `LAST30DAYS_REASONING_PROVIDER=auto`, which provider actually got picked. If this says `local` you're on the deterministic ranker (no LLM keys configured) — search quality will be visibly lower
- `providers`, `native_web_backend`, `bird_authenticated`, `has_scrapecreators`, `has_github` — boolean state per backend

### Bluesky app-password format and search host

`BSKY_APP_PASSWORD` should be a 19-char app password in `xxxx-xxxx-xxxx-xxxx` format (lowercase alphanumeric, three hyphens). Generate one at <https://bsky.app/settings/app-passwords>. The AT Protocol's `createSession` endpoint also accepts your main account login password, but that's bad hygiene — main passwords have no scope (an app password can be limited to non-DM access) and can't be revoked individually.

The skill defaults to `api.bsky.app` for `searchPosts`, which is the canonical authenticated AppView. The previous default `public.api.bsky.app` is the unauthenticated public mirror and is currently blocked by BunnyCDN for `searchPosts` regardless of auth header (verified 2026-05-04). If Bluesky migrates infrastructure again, override the host without a code change by setting `BSKY_SEARCH_HOST` in your `.env`:

```bash
BSKY_SEARCH_HOST=api.bsky.app   # default — change only if Bluesky moves
```

---

## Power-tuning knobs (`LAST30DAYS_*`)

These don't unlock new capabilities — they pin or override default behavior. All can be set in shell env or `.env` (the engine bridges `.env`-loaded values into `os.environ` at startup so module-level reads pick them up).

| Var | Effect | Default |
|---|---|---|
| `LAST30DAYS_DEBUG=1` | Enable `[DEBUG]` stderr logs across all modules | off |
| `LAST30DAYS_SKIP_PREFLIGHT=1` | Skip the topic-quality pre-flight gate | off (gate active) |
| `LAST30DAYS_REASONING_PROVIDER` | Pin to `gemini` / `openai` / `xai` / `openrouter` instead of auto-pick | `auto` |
| `LAST30DAYS_PLANNER_MODEL` | Override planner model name | provider-default (e.g. `gemini-3.1-flash-lite-preview`) |
| `LAST30DAYS_RERANK_MODEL` | Override reranker model name | provider-default |
| `LAST30DAYS_X_BACKEND` | Pin X search to `xai` or `bird` | auto-pick |
| `LAST30DAYS_X_MODEL` | Pin X-search model name (only when `X_BACKEND=xai`) | provider-default |
| `OPENAI_MODEL_PIN` / `XAI_MODEL_PIN` | Per-provider model pin (legacy alias) | provider-default |
| `LAST30DAYS_TRANSCRIPT_TIMEOUT` | Seconds to wait for IG/YT transcript fetch | 30 |
| `LAST30DAYS_STORE=1` | Persist findings to SQLite on every run (alternative to `--store` flag) | off |
| `LAST30DAYS_MEMORY_DIR` | Output directory for `<slug>-raw.md` files | `~/Documents/Last30Days/` |
| `LAST30DAYS_CONFIG_DIR` | Override `.env` directory; set to `""` for no-config mode | `~/.config/last30days/` |

For reproducible client runs, the highest-leverage pin is `LAST30DAYS_REASONING_PROVIDER=<name>` — defends against future config drift even if `auto` would resolve to the same provider today.

---

## Reasoning provider priority

`/last30days` needs one reasoning model for planning + reranking when you don't pass `--plan` yourself. Auto-detect priority (set `LAST30DAYS_REASONING_PROVIDER=<name>` to pin one):

1. **Gemini** - `GOOGLE_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_GENAI_API_KEY`
2. **OpenAI** - `OPENAI_API_KEY` (or Codex auth at `~/.codex/auth.json`)
3. **xAI** - `XAI_API_KEY`
4. **OpenRouter** - `OPENROUTER_API_KEY` (also unlocks `--deep-research`)
5. **Local / deterministic** - always available, lowest quality

When you invoke `/last30days` from Claude Code, Codex, or Gemini, the host model **is** the reasoning provider for plan + synthesis - you don't need any of the keys above unless you also run the script headlessly (cron, CI, watchlist).

---

## Web search backend priority

Used by `--auto-resolve` (when WebSearch isn't available from the host) and Step 2 supplements. Auto-detect priority (override per-run with `--web-backend=<name>`):

1. **Brave** - `BRAVE_API_KEY`
2. **Exa** - `EXA_API_KEY`
3. **Serper** - `SERPER_API_KEY`
4. **Parallel** - `PARALLEL_API_KEY`
5. **Host's native WebSearch** - Claude Code, Codex, Gemini all have one built in

Visible quality difference between hosts with vs without a configured backend. If your client setup produces thinner results than yours, this is usually why.

---

## Trend monitoring (`--store` + watchlist + briefings)

The default behavior - one slug-named file per topic, overwritten on rerun - is the snapshot mode. For continuous monitoring, the repo ships three components most users miss:

### `--store` flag

Adding `--store` to any run persists every finding to a SQLite database (default at `~/.local/share/last30days/research.db`). Findings dedupe on the `source_url` column (UNIQUE constraint), so the same URL across runs updates the existing row instead of creating a duplicate. The markdown file still saves; the SQLite is the time-series substrate.

**Always-on alternative:** set `LAST30DAYS_STORE=1` in your `.env` instead of remembering `--store` on every invocation. The flag still works as before; the env var is purely additive. Same hybrid pattern as `LAST30DAYS_DEBUG` — works whether shell-exported or in `.env`.

Relevant tables: `topics`, `research_runs`, `findings`, `settings`. Schema: [`scripts/store.py`](skills/last30days/scripts/store.py).

### `watchlist.py` - recurring topics

[`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) manages topics that should be researched on a schedule. Subcommands: `add`, `remove`, `list`, `run-one`, `run-all`, `config`. Built-in delivery to Slack incoming webhooks (`hooks.slack.com/...`) or any HTTPS endpoint, fired only when new findings appear.

Two-step flow (the watchlist holds the topic; an external scheduler invokes the run):

```bash
# 1. Add the topic to the watchlist
#    Default schedule daily 8am; --weekly switches to Mondays 8am
python3 scripts/watchlist.py add "british airways middle east" --weekly

# 2. Configure delivery and budget (optional)
python3 scripts/watchlist.py config delivery "https://hooks.slack.com/services/..."
python3 scripts/watchlist.py config budget 5.00

# 3. Trigger via cron / Task Scheduler / GitHub Actions
python3 scripts/watchlist.py run-one "british airways middle east"
# or run every enabled topic, gated by daily_budget
python3 scripts/watchlist.py run-all
```

The schedule field stored on each topic is metadata - the actual cron / Task Scheduler invocation is your responsibility. Watchlist runs hardcode `--quick` and `--lookback-days 90` when spawning the underlying engine.

### `briefing.py` - daily / weekly digests

[`scripts/briefing.py`](skills/last30days/scripts/briefing.py) reads the SQLite store and emits structured data the agent then synthesizes into prose. Modes: `generate` (daily), `generate --weekly`. Briefs save to `~/.local/share/last30days/briefs/`.

### Recommended cadence pattern

| Step | Cadence | Command |
|---|---|---|
| Baseline | one-time per topic | `/last30days "<topic>" --days=30 --store` |
| Add to watchlist | one-time per topic | `python3 scripts/watchlist.py add "<topic>" --weekly` |
| Recurring run | daily or weekly (external scheduler) | `python3 scripts/watchlist.py run-all` |
| Digest | weekly | `python3 scripts/briefing.py generate --weekly` |

---

## Per-client patterns

The skill is built to flex around different client environments. Four patterns that compose well, plus a pointer to the fork-iteration recipe (which lives in its own doc):

### 1. Per-client `.claude/last30days.env` (preferred when you cd into client folders)

The simplest pattern when each client has its own working directory: drop a `.claude/last30days.env` into the client folder. The skill picks it up automatically (see [API keys](#api-keys-env) for the lookup priority). Typical contents:

```bash
LAST30DAYS_MEMORY_DIR=C:\Users\<you>\Clients\acme\Research\Last30Days
SCRAPECREATORS_API_KEY=<acme-scoped-key-or-shared>
INCLUDE_SOURCES=tiktok,instagram
BSKY_HANDLE=<acme-bluesky-handle>.bsky.social
```

`cd` into the client folder, run `/last30days <topic>` as normal, no flags or wrappers. Combine with `--save-suffix=<client-slug>` per run if you also need to differentiate filenames within that folder.

### 2. Per-client save dir + suffix wrapper

For workflows where you don't `cd` into a client folder (running from anywhere, scripted batches), a tiny shell function isolates each client's research without engine changes.

PowerShell example:

```powershell
function Run-L30D-Client {
    param([string]$ClientSlug, [Parameter(ValueFromRemainingArguments=$true)]$Args)
    $env:LAST30DAYS_MEMORY_DIR = "C:\Users\$env:USERNAME\Clients\$ClientSlug\Research\Last30Days"
    /last30days @Args --save-suffix=$ClientSlug
}
# Usage: Run-L30D-Client acme "british airways middle east"
```

Bash example:

```bash
l30d-client() {
    local client=$1; shift
    LAST30DAYS_MEMORY_DIR="$HOME/Clients/$client/Research/Last30Days" \
        /last30days "$@" --save-suffix="$client"
}
# Usage: l30d-client acme "british airways middle east"
```

### 3. Custom category-peer subreddits

[`scripts/lib/categories.py`](skills/last30days/scripts/lib/categories.py) holds a table of `(category_id, trigger_keywords, peer_subreddits)`. If a client lives in a vertical that isn't covered (legal-tech, real-estate-tech, B2B HR SaaS), add a row. Pure data, no logic.

Section 2a of `SKILL.md` documents the merging rule the skill applies when your topic matches a category.

### 4. Pre-built `--competitors-plan` JSON

For competitor-vs-comparisons that recur, a pre-written JSON skeleton per client industry saves real time:

```json
{
  "Competitor B": {
    "x_handle": "competitor_b_handle",
    "subreddits": ["sub1", "sub2"],
    "github_user": "competitor-b-org",
    "context": "Founded 2019, focused on ..."
  },
  "Competitor C": { ... }
}
```

Pass as `--competitors-plan @client/competitors-plan.json` (or as a string). See `SKILL.md` section "If QUERY_TYPE = COMPARISON" for the full schema.

### 5. Running your own fork as the daily driver

If you maintain a fork (per-client variants, in-flight engine fixes, contributions staged for upstream), the four-command reinstall flow + cache verification recipe live in their own doc: see [`FORKING.md`](FORKING.md).

---

## Beta channel

Experimental customizations live on a private companion repo (`mvanhorn/last30days-skill-private`) installed as `/last30days-beta`. Never ship beta-only changes to the public marketplace without a review PR against the public repo. Workflow guide: `BETA.md` in the private repo.

This is the right home for client-specific changes you don't intend to upstream - custom category rows, internal subreddit lists, per-vertical plan templates.

---

## Cross-references

- The CLI flag surface: `python3 scripts/last30days.py --help`
- The skill contract (voice, LAWs, pre-flight protocol): [`skills/last30days/SKILL.md`](skills/last30days/SKILL.md)
- Engine spec (some sections stale; SKILL.md wins on conflicts): [`SPEC.md`](SPEC.md)
- Contributor guidance: [`CONTRIBUTORS.md`](CONTRIBUTORS.md)
