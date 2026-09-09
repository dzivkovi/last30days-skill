"""Keyless Telegram backend (public t.me/s preview pages) for lib/telegram.py.

Fixture-driven: ``fixtures/telegram/tme_channel.html`` is a trimmed public
channel page (two text posts, one service row, a load-more cursor, channel
counters); ``tme_landing.html`` is what a private, empty, or nonexistent handle
renders. No test touches the network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib import backends, pipeline, telegram

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "telegram"
CHANNEL_PAGE = (FIXTURES / "tme_channel.html").read_text(encoding="utf-8")
LANDING_PAGE = (FIXTURES / "tme_landing.html").read_text(encoding="utf-8")


def _pages(mapping: dict[str, str]):
    calls: list[str] = []

    def fetch_text(url: str, **kwargs) -> str | None:
        calls.append(url)
        return mapping.get(url)

    return fetch_text, calls


# ---- counts and page parsing ----


@pytest.mark.parametrize(
    "raw,expected",
    [("24.2M", 24_200_000), ("1.5K", 1_500), ("477", 477), ("12,345", 12_345), ("", 0), ("n/a", 0)],
)
def test_parse_count_handles_suffixes_and_junk(raw, expected):
    assert telegram.parse_count(raw) == expected


def test_parse_tme_page_maps_posts_cursor_and_channel():
    parsed = telegram.parse_tme_page(CHANNEL_PAGE, "examplechannel")

    assert parsed["channel"] == {"handle": "examplechannel", "name": "Example Channel", "subscriber_count": 12_300}
    assert parsed["before"] == "101"
    assert [p["id"] for p in parsed["posts"]] == ["101", "102"]  # service row 103 is dropped
    first, second = parsed["posts"]
    assert first["url"] == "https://t.me/examplechannel/101"
    assert first["author_name"] == "Example Channel"
    assert first["published_at"] == "2026-08-20T10:00:00+00:00"
    assert first["view_count"] == 24_200
    assert first["reaction_count"] == 1_500 + 322 + 89
    assert first["text"] == "\U0001f3c6 Older post about AI agents and their memory.\nSecond paragraph & more."
    assert second["view_count"] == 1_100_000 and second["reaction_count"] == 0
    assert second["text"] == "Newest post: agents get long-term memory."


def test_parse_tme_page_landing_has_no_posts():
    parsed = telegram.parse_tme_page(LANDING_PAGE, "nosuchchannel")

    assert parsed["posts"] == [] and parsed["before"] is None
    assert parsed["channel"]["name"] == "nosuchchannel"


# ---- backend resolution ----


def test_resolve_backend_prefers_key_then_keyless(monkeypatch):
    monkeypatch.delenv(telegram.BACKEND_PIN_VAR, raising=False)
    assert telegram.resolve_backend({}) == telegram.BACKEND_KEYLESS
    assert telegram.resolve_backend({"SCRAPECREATORS_API_KEY": "k"}) == telegram.BACKEND_SCRAPECREATORS
    assert telegram.resolve_backend({}, token="k") == telegram.BACKEND_SCRAPECREATORS


def test_resolve_backend_honors_pin(monkeypatch):
    monkeypatch.delenv(telegram.BACKEND_PIN_VAR, raising=False)
    config = {"SCRAPECREATORS_API_KEY": "k", telegram.BACKEND_PIN_VAR: "keyless"}
    assert telegram.resolve_backend(config) == telegram.BACKEND_KEYLESS
    assert telegram.resolve_backend({telegram.BACKEND_PIN_VAR: "SCRAPECREATORS"}) == telegram.BACKEND_SCRAPECREATORS
    assert telegram.resolve_backend({telegram.BACKEND_PIN_VAR: "bogus"}) == telegram.BACKEND_KEYLESS


# ---- search through the keyless backend ----


def test_search_keyless_paginates_until_landing_page(monkeypatch):
    fetch_text, calls = _pages(
        {
            "https://t.me/s/examplechannel": CHANNEL_PAGE,
            "https://t.me/s/examplechannel?before=101": LANDING_PAGE,
        }
    )
    monkeypatch.setattr(telegram.http, "get_text", fetch_text)

    result = telegram.search_telegram(
        "AI agents memory", "2026-08-01", "2026-09-09", depth="deep", token=None, config={"TELEGRAM_SOURCES": "examplechannel"}
    )

    assert result["backend"] == telegram.BACKEND_KEYLESS
    assert calls == ["https://t.me/s/examplechannel", "https://t.me/s/examplechannel?before=101"]
    assert {item["id"] for item in result["items"]} == {"101", "102"}
    top = result["items"][0]
    assert top["engagement"]["subscribers"] == 12_300 and top["date"] in {"2026-08-20", "2026-09-08"}
    assert "error" not in result


def test_search_keyless_stops_when_page_is_all_old(monkeypatch):
    fetch_text, calls = _pages({"https://t.me/s/examplechannel": CHANNEL_PAGE})
    monkeypatch.setattr(telegram.http, "get_text", fetch_text)

    result = telegram.search_telegram(
        "agents", "2026-09-09", "2026-09-09", depth="deep", token=None, config={"TELEGRAM_SOURCES": "examplechannel"}
    )

    assert calls == ["https://t.me/s/examplechannel"]  # both posts predate from_date: no second page
    assert len(result["items"]) == 2  # out-of-range fallback keeps them, as with ScrapeCreators


def test_search_keyless_fetch_failure_is_empty_not_error(monkeypatch):
    monkeypatch.setattr(telegram.http, "get_text", lambda url, **kwargs: None)

    result = telegram.search_telegram("x", "2026-08-01", "2026-09-09", token=None, config={"TELEGRAM_SOURCES": "gone"})

    assert result["items"] == [] and "error" not in result


def test_pinned_scrapecreators_without_key_is_an_error_not_a_silent_fallback(monkeypatch):
    monkeypatch.setattr(telegram.http, "get_text", lambda url, **kwargs: pytest.fail("keyless must not run under a pin"))
    config = {"TELEGRAM_SOURCES": "examplechannel", telegram.BACKEND_PIN_VAR: "scrapecreators"}

    result = telegram.search_telegram("agents", "2026-08-01", "2026-09-09", token=None, config=config)

    assert result["items"] == [] and "SCRAPECREATORS_API_KEY" in result["error"]
    pinned = backends.resolve("telegram", config)
    assert pinned.active_backend is None and pinned.pinned


def test_search_with_key_uses_scrapecreators(monkeypatch):
    seen: list[str] = []

    def mock_get(url, **kwargs):
        seen.append(url)
        return {"success": True, "channel": {"handle": "c"}, "posts": [], "has_more": False}

    monkeypatch.setattr(telegram.http, "get", mock_get)
    monkeypatch.setattr(telegram.http, "get_text", lambda url, **kwargs: pytest.fail("keyless must not run"))

    result = telegram.search_telegram("agents", "2026-08-01", "2026-09-09", token="k", config={"TELEGRAM_SOURCES": "c"})

    assert result["backend"] == telegram.BACKEND_SCRAPECREATORS and seen and "scrapecreators.com" in seen[0]


def test_search_still_requires_channels():
    result = telegram.search_telegram("x", "2026-08-01", "2026-09-09", token=None, config={})

    assert result["items"] == [] and "TELEGRAM_SOURCES" in result["error"]


# ---- availability and doctor chain ----


def test_available_without_key_when_opted_in_with_channels():
    config = {"INCLUDE_SOURCES": "telegram", "TELEGRAM_SOURCES": "examplechannel"}

    assert "telegram" in pipeline.available_sources(config, local_only=True)


def test_backend_chain_predicts_keyless_without_key_and_scrapecreators_with_it(monkeypatch):
    monkeypatch.delenv(telegram.BACKEND_PIN_VAR, raising=False)
    descriptor = backends.get_descriptor("telegram")
    assert [spec.name for spec in descriptor.backends] == ["scrapecreators", "keyless"]
    assert descriptor.pin_var == telegram.BACKEND_PIN_VAR

    without_key = backends.resolve("telegram", {})
    with_key = backends.resolve("telegram", {"SCRAPECREATORS_API_KEY": "k"})

    assert without_key.active_backend == "keyless"
    assert with_key.active_backend == "scrapecreators"


def test_doctor_record_carries_the_chain_prediction(monkeypatch):
    from lib import doctor

    monkeypatch.delenv(telegram.BACKEND_PIN_VAR, raising=False)
    config = {"INCLUDE_SOURCES": "telegram", "TELEGRAM_SOURCES": "examplechannel"}

    record = doctor._telegram_record(config)

    assert record["status"] == "ok" and record["active_backend"] == "keyless"
    assert record["pin_var"] == telegram.BACKEND_PIN_VAR and "1 channel(s)" in record["detail"]
