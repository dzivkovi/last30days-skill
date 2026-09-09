"""GitHub trending lane for /last30days: repositories rising this week, plus new
repositories in the topic.

Two clearly separated halves, never blended into one "trending" number:

- **risers**: the ``github.com/trending?since=weekly`` page, the only public
  source that still publishes a real "stars this week" delta (the stargazers
  API was restricted to repo admins on 2026-06-30, GH Archive star events fell
  to a few percent of baseline, and OSSInsight switched its rankings off).
  The page has no topic filter and about 25 rows, so risers are kept only when
  they lexically match the run topic, unless the topic itself is a global
  "what is trending" question.
- **new**: ``GET /search/repositories?q=<terms> created:>{from_date}&sort=stars``,
  keyless (10 requests per minute; a GITHUB_TOKEN raises that). It finds
  brand-new repositories in the topic ranked by lifetime stars; it cannot see an
  established repository having a breakout week, which is the risers' job.
  ``pushed:>`` is deliberately not used: it returns all-time giants that merely
  received a commit.

Opt-in only: ``--search github_trending`` or ``INCLUDE_SOURCES=github_trending``.
The two halves share the item shape ``lib.github`` already normalizes; the
half is carried in ``container`` and ``metadata["half"]``.
"""

from __future__ import annotations

import re
import urllib.parse
from html.parser import HTMLParser
from typing import Any

from . import github, http, log
from .relevance import token_overlap_relevance

TRENDING_URL = "https://github.com/trending"
SEARCH_REPOS_URL = "https://api.github.com/search/repositories"
SOURCE = "github_trending"
# (risers kept, new repos requested) per depth.
# The engine renders a bounded number of items per source, so the search half
# is capped low enough that risers (fewer, filtered by topic) stay visible.
DEPTH_CONFIG: dict[str, tuple[int, int]] = {"quick": (8, 6), "default": (12, 10), "deep": (25, 16)}
RISER_RELEVANCE_FLOOR = 0.15
# Topics that mean "what is hot on GitHub" rather than a domain: risers are
# kept unfiltered for these.
GLOBAL_TRENDING_TERMS = frozenset({"trending", "trend", "trends", "popular", "hot", "top", "github", "repos", "repositories", "week", "this"})
_COUNT = re.compile(r"[\d,]+")


def _log(msg: str) -> None:
    log.source_log("GitHub trending", msg, tty_only=False)


# ---------------------------------------------------------------------------
# Risers: github.com/trending
# ---------------------------------------------------------------------------


class _TrendingParser(HTMLParser):
    """One ``<article class="Box-row">`` per repository: the ``<h2>`` link is the
    repo, ``<p>`` the description, ``itemprop="programmingLanguage"`` the language,
    the ``/stargazers`` and ``/forks`` links carry totals, and the trailing span
    carries "N stars this week"."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.repos: list[dict[str, Any]] = []
        self._repo: dict[str, Any] | None = None
        self._depth = 0
        self._article_depth = 0
        self._capture: str | None = None
        self._capture_depth = 0
        self._buf: list[str] = []
        self._in_h2 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._depth += 1
        a = {k: (v or "") for k, v in attrs}
        classes = a.get("class", "").split()
        if tag == "article" and "Box-row" in classes:
            self._flush()
            self._repo = {"repo": "", "description": "", "language": "", "stars": 0, "forks": 0, "stars_week": 0}
            self._article_depth = self._depth
            return
        if self._repo is None:
            return
        if tag == "h2":
            self._in_h2 = True
        elif tag == "a" and self._in_h2 and a.get("href") and not self._repo["repo"]:
            self._repo["repo"] = a["href"].strip("/")
        elif tag == "p" and any(c.startswith("col-") for c in classes):
            self._start("description")
        elif tag == "span" and a.get("itemprop") == "programmingLanguage":
            self._start("language")
        elif tag == "a" and a.get("href", "").endswith("/stargazers"):
            self._start("stars")
        elif tag == "a" and a.get("href", "").endswith("/forks"):
            self._start("forks")
        elif tag == "span" and "float-sm-right" in classes:
            self._start("stars_week")

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture is not None and self._depth == self._capture_depth:
            text = " ".join("".join(self._buf).split())
            name = self._capture
            self._capture = None
            if self._repo is not None:
                if name in ("description", "language"):
                    self._repo[name] = text
                else:
                    m = _COUNT.search(text)
                    self._repo[name] = int(m.group(0).replace(",", "")) if m else 0
        if tag == "h2":
            self._in_h2 = False
        if self._repo is not None and tag == "article" and self._depth == self._article_depth:
            self._flush()
        self._depth = max(0, self._depth - 1)

    def close(self) -> None:
        super().close()
        self._flush()

    def _start(self, name: str) -> None:
        self._capture = name
        self._capture_depth = self._depth
        self._buf = []

    def _flush(self) -> None:
        if self._repo is not None and self._repo["repo"]:
            self.repos.append(self._repo)
        self._repo = None


def parse_trending_page(page: str) -> list[dict[str, Any]]:
    """Rows of the trending page in page order (rank 1 first)."""
    parser = _TrendingParser()
    parser.feed(page or "")
    parser.close()
    return parser.repos


def is_global_trending_query(query: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(w) > 1]
    return not words or all(w in GLOBAL_TRENDING_TERMS for w in words)


def fetch_risers(
    query: str,
    *,
    since: str = "weekly",
    limit: int,
    observed_at: str | None = None,
    fetch_text: Any = None,
    errors: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Trending-page repositories, filtered by lexical match to the topic unless the
    topic is a global "what is trending" question.

    A riser carries no publication date; "rising this week" is a fact observed on
    ``observed_at`` (the run's end date), so that date is stamped with low
    confidence. Without any date the engine would sort risers below every dated
    item and the per-source render cap would hide them.
    """
    fetch_text = fetch_text or (lambda url: http.get_text(url, timeout=30, retries=1, accept="text/html"))
    page = fetch_text(f"{TRENDING_URL}?since={since}")
    if page is None:
        if errors is not None:
            errors.append("trending page unreachable")
        return []
    rows = parse_trending_page(page)
    if not rows:
        if errors is not None:
            errors.append("trending page markup not recognized")
        return []
    keep_all = is_global_trending_query(query)
    items: list[dict[str, Any]] = []
    for rank, row in enumerate(rows):
        text = f"{row['repo'].replace('/', ' ')} {row['description']} {row['language']}"
        relevance = token_overlap_relevance(query, text) if not keep_all else 1.0
        if not keep_all and relevance < RISER_RELEVANCE_FLOOR:
            continue
        items.append(_item(row, half="risers", rank=rank, relevance=relevance, since=since, observed_at=observed_at))
        if len(items) >= limit:
            break
    _log(f"risers: {len(rows)} on the {since} page, {len(items)} kept for '{query}'")
    return items


# ---------------------------------------------------------------------------
# New in topic: /search/repositories
# ---------------------------------------------------------------------------


def _topic_terms(query: str) -> str:
    cleaned = github.strip_search_qualifiers(query or "")
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+#-]*", cleaned) if w.lower() not in GLOBAL_TRENDING_TERMS]
    return " ".join(words[:6])


def fetch_new_repos(query: str, from_date: str, *, limit: int, token: str | None = None, errors: list[str] | None = None) -> list[dict[str, Any]]:
    """Repositories created since ``from_date`` matching the topic, by lifetime stars."""
    terms = _topic_terms(query)
    if not terms:
        return []
    q = f"{terms} created:>{from_date}"
    url = f"{SEARCH_REPOS_URL}?" + urllib.parse.urlencode({"q": q, "sort": "stars", "order": "desc", "per_page": str(min(limit, 50))})
    data = github._fetch_json(url, token, failure_out=errors)
    if not data:
        return []
    items: list[dict[str, Any]] = []
    for rank, repo in enumerate(data.get("items") or []):
        row = {
            "repo": str(repo.get("full_name") or ""),
            "description": str(repo.get("description") or ""),
            "language": str(repo.get("language") or ""),
            "stars": int(repo.get("stargazers_count") or 0),
            "forks": int(repo.get("forks_count") or 0),
            "stars_week": 0,
            "topics": [str(t) for t in (repo.get("topics") or [])],
            "created_at": str(repo.get("created_at") or ""),
            "url": str(repo.get("html_url") or ""),
        }
        if not row["repo"]:
            continue
        text = f"{row['repo'].replace('/', ' ')} {row['description']} {' '.join(row['topics'])}"
        items.append(_item(row, half="new", rank=rank, relevance=max(0.5, token_overlap_relevance(query, text))))
    _log(f"new: {len(items)} repositories created since {from_date} for '{terms}'")
    return items


# ---------------------------------------------------------------------------
# Shared item shape and entry point
# ---------------------------------------------------------------------------


def _item(
    row: dict[str, Any],
    *,
    half: str,
    rank: int,
    relevance: float,
    since: str = "weekly",
    observed_at: str | None = None,
) -> dict[str, Any]:
    repo = row["repo"]
    owner = repo.split("/", 1)[0] if "/" in repo else ""
    if half == "risers":
        container = f"GitHub trending: rising this {since.removesuffix('ly') if since != 'daily' else 'day'}"
        why = f"{row['stars_week']:,} stars this {since.removesuffix('ly')}, rank {rank + 1} on github.com/trending"
    else:
        container = "GitHub: new repositories in topic"
        why = f"created {row.get('created_at', '')[:10]}, {row['stars']:,} stars"
    return {
        "id": f"GT-{half}-{repo}",
        "title": repo,
        "url": row.get("url") or f"https://github.com/{repo}",
        "snippet": row["description"],
        "author": owner,
        "container": container,
        "date": (row.get("created_at") or "")[:10] or (observed_at if half == "risers" else None),
        "engagement": {"stars": row["stars"], "forks": row["forks"], "stars_week": row["stars_week"]},
        "relevance": round(min(1.0, relevance), 2),
        "why_relevant": why,
        "metadata": {
            "half": half,
            "language": row["language"],
            "topics": row.get("topics", []),
            "rank": rank + 1,
            "observed_at": observed_at if half == "risers" else None,
            # The search half was gated by the topic query already; without
            # this the lexical prune drops repos whose description does not
            # repeat the query words (same rule the Amazon lane uses).
            "grounding_exempt": half == "new",
        },
    }


def search_github_trending(
    query: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Both halves, deduplicated by repository. Returns ``{"items": [...]}`` plus an
    ``error`` key only when both halves failed to produce anything."""
    risers_cap, new_cap = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    errors: list[str] = []
    risers = fetch_risers(query, limit=risers_cap, observed_at=to_date, errors=errors)
    new = fetch_new_repos(query, from_date, limit=new_cap, token=token, errors=errors)
    seen: dict[str, dict[str, Any]] = {}
    for item in risers + new:
        key = item["title"].lower()
        if key in seen:
            first = seen[key]
            first["metadata"]["half"] = "both"
            first["engagement"]["stars_week"] = first["engagement"]["stars_week"] or item["engagement"]["stars_week"]
            first["date"] = first["date"] or item["date"]
            continue
        seen[key] = item
    items = list(seen.values())
    result: dict[str, Any] = {"items": items}
    if errors and not items:
        result["error"] = "; ".join(errors)
    elif errors:
        result["warning"] = "; ".join(errors)
    _log(f"Found {len(items)} repositories ({len(risers)} risers, {len(new)} new)")
    return result


def parse_github_trending_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    return list(response.get("items") or [])
