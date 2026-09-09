"""Review-driven hardening of the keyless Telegram backend.

Each test pins one finding from the PR #26 review: the second load-more anchor
must not blank the cursor, pages rank newest first, unbalanced markup cannot
swallow the next post, a media-only page continues pagination, markup drift
and undated pages are errors rather than silent successes, handles are
validated before reaching a URL, and doctor's branches match the runtime.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib import backends, doctor, telegram

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "telegram"
PAGE1 = (FIXTURES / "tme_channel.html").read_text(encoding="utf-8")
PAGE2 = (FIXTURES / "tme_channel_page2.html").read_text(encoding="utf-8")
LANDING = (FIXTURES / "tme_landing.html").read_text(encoding="utf-8")
BASE = "https://t.me/s/examplechannel"


@pytest.fixture(autouse=True)
def _fast_and_offline(monkeypatch):
    monkeypatch.delenv(telegram.BACKEND_PIN_VAR, raising=False)
    monkeypatch.setattr(telegram, "KEYLESS_PAGE_DELAY_SECONDS", 0)


def _serve(monkeypatch, mapping: dict[str, str]) -> list[str]:
    calls: list[str] = []

    def fetch_text(url: str, **kwargs) -> str | None:
        calls.append(url)
        return mapping.get(url)

    monkeypatch.setattr(telegram.http, "get_text", fetch_text)
    return calls


def _search(**overrides):
    options = dict(depth="deep", token=None, config={"TELEGRAM_SOURCES": "examplechannel"})
    options.update(overrides)
    return telegram.search_telegram("agents memory", "2026-08-01", "2026-09-09", **options)


# ---- pagination ----


def test_page_two_keeps_the_before_cursor_despite_the_after_anchor():
    parsed = telegram.parse_tme_page(PAGE2, "examplechannel")

    assert parsed["before"] == "97"  # href-only anchor, no data-before; the ?after= anchor is ignored
    assert parsed["wrappers"] == 3 and [p["id"] for p in parsed["posts"]] == ["97", "100"]


def test_three_pages_are_walked_until_the_landing_page(monkeypatch):
    calls = _serve(monkeypatch, {BASE: PAGE1, f"{BASE}?before=101": PAGE2, f"{BASE}?before=97": LANDING})

    result = _search()

    assert calls == [BASE, f"{BASE}?before=101", f"{BASE}?before=97"]
    assert {i["id"] for i in result["items"]} == {"101", "102", "97", "100"}


def test_page_cap_stops_the_walk_and_quick_depth_is_one_page(monkeypatch):
    calls = _serve(monkeypatch, {BASE: PAGE1, f"{BASE}?before=101": PAGE2})

    result = _search(depth="quick")

    assert calls == [BASE] and len(result["items"]) == 2


def test_missing_cursor_ends_pagination(monkeypatch):
    calls = _serve(monkeypatch, {BASE: PAGE1.replace('data-before="101"', "").replace("?before=101", "")})

    _search()

    assert calls == [BASE]


def test_repeated_cursor_does_not_loop(monkeypatch):
    looping = PAGE2.replace("?before=97", "?before=101")
    calls = _serve(monkeypatch, {BASE: PAGE1, f"{BASE}?before=101": looping})

    _search()

    assert calls == [BASE, f"{BASE}?before=101"]


def test_newest_post_on_a_page_gets_the_best_recency_rank(monkeypatch):
    _serve(monkeypatch, {BASE: PAGE1})

    result = _search()

    by_id = {i["id"]: i for i in result["items"]}
    assert by_id["102"]["relevance"] >= by_id["101"]["relevance"]  # 102 is newer and ranked first on the page


# ---- markup robustness ----


def test_unbalanced_tag_in_one_post_does_not_swallow_the_next():
    parsed = telegram.parse_tme_page(PAGE2, "examplechannel")

    first, last = parsed["posts"]
    assert first["text"] == "Unclosed bold run about agents and memory"
    assert first["published_at"] == "2026-08-18T09:00:00+00:00" and first["view_count"] == 500
    assert last["text"] == "Agents roundup, second page." and last["view_count"] == 900


def test_media_only_post_is_skipped_but_the_page_continues(monkeypatch):
    calls = _serve(monkeypatch, {BASE: PAGE2, f"{BASE}?before=97": LANDING})

    result = _search()

    assert "98" not in {i["id"] for i in result["items"]}
    assert calls == [BASE, f"{BASE}?before=97"]


def test_markup_drift_is_an_error_not_an_empty_success(monkeypatch):
    drifted = PAGE1.replace("tgme_widget_message_text", "tgme_widget_message_txt")
    _serve(monkeypatch, {BASE: drifted, f"{BASE}?before=101": LANDING})

    result = _search()

    assert result["items"] == [] and "markup not recognized" in result["error"]


def test_undated_posts_are_an_error_not_in_range_items(monkeypatch):
    undated = PAGE1.replace('datetime="2026-08-20T10:00:00+00:00"', "").replace('datetime="2026-09-08T18:30:00+00:00"', "")
    _serve(monkeypatch, {BASE: undated})

    result = _search()

    assert result["items"] == [] and "timestamps" in result["error"]


def test_parse_count_never_raises_on_hostile_numbers():
    assert telegram.parse_count("1e400") == 0 and telegram.parse_count("inf") == 0 and telegram.parse_count("2B") == 2_000_000_000


# ---- handles and routing ----


@pytest.mark.parametrize("raw", ["ab", "bad?query", "a/b", "../etc", "with space", "x" * 33])
def test_invalid_usernames_are_rejected_before_any_url(raw):
    with pytest.raises(telegram.InvalidChannelHandle):
        telegram.parse_channel_handle(raw)


def test_valid_usernames_still_pass():
    assert telegram.parse_channel_handle("Some_Chan9") == "Some_Chan9"
    assert telegram.parse_channel_sources("durov, bad?x, aipost") == ["durov", "aipost"]


def test_pin_is_read_from_config_only_and_whitespace_tolerant():
    config = {"TELEGRAM_SOURCES": "examplechannel", telegram.BACKEND_PIN_VAR: "  Keyless "}
    assert telegram.resolve_backend(config) == telegram.BACKEND_KEYLESS
    assert backends.resolve("telegram", config).active_backend == "keyless"


def test_unknown_pin_is_ignored_with_a_log_line(capsys):
    config = {"SCRAPECREATORS_API_KEY": "k", telegram.BACKEND_PIN_VAR: "keyles"}

    assert telegram.resolve_backend(config) == telegram.BACKEND_SCRAPECREATORS
    assert "Ignoring unknown" in capsys.readouterr().err


def test_doctor_branches_match_the_runtime():
    no_channels = doctor._telegram_record({"INCLUDE_SOURCES": "telegram"})
    assert no_channels["status"] == "unconfigured" and "key present" not in no_channels["note"]

    not_opted_in = doctor._telegram_record({"TELEGRAM_SOURCES": "examplechannel"})
    assert not_opted_in["status"] == "opt-in" and "keyless" in not_opted_in["note"]

    pinned_without_key = doctor._telegram_record(
        {"INCLUDE_SOURCES": "telegram", "TELEGRAM_SOURCES": "examplechannel", telegram.BACKEND_PIN_VAR: "scrapecreators"}
    )
    assert pinned_without_key["status"] != "ok" and pinned_without_key["pinned"]
