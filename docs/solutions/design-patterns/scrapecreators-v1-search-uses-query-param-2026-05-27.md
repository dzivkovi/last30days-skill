---
title: ScrapeCreators v1 search endpoints require `query=`, not `keyword=` (despite "Search by Keyword" docs label)
date: 2026-05-27
category: design-patterns
module: threads
problem_type: api_contract_convention
component: source_integrations
severity: high
applies_when:
  - integrating any new ScrapeCreators v1 endpoint into a lib/<source>.py module
  - refactoring an existing ScrapeCreators source (especially when porting from `requests` to `lib/http`)
  - debugging "source returns 0 items but other sources work" against a ScrapeCreators-backed source
  - copying request shape from a ScrapeCreators endpoint doc page that lists "Search by Keyword" in its sidebar
tags:
  - scrapecreators
  - threads
  - api-contract
  - silent-regression
  - last30days-issue-15
related_components:
  - threads
  - instagram
  - tiktok
  - pinterest
  - lib/http
---

# ScrapeCreators v1 search endpoints use `query=`, not `keyword=`

## Context

PR #393 (commit `80a1a47e`, 2026-05-15) refactored several source modules to route HTTP calls through `lib/http`'s urllib wrapper, replacing the `requests` library. The refactor preserved or introduced `params={"keyword": core_topic}` in `lib/threads.py:157`. ScrapeCreators' `/v1/threads/search` endpoint actually requires `params={"query": ...}` and returns HTTP 400 "Invalid parameters or missing required fields" on every request when sent `keyword=`. The Threads source contributed 0 items on every run for every user with `SCRAPECREATORS_API_KEY` configured, for 12 days, before being diagnosed and fixed in issue #15 (2026-05-27).

## The trap

ScrapeCreators' documentation labels the endpoint "**Search by Keyword**" in their docs sidebar. A developer adding or refactoring this integration naturally reaches for `params={"keyword": ...}`. The label is misleading: the actual parameter name on every ScrapeCreators v1 search endpoint is `query`, regardless of the endpoint's human-readable name.

Cross-endpoint confirmation:

- `/v1/threads/search?query=...` ✓
- `/v1/pinterest/search?query=...` ✓ (verified via lib/pinterest.py, working in production)
- `/v1/tiktok/search?query=...` ✓ (verified via lib/tiktok.py)
- `/v1/instagram/search?query=...` ✓ (verified via lib/instagram.py)

Every v1 search endpoint uses `query`. None use `keyword`. The convention is uniform; only the human-readable label varies.

## Failure mode

A `keyword=` request returns HTTP 400. ScrapeCreators' 400 response body is "Invalid parameters or missing required fields" — generic enough that the failure looks like a transient API issue rather than a request-shape bug. In this repository, two compounding observability gaps hid the failure for 12 days:

1. The Threads `_log` helper at `lib/threads.py:28` did not pass `tty_only=False`, so `log.source_log("Threads", ...)` calls were silently dropped in non-TTY contexts. Fixed by commit `6270694` (PR #14) and pending upstream as PR #454.
2. `lib/threads.py:162-164` catches `HTTPError` inline and returns `{"items": [], "error": "..."}`. The pipeline's `parse_threads_response()` only returns the items list — the `error` key is dropped, so `bundle.errors_by_source` never receives the failure. Affects multiple source modules with the same swallow pattern (Perplexity, Bluesky, Instagram, etc.). Deferred follow-up noted in `work/2026-05-27/10-perplexity-fix-wrap-up-final-state.md`.

Both gaps reinforce each other: silent log + dropped error envelope = a source that quietly returns 0 items with no visible diagnostic. The param-name bug is what *caused* the failure; the observability gaps are what *hid* it.

## Detection

Direct cURL verification with both shapes — one passes, one fails:

```bash
# Confirms the correct shape:
curl "https://api.scrapecreators.com/v1/threads/search?query=tourism" \
  -H "x-api-key: $SCRAPECREATORS_API_KEY"
# Returns 200 OK with {"success": true, "credits_remaining": N, "posts": [...]}

# Confirms the broken shape:
curl "https://api.scrapecreators.com/v1/threads/search?keyword=tourism" \
  -H "x-api-key: $SCRAPECREATORS_API_KEY"
# Returns HTTP 400 Bad Request
```

The regression test pinning this is `tests/test_threads.py::TestSearchThreadsRequestShape::test_uses_query_param_not_keyword` (issue #15).

## Pattern to follow

When adding or refactoring a ScrapeCreators v1 source integration:

1. Use `params={"query": <subject>}` for the search request. **Always `query`, never `keyword`.**
2. Add an inline comment near the param noting the trap, so the next reader doesn't "fix" it back to `keyword=`. Pattern:
   ```python
   # ScrapeCreators v1 search endpoints use 'query', NOT 'keyword' — even
   # though the endpoint is labeled "Search by Keyword" in their docs sidebar.
   # Sending 'keyword=' returns HTTP 400. See docs/solutions/design-patterns/
   # scrapecreators-v1-search-uses-query-param-2026-05-27.md
   params={"query": core_topic},
   ```
3. Write a regression test pinning the param name. Mock `lib.<source>.http.get`, call `search_<source>`, then assert `mock_http_get.call_args.kwargs["params"]` contains `"query"` and does NOT contain `"keyword"`.
4. Cross-check the response shape against the actual API. ScrapeCreators returns `{"success": bool, "credits_remaining": int, "posts": [...]}` for Threads. Items are under `posts`, not `items` or `data`. The existing extractor at `lib/threads.py:167-174` already includes `posts` in its fallback chain — preserve this when refactoring.
5. Pass `tty_only=False` to every `log.source_log(...)` call in the source's `_log` helper. Enforced by `tests/test_source_log_visibility.py`.

## Out-of-scope follow-ups discovered while diagnosing this

Documented for future work, not addressed by this fix:

- **Query-shape sensitivity.** ScrapeCreators' Threads search returns 0 items for long phrasal queries even when the param name is correct. "tourism" returns 6-17 items; "where are people no longer flying and where are they going instead" returns 0. The skill's `_extract_core_subject()` strips some noise but not enough for ScrapeCreators' Threads endpoint specifically. Likely needs a Threads-specific aggressive noun-extraction pass or a fallback-to-shorter-keyword strategy on first-0-result subqueries.
- **Layer-2 diagnosability.** Errors caught inside source modules should propagate to `bundle.errors_by_source` so the rendered report's `## Source Errors` section reflects them. Same swallow pattern affects multiple sources.
- **`threads.net` → `threads.com` URL emitter.** Meta rebranded the public web URL; the 301 redirect still works but `lib/threads.py:97-100` should emit `threads.com` directly for cleanliness.

## References

- Issue: dzivkovi/last30days-skill#15
- Regression introduction: PR #393 (mvanhorn/last30days-skill), commit `80a1a47e`, 2026-05-15
- Regression test: `tests/test_threads.py::TestSearchThreadsRequestShape`
- Inline anti-regression comment: `skills/last30days/scripts/lib/threads.py:156-159`
- ScrapeCreators docs landing: https://docs.scrapecreators.com
- Threads search endpoint: https://docs.scrapecreators.com/v1/threads/search
