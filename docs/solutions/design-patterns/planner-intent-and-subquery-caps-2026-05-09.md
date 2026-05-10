---
title: Planner intent classification — pick from 8 allowed values, know the per-intent subquery cap
date: 2026-05-09
category: design-patterns
module: planner
problem_type: design_pattern
component: tooling
severity: medium
applies_when:
  - authoring a JSON query plan via --plan flag for the last30days engine
  - choosing the intent value when calling /last30days from another agent or skill
  - debugging "my plan had N subqueries but engine only ran M"
  - extending the planner FSM or adding a new intent type
  - investigating why a research run produced thinner output than expected
tags:
  - planner
  - intent
  - subqueries
  - calling-agent-contract
  - silent-failure
  - fsm
---

# Planner intent classification — pick from 8 allowed values, know the per-intent subquery cap

## Context

The `/last30days` skill's calling-agent contract (SKILL.md "LAW 7") says: *"YOU are the planner — pass `--plan`, the engine respects it."* In practice the engine applies two silent transformations to user-provided plans that calling agents need to know about:

1. **`intent` strings outside `ALLOWED_INTENTS` are silently reclassified** (no warning, no log line) to whatever `_infer_intent(topic)` returns. Most unclassified topics resolve to `concept` per Matt's deliberate Unit 3 choice in commit `4d9f29d`.
2. **Subquery counts above the per-intent cap are silently truncated from the END of the list.** No warning — the dropped subqueries vanish before any source backend is invoked.

These behaviors aren't bugs. The cap was added in commit `4d9f29d` (2026-04-19, the "Hermes Agent Use Cases" failure) to defend against the engine's own auto-planner LLM producing near-duplicate subqueries when topics don't have natural fanout. The default `concept` over `breaking_news` was a deliberate Unit 3 trade to preserve evergreen recall on unclassified topics. Both choices are defensible — but they're undocumented from the calling-agent's perspective, and they're applied to *external* plans (where the calling agent has more context than the auto-planner) using the same cap as for *internal* plans.

## Guidance

**Three rules when authoring a `--plan` JSON for `/last30days`:**

### 1. Pick `intent` from EXACTLY one of these 8 values

```
factual, product, concept, opinion, how_to, comparison, breaking_news, prediction
```

Anything else (`"news"`, `"trending"`, `"recap"`, `"social-listening"`, etc.) is silently reclassified by the engine. The string `"news"` in particular reclassifies to `concept`, which then caps subqueries to **2**.

### 2. Know the per-intent subquery cap

| Intent | Cap | Default freshness | Default cluster |
|---|---|---|---|
| `breaking_news`, `how_to`, `opinion`, `product`, `prediction` | **5** | varies (see below) | varies |
| `comparison` | **4** | normal | `entity` |
| `concept`, `factual` | **2** | `evergreen_ok` | none |
| any of the above with intent-modifier word in topic (`use cases`, `workflows`, `review`, `examples`) | **5** | n/a | n/a |

The engine drops subqueries from the END of your list when count exceeds cap. **Order your subqueries by importance.**

### 3. For each topic class, pick the right intent

| Topic class | Right intent | Notes |
|---|---|---|
| Single breaking event ("Israel-Iran ceasefire", "Apple WWDC 2026 announcements") | `breaking_news` | Default `strict_recent` freshness is correct here |
| **Ongoing beat / social listening** ("Toronto real estate", "AI agents adoption") | `breaking_news` **+ explicit `freshness_mode="balanced_recent"`** | The `breaking_news` intent gives you cap=5; the explicit override prevents `strict_recent` from over-filtering older relevant material on slow-moving topics |
| Comparison ("Cursor vs Windsurf", "React vs Vue") | `comparison` | Cap=4, automatically applies entity-based clustering |
| How-to / tutorial ("How to use Claude Skills", "Cursor agent workflows") | `how_to` | Cap=5; `evergreen_ok` freshness lets older tutorials surface |
| Cold-start unclassified topic, conservative recall | `concept` | Cap=2 (Matt's deliberate default — preserves evergreen recall, avoids forcing recency bias on unfamiliar topics) |
| Topics with intent-modifier suffix ("Hermes use cases", "Cursor review", "Llama workflows") | matches the suffix's intent (`how_to` / `opinion`) | Cap auto-lifts to 5 because `_has_intent_modifier(topic)` returns True |

## Why This Matters

The cap-2 default for `concept` and the silent reclassification of invalid intents combined produce a non-obvious failure mode: **a calling agent that thoughtfully writes a 4-subquery plan with `intent="news"` gets a 2-subquery run, with no warning, and produces thinner output than expected**. The dropped subqueries never run any source backend.

The Hermes-2026-04-19 failure that motivated the cap was real — when the engine's auto-planner LLM was asked to fan out at N=3 for "Hermes Agent use cases", it produced near-literal echoes of the topic. The cap defends against that. But it's applied unconditionally to external plans where the calling agent has full context (entities resolved, prior runs known, distinct angles in mind) — and the calling agent is left to discover the cap by comparing what-it-submitted vs what the `[Planner]` stderr line printed.

For the cold-start unclassified case, Matt's choice is right. For the warm hand-authored social-listening case, the calling agent needs to know to pick `breaking_news` + `balanced_recent` rather than `concept`. There's no signal in the engine telling them this — only this guidance.

## When to Apply

See `applies_when` frontmatter. Most concretely: any time you write a `--plan` JSON with more than 2 subqueries OR with an intent value you haven't double-checked against the 8-value list.

## Examples

**Wrong** (the failure that triggered this learning, observed 2026-05-08):
```json
{
  "intent": "news",
  "freshness_mode": "balanced_recent",
  "cluster_mode": "story",
  "subqueries": [
    {"label": "primary", "search_query": "toronto real estate", ...},
    {"label": "gta_market", "search_query": "GTA housing market", ...},
    {"label": "prices", "search_query": "toronto home prices condo", ...},
    {"label": "market_data", "search_query": "TRREB market watch April 2026", ...}
  ]
}
```
Engine silently reclassifies `intent="news"` → `concept`, applies cap=2, drops `prices` and `market_data`. Run completes with 2 subqueries instead of 4. **No warning.**

**Right** (same plan, valid intent + explicit freshness override):
```json
{
  "intent": "breaking_news",
  "freshness_mode": "balanced_recent",
  "cluster_mode": "story",
  "subqueries": [
    {"label": "primary", "search_query": "toronto real estate", ...},
    {"label": "gta_market", "search_query": "GTA housing market", ...},
    {"label": "prices", "search_query": "toronto home prices condo", ...},
    {"label": "market_data", "search_query": "TRREB market watch April 2026", ...}
  ]
}
```
Engine accepts as-is. Cap=5 means all 4 subqueries pass through. The explicit `freshness_mode="balanced_recent"` overrides `breaking_news`'s default `strict_recent`, preserving older relevant material for the slow-moving topic.

**Right for a different topic class** (cold-start unclassified topic):
```json
{
  "intent": "concept",
  "freshness_mode": "evergreen_ok",
  "subqueries": [
    {"label": "primary", "search_query": "obscure framework name", ...},
    {"label": "alt", "search_query": "obscure framework alternative phrasing", ...}
  ]
}
```
Cap=2 here is right. Auto-fan to 4-5 on an unknown topic risks the Hermes-failure-mode (near-duplicate paraphrases). Matt's `concept` default protects this case.

**Right for "use cases" topics** (intent modifier present):
```json
{
  "intent": "how_to",
  "subqueries": [
    {"label": "primary", "search_query": "Hermes Agent", ...},
    {"label": "workflows", "search_query": "Hermes Agent workflows", ...},
    {"label": "production", "search_query": "Hermes Agent production deployment", ...},
    {"label": "experience", "search_query": "Hermes Agent developer experience", ...},
    {"label": "comparison", "search_query": "Hermes Agent vs alternatives", ...}
  ]
}
```
The topic carrying "use cases" lifts the cap to 5 via `_has_intent_modifier`. All 5 pass through.

## How to verify in your own runs

Run with stderr captured (or look at the bash output in your Claude Code session). The first informational line from the planner looks like:

```
[Planner] Plan: intent=concept, freshness=balanced_recent, cluster_mode=story, subqueries=2, source=external
```

If `subqueries=N` is less than what you submitted, the engine truncated. If `intent=X` is different from what you submitted, the engine reclassified. Both happen silently — `[Planner]` is the only signal.

You can also probe the engine's classification of any topic+intent combination directly without running a full search:

```python
from lib import planner
print(planner._infer_intent("your topic"))           # what auto-classification returns
print(planner._max_subqueries("concept", "your topic"))  # the cap that would apply
```

This is how the perplexity-causality test on 2026-05-09 conclusively proved that the truncation depends only on `(intent, topic)` — adding/removing perplexity from `INCLUDE_SOURCES` had zero effect on cap calculation.

## Related

- Investigation chain (this fork's work notes): `work/2026-05-08/{02,04,05}-*.md`, `work/2026-05-09/{01,02}-*.md`, `work/2026-05-10/01-*.md`
- SKILL.md Step 0.75 update (this fork, uncommitted at time of writing): adds the 8-value list + per-intent cap table + ongoing-beat guidance inline
- Upstream issue draft (this fork): `work/2026-05-09/02-upstream-issue-draft-fsm-gap-news-intent.md` — frames the FSM gap for `mvanhorn/last30days-skill`
- Origin commit for the cap: `4d9f29d` "fix: broaden planner retrieval and fix deterministic fallback defaults" (2026-04-19) — Matt's commit message defends both the cap-2 for concept and the `concept`-as-default decisions
- Source: `skills/last30days/scripts/lib/planner.py:206-208` (intent reclassification), `:236` (truncation slice), `:641-657` (`_max_subqueries` cap function)
- Multi-agent review: Codex initially recommended structural FSM fix; revised position to "use existing FSM correctly" after seeing commit `4d9f29d`'s deliberate-design reasoning. Convergence captured in `work/2026-05-09/01-...md`
