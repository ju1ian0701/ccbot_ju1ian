"""Tests for typed callback_data encode/parse round-trips."""

from ccbot.handlers.callback_data import (
    CALLBACK_DATA_MAX,
    CB_ASK_UP,
    CB_DIR_SELECT,
    CB_HISTORY_NEXT,
    CB_HISTORY_PREV,
    CB_KEYS_PREFIX,
    CB_SCREENSHOT_REFRESH,
    HistoryCallback,
    clip_callback_data,
    encode_ask,
    encode_dir_page,
    encode_dir_select,
    encode_history,
    encode_history_page,
    encode_key,
    encode_screenshot_refresh,
    encode_session_select,
    encode_win_bind,
    parse_ask,
    parse_dir_page,
    parse_dir_select,
    parse_history,
    parse_key,
    parse_screenshot_refresh,
    parse_session_select,
    parse_win_bind,
)


class TestClipCallbackData:
    def test_short_unchanged(self):
        assert clip_callback_data("hp:1:@0:0:0") == "hp:1:@0:0:0"

    def test_truncates_to_max(self):
        long = "x" * (CALLBACK_DATA_MAX + 20)
        assert len(clip_callback_data(long)) == CALLBACK_DATA_MAX


class TestHistoryCallback:
    def test_encode_round_trip_newer(self):
        raw = encode_history_page(
            older=False,
            page=2,
            window_id="@12",
            start_byte=100,
            end_byte=500,
        )
        assert raw == f"{CB_HISTORY_NEXT}2:@12:100:500"
        parsed = parse_history(raw)
        assert parsed is not None
        assert parsed.older is False
        assert parsed.page == 2
        assert parsed.window_id == "@12"
        assert parsed.start_byte == 100
        assert parsed.end_byte == 500

    def test_encode_round_trip_older(self):
        raw = encode_history(
            HistoryCallback(
                older=True, page=0, window_id="@1", start_byte=0, end_byte=0
            )
        )
        assert raw.startswith(CB_HISTORY_PREV)
        parsed = parse_history(raw)
        assert parsed is not None
        assert parsed.older is True
        assert parsed.page == 0
        assert parsed.window_id == "@1"

    def test_parse_legacy_format(self):
        raw = f"{CB_HISTORY_PREV}3:@9"
        parsed = parse_history(raw)
        assert parsed is not None
        assert parsed.page == 3
        assert parsed.window_id == "@9"
        assert parsed.start_byte == 0
        assert parsed.end_byte == 0

    def test_window_id_with_colons(self):
        raw = encode_history_page(
            older=True,
            page=1,
            window_id="sess:abc:def",
            start_byte=10,
            end_byte=20,
        )
        parsed = parse_history(raw)
        assert parsed is not None
        assert parsed.window_id == "sess:abc:def"
        assert parsed.start_byte == 10
        assert parsed.end_byte == 20

    def test_invalid_returns_none(self):
        assert parse_history("hp:not-a-number:@1:0:0") is None
        assert parse_history("hp:") is None
        assert parse_history("noop") is None
        assert parse_history("db:sel:1") is None


class TestIndexCallbacks:
    def test_dir_select_round_trip(self):
        raw = encode_dir_select(7)
        assert raw == f"{CB_DIR_SELECT}7"
        parsed = parse_dir_select(raw)
        assert parsed is not None
        assert parsed.index == 7

    def test_dir_page_round_trip(self):
        raw = encode_dir_page(3)
        assert parse_dir_page(raw) is not None
        assert parse_dir_page(raw).index == 3  # type: ignore[union-attr]

    def test_win_bind_and_session_select(self):
        assert parse_win_bind(encode_win_bind(0)).index == 0  # type: ignore[union-attr]
        assert parse_session_select(encode_session_select(4)).index == 4  # type: ignore[union-attr]

    def test_invalid_index(self):
        assert parse_dir_select(f"{CB_DIR_SELECT}x") is None
        assert parse_dir_select("noop") is None


class TestWindowAndKeyCallbacks:
    def test_screenshot_refresh_round_trip(self):
        raw = encode_screenshot_refresh("@42")
        assert raw == f"{CB_SCREENSHOT_REFRESH}@42"
        parsed = parse_screenshot_refresh(raw)
        assert parsed is not None
        assert parsed.window_id == "@42"

    def test_ask_round_trip(self):
        raw = encode_ask(CB_ASK_UP, "@7")
        parsed = parse_ask(raw, CB_ASK_UP)
        assert parsed is not None
        assert parsed.window_id == "@7"
        assert parse_ask(raw, CB_ASK_UP) is not None
        assert parse_ask("aq:up:", CB_ASK_UP) is None  # empty window

    def test_key_round_trip(self):
        raw = encode_key("spc", "@3")
        assert raw == f"{CB_KEYS_PREFIX}spc:@3"
        parsed = parse_key(raw)
        assert parsed is not None
        assert parsed.key_id == "spc"
        assert parsed.window_id == "@3"

    def test_key_window_with_colons(self):
        parsed = parse_key(encode_key("up", "a:b:c"))
        assert parsed is not None
        assert parsed.key_id == "up"
        assert parsed.window_id == "a:b:c"

    def test_key_invalid(self):
        assert parse_key(f"{CB_KEYS_PREFIX}nocolon") is None
        assert parse_key(f"{CB_KEYS_PREFIX}:@1") is None  # empty key_id
        assert parse_key("noop") is None

    def test_encode_clips_long_payload(self):
        long_window = "w" * 80
        raw = encode_key("up", long_window)
        assert len(raw) == CALLBACK_DATA_MAX
