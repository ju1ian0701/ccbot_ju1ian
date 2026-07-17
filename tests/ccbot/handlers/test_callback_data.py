"""Tests for REF-006 typed callback_data: parse, round-trip, validation, safety."""

from dataclasses import FrozenInstanceError

import pytest

from ccbot.handlers.callback_data import (
    CB_ASK_UP,
    CB_DIR_SELECT,
    CB_HISTORY_NEXT,
    CB_HISTORY_PREV,
    CB_KEYS_PREFIX,
    CB_SCREENSHOT_REFRESH,
    MAX_CALLBACK_DATA_BYTES,
    AskCallback,
    DirPageCallback,
    DirSelectCallback,
    HistoryCallback,
    KeyCallback,
    ScreenshotRefreshCallback,
    SessionSelectCallback,
    WinBindCallback,
    parse_ask,
    parse_dir_page,
    parse_dir_select,
    parse_history,
    parse_key,
    parse_screenshot_refresh,
    parse_session_select,
    parse_win_bind,
    safe_callback_data,
)


class TestHistoryParse:
    def test_current_format_newer(self):
        raw = f"{CB_HISTORY_NEXT}2:@12:100:500"
        cb = parse_history(raw)
        assert cb == HistoryCallback(
            older=False, page=2, window_id="@12", start_byte=100, end_byte=500
        )

    def test_legacy_format(self):
        raw = f"{CB_HISTORY_PREV}3:@9"
        cb = parse_history(raw)
        assert cb is not None
        assert cb.page == 3
        assert cb.window_id == "@9"
        assert cb.start_byte == 0
        assert cb.end_byte == 0

    def test_window_id_with_colons(self):
        raw = f"{CB_HISTORY_PREV}1:sess:abc:def:10:20"
        cb = parse_history(raw)
        assert cb is not None
        assert cb.window_id == "sess:abc:def"
        assert cb.start_byte == 10
        assert cb.end_byte == 20

    def test_malformed_returns_none(self):
        assert parse_history("hp:") is None
        assert parse_history("hp:not-a-number:@1:0:0") is None
        assert parse_history("noop") is None
        assert parse_history("db:sel:1") is None

    def test_negative_page_rejected(self):
        assert parse_history(f"{CB_HISTORY_PREV}-1:@1:0:0") is None


class TestIndexParsers:
    def test_dir_select(self):
        assert parse_dir_select(f"{CB_DIR_SELECT}7") == DirSelectCallback(index=7)
        assert parse_dir_select(f"{CB_DIR_SELECT}x") is None
        assert parse_dir_select("noop") is None

    def test_dir_page_win_session(self):
        assert parse_dir_page("db:page:3") == DirPageCallback(page=3)
        assert parse_win_bind("wb:sel:0") == WinBindCallback(index=0)
        assert parse_session_select("rs:sel:4") == SessionSelectCallback(index=4)


class TestWindowAndKeyParsers:
    def test_screenshot_refresh(self):
        cb = parse_screenshot_refresh(f"{CB_SCREENSHOT_REFRESH}@42")
        assert cb == ScreenshotRefreshCallback(window_id="@42")
        assert parse_screenshot_refresh(CB_SCREENSHOT_REFRESH) is None

    def test_ask(self):
        raw = f"{CB_ASK_UP}@7"
        cb = parse_ask(raw, CB_ASK_UP)
        assert cb == AskCallback(prefix=CB_ASK_UP, window_id="@7")
        assert parse_ask("aq:up:", CB_ASK_UP) is None

    def test_key(self):
        cb = parse_key(f"{CB_KEYS_PREFIX}spc:@3")
        assert cb == KeyCallback(key_id="spc", window_id="@3")
        assert parse_key(f"{CB_KEYS_PREFIX}nocolon") is None
        assert parse_key(f"{CB_KEYS_PREFIX}:@1") is None


class TestRoundTrip:
    @pytest.mark.parametrize(
        "cb",
        [
            HistoryCallback(
                older=True, page=0, window_id="@1", start_byte=0, end_byte=0
            ),
            HistoryCallback(
                older=False, page=2, window_id="@12", start_byte=10, end_byte=20
            ),
            DirSelectCallback(index=7),
            DirPageCallback(page=3),
            WinBindCallback(index=1),
            SessionSelectCallback(index=2),
            AskCallback(prefix=CB_ASK_UP, window_id="@5"),
            ScreenshotRefreshCallback(window_id="@9"),
            KeyCallback(key_id="esc", window_id="@0"),
        ],
    )
    def test_serialize_then_parse(self, cb):
        serialized = safe_callback_data(cb)
        parsers = {
            HistoryCallback: parse_history,
            DirSelectCallback: parse_dir_select,
            DirPageCallback: parse_dir_page,
            WinBindCallback: parse_win_bind,
            SessionSelectCallback: parse_session_select,
            AskCallback: lambda s: parse_ask(s, CB_ASK_UP),
            ScreenshotRefreshCallback: parse_screenshot_refresh,
            KeyCallback: parse_key,
        }
        parser = parsers[type(cb)]
        assert parser(serialized) == cb


class TestValidation:
    def test_negative_index_construction(self):
        with pytest.raises(ValueError, match="non-negative"):
            DirSelectCallback(index=-1)

    def test_empty_window_id(self):
        with pytest.raises(ValueError, match="window_id"):
            KeyCallback(key_id="up", window_id="")


class TestImmutability:
    def test_frozen_prevents_mutation(self):
        cb = HistoryCallback(older=True, page=0, window_id="@x")
        with pytest.raises(FrozenInstanceError):
            cb.page = 1  # type: ignore[misc]


class TestSafeSerialization:
    def test_within_limit(self):
        cb = HistoryCallback(older=True, page=0, window_id="@1")
        assert safe_callback_data(cb) == cb.to_old_string()

    def test_string_passthrough(self):
        assert safe_callback_data("noop") == "noop"
        assert safe_callback_data("db:up") == "db:up"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            safe_callback_data("")

    def test_exceeds_limit_raises(self):
        long_window = "w" * 100
        cb = KeyCallback(key_id="up", window_id=long_window)
        with pytest.raises(ValueError, match="too long"):
            safe_callback_data(cb)

    def test_multibyte_over_limit(self):
        # 22 * 3 bytes = 66 > 64
        payload = "€" * 22
        assert len(payload) < MAX_CALLBACK_DATA_BYTES
        assert len(payload.encode("utf-8")) > MAX_CALLBACK_DATA_BYTES
        with pytest.raises(ValueError, match="too long"):
            safe_callback_data(payload)
