"""Telegram public channel posts for /last30days.

Two backends behind one source: the ScrapeCreators REST API (paid, primary when
its key is present) and the keyless ``https://t.me/s/<channel>`` preview pages
(text, timestamp, views, reactions; no login, no key). No keyword search -
channel handles only.

Requires a channel list (TELEGRAM_SOURCES env var or --telegram-sources CLI
flag). SCRAPECREATORS_API_KEY is optional; ``LAST30DAYS_TELEGRAM_BACKEND``
(``scrapecreators`` | ``keyless``) pins one backend.

API docs: https://docs.scrapecreators.com/v1/telegram/channel/posts
"""

import math
import os
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

from . import dates, http, log
from .relevance import token_overlap_relevance as _compute_relevance

SCRAPECREATORS_BASE = "https://api.scrapecreators.com/v1/telegram"

DEPTH_PAGE_CAPS = {
    "quick": 1,
    "default": 3,
    "deep": 6,
}


def _log(msg: str):
    log.source_log("Telegram", msg, tty_only=False)


class InvalidChannelHandle(ValueError):
    """Raised when a channel handle is rejected (joinchat, numeric -100 ID)."""


def parse_channel_handle(raw: str) -> str:
    """Normalize a Telegram channel identifier to a bare handle.

    Accepts:
        - bare username: aipost
        - @handle: @aipost
        - t.me URL: https://t.me/aipost
        - t.me/s preview URL: https://t.me/s/aipost

    Rejects (raises InvalidChannelHandle):
        - joinchat links: https://t.me/joinchat/xxxxx
        - numeric -100 supergroup IDs: -1001234567890

    Returns:
        Bare handle string (no @ prefix).
    """
    handle = raw.strip()
    if not handle:
        raise InvalidChannelHandle("Empty channel handle")

    if handle.lstrip("-").isdigit() and handle.startswith("-100"):
        raise InvalidChannelHandle(
            f"Numeric supergroup IDs are not supported: {handle}"
        )

    if handle.startswith("@"):
        handle = handle[1:]
        if not handle:
            raise InvalidChannelHandle("Empty handle after @ prefix")
        return _validated_username(handle, raw)

    url_match = re.match(
        r"(?:https?://)?(?:www\.)?t\.me/(?:s/)?([^/?#]+)",
        handle,
        re.IGNORECASE,
    )
    if url_match:
        extracted = url_match.group(1)
        if extracted.lower() == "joinchat":
            raise InvalidChannelHandle(
                f"Private joinchat links are not supported: {raw}"
            )
        return _validated_username(extracted, raw)

    if "joinchat" in handle.lower():
        raise InvalidChannelHandle(
            f"Private joinchat links are not supported: {raw}"
        )

    return _validated_username(handle, raw)


# Telegram public usernames: 5-32 characters of [A-Za-z0-9_]. Anything else
# would be interpolated into a URL path (keyless) or an API query (paid).
_USERNAME = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def _validated_username(handle: str, raw: str) -> str:
    if not _USERNAME.match(handle):
        raise InvalidChannelHandle(f"Not a valid public channel username: {raw}")
    return handle


def parse_channel_sources(raw: str) -> list[str]:
    """Parse a comma-separated list of channel handles.

    Filters out invalid handles (logs a warning) and returns valid ones.
    """
    handles: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            handle = parse_channel_handle(part)
            if handle.lower() not in {h.lower() for h in handles}:
                handles.append(handle)
        except InvalidChannelHandle as exc:
            _log(f"Skipping invalid channel: {exc}")
    return handles


def _get_channel_sources(config: dict[str, Any]) -> list[str]:
    """Get configured Telegram channel sources from config or env."""
    raw = config.get("TELEGRAM_SOURCES") or os.environ.get("TELEGRAM_SOURCES") or ""
    return parse_channel_sources(raw)


def is_telegram_configured(config: dict[str, Any]) -> bool:
    """True when at least one channel is configured. The key is optional: without
    it the keyless t.me/s backend serves the same public posts."""
    return bool(_get_channel_sources(config))


def _parse_date(item: dict[str, Any]) -> str | None:
    """Parse date from Telegram post to YYYY-MM-DD."""
    for key in ("published_at", "date", "created_at"):
        val = item.get(key)
        if val is None:
            continue
        dt = dates.parse_date(str(val))
        if dt:
            return dt.strftime("%Y-%m-%d")
    return None


def _parse_post(
    raw: dict[str, Any],
    channel: dict[str, Any],
    topic: str,
    index: int,
) -> dict[str, Any]:
    """Parse a single Telegram post into normalized dict."""
    post_id = str(raw.get("id") or f"TG{index + 1}")
    text = str(raw.get("text") or "").strip()
    url = str(raw.get("url") or "")
    date_str = _parse_date(raw)

    handle = str(raw.get("channel_handle") or channel.get("handle") or "")
    author_name = str(raw.get("author_name") or channel.get("name") or handle)

    view_count = raw.get("view_count") or 0
    reaction_count = raw.get("reaction_count") or 0
    subscriber_count = channel.get("subscriber_count") or 0

    text_relevance = _compute_relevance(topic, text)
    rank_score = max(0.3, 1.0 - (index * 0.02))
    engagement_boost = min(0.2, math.log1p(view_count + reaction_count * 10) / 50)
    relevance = min(1.0, text_relevance * 0.5 + rank_score * 0.3 + engagement_boost + 0.1)

    return {
        "id": post_id,
        "handle": handle,
        "display_name": author_name,
        "text": text,
        "url": url,
        "date": date_str,
        "engagement": {
            "views": view_count,
            "reactions": reaction_count,
            "subscribers": subscriber_count,
        },
        "relevance": round(relevance, 2),
        "why_relevant": f"Telegram @{handle}: {text[:60]}" if text else f"Telegram: @{handle}",
    }


def _fetch_channel_posts(
    handle: str,
    token: str,
    *,
    from_date: str,
    topic: str,
    max_pages: int,
) -> list[dict[str, Any]]:
    """Fetch posts from a single channel, paginating until date cutoff."""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    pages_fetched = 0

    while pages_fetched < max_pages:
        _log(f"Fetching @{handle} (page {pages_fetched + 1}/{max_pages})")

        params: dict[str, Any] = {"handle": handle}
        if cursor:
            params["cursor"] = cursor

        try:
            data = http.get(
                f"{SCRAPECREATORS_BASE}/channel/posts",
                params=params,
                headers=http.scrapecreators_headers(token),
                timeout=30,
                retries=2,
            )
        except http.HTTPError as exc:
            _log(f"HTTP error fetching @{handle}: {exc}")
            break

        if not data.get("success"):
            error_msg = data.get("error") or data.get("message") or "Unknown error"
            _log(f"API error for @{handle}: {error_msg}")
            break

        channel = data.get("channel") or {}
        posts = data.get("posts") or []

        if not posts:
            _log(f"No posts returned for @{handle}")
            break

        page_all_old = True
        for idx, raw_post in enumerate(posts):
            parsed = _parse_post(raw_post, channel, topic, len(items) + idx)
            items.append(parsed)
            if parsed["date"] and parsed["date"] >= from_date:
                page_all_old = False

        pages_fetched += 1

        if page_all_old:
            _log(f"All posts on page older than {from_date}, stopping pagination")
            break

        cursor = data.get("cursor")
        if not data.get("has_more") or not cursor:
            break

    return items


# ---------------------------------------------------------------------------
# Keyless backend: public t.me/s preview pages
# ---------------------------------------------------------------------------
#
# ``https://t.me/s/<channel>`` renders a public channel's recent posts as HTML
# with text, timestamp, view count and reaction counts, no login and no key.
# It is the "opt-in backend under an existing lane" shape: the same channel
# list, the same post dict, the same normalization; only the transport differs.
# Private channels, groups and nonexistent handles render a landing page with
# no messages, which reads as "no public posts", never as an error.

TME_BASE = "https://t.me/s"
BACKEND_PIN_VAR = "LAST30DAYS_TELEGRAM_BACKEND"
BACKEND_SCRAPECREATORS = "scrapecreators"
BACKEND_KEYLESS = "keyless"
BACKEND_ORDER = (BACKEND_SCRAPECREATORS, BACKEND_KEYLESS)
_COUNT_SUFFIX = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
# The preview pages are an unmetered public surface: never fetch more than this
# many per channel per run, and pause between pages.
KEYLESS_MAX_PAGES = 20
KEYLESS_PAGE_DELAY_SECONDS = 0.4
_VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "source", "wbr", "area", "base", "col", "embed", "param", "track"}
# Classes that can never sit inside a captured text block; meeting one while a
# capture is open means a tag was left unclosed, so the capture ends there.
_STRUCTURAL = {
    "tgme_widget_message_footer",
    "tgme_widget_message_info",
    "tgme_widget_message_meta",
    "tgme_widget_message_reactions",
    "tgme_widget_message_views",
    "tgme_widget_message_date",
    "tgme_widget_message_bubble",
    "tgme_widget_message_author",
    "tgme_widget_message_wrap",
    "tgme_widget_message",
}


def resolve_backend(config: dict[str, Any], token: str | None = None) -> str:
    """``LAST30DAYS_TELEGRAM_BACKEND`` pin wins; else ScrapeCreators when its key is
    present, else the keyless preview pages. Reads ``config`` only: the engine
    loads that variable from the process environment and ``.env`` into config,
    and ``backends.py`` predicts from the same dict, so the two cannot disagree."""
    pin = str(config.get(BACKEND_PIN_VAR) or "").strip().lower()
    if pin in BACKEND_ORDER:
        return pin
    if pin:
        _log(f"Ignoring unknown {BACKEND_PIN_VAR}={pin!r} (expected scrapecreators or keyless)")
    if token or config.get("SCRAPECREATORS_API_KEY"):
        return BACKEND_SCRAPECREATORS
    return BACKEND_KEYLESS


def parse_count(text: Any) -> int:
    """``24.2M`` -> 24200000, ``1.5K`` -> 1500, ``477`` -> 477, junk -> 0."""
    raw = str(text or "").strip().replace(",", "").replace("\u00a0", "")
    if not raw:
        return 0
    multiplier = 1
    if raw[-1].upper() in _COUNT_SUFFIX:
        multiplier = _COUNT_SUFFIX[raw[-1].upper()]
        raw = raw[:-1]
    try:
        return int(float(raw) * multiplier)
    except (ValueError, OverflowError):
        return 0


def _collapse_text(text: str) -> str:
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line).strip()


class _TmePageParser(HTMLParser):
    """Stdlib parser for one ``t.me/s/<channel>`` page.

    Tracks element depth so a post ends exactly where its ``tgme_widget_message``
    div closes; captures the text block, views, reactions, author, date link and
    ``<time datetime>``; reads the "load more" cursor and the channel header.
    """

    def __init__(self, handle: str) -> None:
        super().__init__(convert_charrefs=True)
        self.handle = handle
        self.posts: list[dict[str, Any]] = []
        self.wrappers = 0  # raw message blocks seen, before the text/service filter
        self.before: str | None = None
        self.channel_name = ""
        self.subscriber_count = 0
        self._depth = 0
        self._post: dict[str, Any] | None = None
        self._post_depth = 0
        self._capture: str | None = None
        self._capture_depth = 0
        self._buf: list[str] = []
        self._counter_value: str | None = None

    # -- element tracking -------------------------------------------------

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            self._void(tag)
        else:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def _void(self, tag: str) -> None:
        if self._capture is not None and tag == "br":
            self._buf.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            self._void(tag)
            return
        self._depth += 1
        attributes = {key: (value or "") for key, value in attrs}
        classes = attributes.get("class", "").split()
        if tag == "div" and "tgme_widget_message" in classes and attributes.get("data-post"):
            # Recovery: an unbalanced tag in the previous block must not swallow
            # this one, so a new message always closes whatever was open.
            if self._capture is not None:
                self._finish_capture()
            if self._post is not None:
                self._finish_post()
            self.wrappers += 1
            post_id = attributes["data-post"].rsplit("/", 1)[-1]
            self._post = {
                "id": post_id,
                "channel_handle": self.handle,
                "url": f"https://t.me/{self.handle}/{post_id}",
                "author_name": "",
                "text": "",
                "published_at": None,
                "view_count": 0,
                "reaction_count": 0,
                "_service": "service_message" in classes,
            }
            self._post_depth = self._depth
            return
        if tag == "a" and "tme_messages_more" in classes:
            # A page carries a "before" anchor (older) and, past page one, an
            # "after" anchor (newer). Only the former is a cursor, and the first
            # one wins; the after-anchor must never blank it.
            before = attributes.get("data-before", "")
            href = attributes.get("href", "")
            if not before and "before=" in href:
                before = href.split("before=", 1)[1].split("&", 1)[0]
            if before and self.before is None and before.isdigit():
                self.before = before
            return
        if self._capture is not None:
            # An unclosed tag inside the text block would otherwise let the
            # capture run into the footer. Any structural element ends it.
            if classes and _STRUCTURAL.intersection(classes):
                self._finish_capture()
            else:
                if tag in ("p", "div"):
                    self._buf.append("\n")
                return
        if tag == "div" and "tgme_channel_info_header_title" in classes:
            self._start_capture("channel_name")
        elif tag == "span" and "counter_value" in classes:
            self._start_capture("counter_value")
        elif tag == "span" and "counter_type" in classes:
            self._start_capture("counter_type")
        elif self._post is None:
            return
        elif tag == "div" and "tgme_widget_message_text" in classes:
            self._start_capture("text")
        elif tag == "span" and "tgme_widget_message_views" in classes:
            self._start_capture("views")
        elif tag == "span" and "tgme_reaction" in classes:
            self._start_capture("reaction")
        elif tag == "a" and "tgme_widget_message_owner_name" in classes:
            self._start_capture("author")
        elif tag == "a" and "tgme_widget_message_date" in classes and attributes.get("href"):
            self._post["url"] = attributes["href"]
        elif tag == "time" and attributes.get("datetime"):
            self._post["published_at"] = attributes["datetime"]

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if self._capture is not None and self._depth == self._capture_depth:
            self._finish_capture()
        # A post ends when the next message block starts or the document ends
        # (see the recovery in handle_starttag and close()); depth is not
        # trusted for that boundary because one unbalanced tag would shift it.
        self._depth = max(0, self._depth - 1)

    def close(self) -> None:
        super().close()
        if self._capture is not None:
            self._finish_capture()
        if self._post is not None:
            self._finish_post()

    # -- captures ---------------------------------------------------------

    def _start_capture(self, name: str) -> None:
        self._capture = name
        self._capture_depth = self._depth
        self._buf = []

    def _finish_capture(self) -> None:
        text = "".join(self._buf)
        name = self._capture
        self._capture = None
        self._buf = []
        post = self._post
        if name == "text" and post is not None:
            post["text"] = _collapse_text(text)
        elif name == "views" and post is not None:
            post["view_count"] = parse_count(text)
        elif name == "reaction" and post is not None:
            post["reaction_count"] += parse_count(text)
        elif name == "author" and post is not None:
            post["author_name"] = " ".join(text.split())
        elif name == "channel_name":
            self.channel_name = " ".join(text.split())
        elif name == "counter_value":
            self._counter_value = text
        elif name == "counter_type":
            if "subscriber" in text.lower() and self._counter_value is not None:
                self.subscriber_count = parse_count(self._counter_value)
            self._counter_value = None

    def _finish_post(self) -> None:
        post = self._post
        self._post = None
        if post is None:
            return
        service = post.pop("_service", False)
        # Service rows ("channel photo updated") and media-only posts carry no
        # text the ranking could use.
        if service or not post["text"]:
            return
        self.posts.append(post)


def parse_tme_page(page: str, handle: str) -> dict[str, Any]:
    """Parse one preview page into the ScrapeCreators-shaped ``{channel, posts, before}``."""
    parser = _TmePageParser(handle)
    parser.feed(page or "")
    parser.close()
    return {
        "channel": {
            "handle": handle,
            "name": parser.channel_name or handle,
            "subscriber_count": parser.subscriber_count,
        },
        "posts": parser.posts,
        "before": parser.before,
        "wrappers": parser.wrappers,
    }


def _fetch_channel_posts_keyless(
    handle: str,
    *,
    from_date: str,
    topic: str,
    max_pages: int,
    fetch_text: Any = None,
) -> list[dict[str, Any]]:
    """Fetch posts from ``t.me/s/<handle>``, paginating with ``?before=<id>`` until the
    date cutoff or the page cap. Never raises: a fetch failure or a landing page
    ends the channel with a log line."""
    fetch_text = fetch_text or (lambda url: http.get_text(url, timeout=30, retries=1, accept="text/html"))
    max_pages = min(max_pages, KEYLESS_MAX_PAGES)
    items: list[dict[str, Any]] = []
    wrappers_seen = 0
    before: str | None = None
    seen_cursors: set[str] = set()
    pages_fetched = 0
    while pages_fetched < max_pages:
        url = f"{TME_BASE}/{quote(handle, safe='')}" + (f"?before={quote(before, safe='')}" if before else "")
        if pages_fetched:
            time.sleep(KEYLESS_PAGE_DELAY_SECONDS)
        _log(f"Fetching t.me/s/{handle} (page {pages_fetched + 1}/{max_pages}, keyless)")
        page = fetch_text(url)
        if page is None:
            _log(f"Could not fetch t.me/s/{handle}")
            break
        parsed = parse_tme_page(page, handle)
        pages_fetched += 1
        wrappers_seen += parsed["wrappers"]
        posts = parsed["posts"]
        if not posts and not parsed["wrappers"]:
            _log(f"No public posts at t.me/s/{handle} (private, empty, or nonexistent channel)")
            break
        page_all_old = not posts
        if posts:
            page_all_old = True
            # Pages list oldest first; rank newest first so the recency index
            # (rank_score in _parse_post) means the same as on the paid path.
            for idx, raw_post in enumerate(reversed(posts)):
                item = _parse_post(raw_post, parsed["channel"], topic, len(items) + idx)
                items.append(item)
                if item["date"] and item["date"] >= from_date:
                    page_all_old = False
        else:
            _log(f"Page {pages_fetched} of t.me/s/{handle} had message blocks but no text posts; continuing")
            page_all_old = False
        if page_all_old:
            _log(f"All posts on page older than {from_date}, stopping pagination")
            break
        cursor = parsed["before"]
        if not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
        before = cursor
    else:
        if before:
            _log(f"Page cap reached for t.me/s/{handle} ({max_pages}); older posts not fetched")
    items_meta = items  # keep the name explicit for the caller
    _KEYLESS_WRAPPERS[handle] = wrappers_seen
    return items_meta


# Per-run bookkeeping so search_telegram can tell "channel is empty" from
# "Telegram changed its markup": message blocks seen but nothing parsed.
_KEYLESS_WRAPPERS: dict[str, int] = {}


def search_telegram(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch recent posts from configured Telegram channels.

    Args:
        topic: Search topic (for relevance scoring)
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        token: ScrapeCreators API key
        config: Config dict (for TELEGRAM_SOURCES)

    Returns:
        Dict with 'items' list and optional 'error'.
    """
    config = config or {}

    backend = resolve_backend(config, token)
    if backend == BACKEND_SCRAPECREATORS and not token:
        pin = str(config.get(BACKEND_PIN_VAR) or os.environ.get(BACKEND_PIN_VAR) or "").strip().lower()
        if pin == BACKEND_SCRAPECREATORS:
            # A pin is an explicit choice; failing loudly matches what doctor predicts.
            return {"items": [], "error": f"{BACKEND_PIN_VAR}=scrapecreators but SCRAPECREATORS_API_KEY is not set"}
        _log("SCRAPECREATORS_API_KEY not set; using the keyless t.me/s backend")
        backend = BACKEND_KEYLESS

    channels = _get_channel_sources(config)
    if not channels:
        return {"items": [], "error": "No TELEGRAM_SOURCES configured (channel list required)"}

    base_cap = DEPTH_PAGE_CAPS.get(depth, DEPTH_PAGE_CAPS["default"])
    override = config.get("TELEGRAM_MAX_PAGES")
    if override:
        try:
            max_pages = max(base_cap, int(override))
        except (ValueError, TypeError):
            max_pages = base_cap
    else:
        max_pages = base_cap

    _log(
        f"Searching {len(channels)} channel(s) for '{topic}' "
        f"(depth={depth}, max_pages={max_pages}, backend={backend})"
    )

    all_items: list[dict[str, Any]] = []
    for handle in channels:
        if backend == BACKEND_KEYLESS:
            channel_items = _fetch_channel_posts_keyless(
                handle,
                from_date=from_date,
                topic=topic,
                max_pages=max_pages,
            )
        else:
            channel_items = _fetch_channel_posts(
                handle,
                token or "",
                from_date=from_date,
                topic=topic,
                max_pages=max_pages,
            )
        all_items.extend(channel_items)

    if backend == BACKEND_KEYLESS:
        blocks = sum(_KEYLESS_WRAPPERS.get(handle, 0) for handle in channels)
        _KEYLESS_WRAPPERS.clear()
        if blocks and not all_items:
            return {
                "items": [],
                "backend": backend,
                "error": f"t.me/s markup not recognized ({blocks} message block(s) seen, 0 parsed)",
            }
        if all_items and all(item["date"] is None for item in all_items):
            return {
                "items": [],
                "backend": backend,
                "error": "t.me/s timestamps not recognized (every post undated)",
            }

    in_range = [
        item for item in all_items
        if item["date"] and from_date <= item["date"] <= to_date
    ]
    out_of_range = len(all_items) - len(in_range)
    if in_range:
        items = in_range
        if out_of_range:
            _log(f"Filtered {out_of_range} posts outside date range")
    else:
        items = all_items
        _log(f"No posts within date range, keeping all {len(items)}")

    items.sort(key=lambda x: x.get("relevance", 0), reverse=True)

    _log(f"Found {len(items)} Telegram posts")
    return {"items": items, "backend": backend}


def parse_telegram_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse Telegram search response to normalized format.

    Returns:
        List of item dicts ready for normalization.
    """
    return response.get("items", [])
