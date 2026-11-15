"""Tests for the hook events channel (hook hook_events.py)."""

import json
import os
import stat
from pathlib import Path

import pytest

import ccbot.hook_events as he
from ccbot.hook_events import (
    EVENTS_FILE_NAME,
    MAX_FIELD_CHARS,
    HookEventReader,
    append_event,
    build_record,
)

SESSION_A = "550e8400-e29b-41d4-a716-446655440000"


def _payload(event: str, **extra: object) -> dict:
    return {"session_id": SESSION_A, "cwd": "/tmp", "hook_event_name": event, **extra}


def _line(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False) + "\n"


class TestBuildRecord:
    def test_notification_extras(self) -> None:
        rec = build_record(
            _payload(
                "Notification",
                notification_type="permission_prompt",
                message="Allow?",
                title="Claude",
                cwd="/tmp",
            ),
            "ccbot:@1",
            now=12.5,
        )
        assert rec == {
            "ts": 12.5,
            "event": "Notification",
            "window_key": "ccbot:@1",
            "session_id": SESSION_A,
            "notification_type": "permission_prompt",
            "message": "Allow?",
            "title": "Claude",
        }

    def test_stop_has_no_extras(self) -> None:
        rec = build_record(
            _payload("Stop", last_assistant_message="done", stop_hook_active=False),
            "ccbot:@1",
            now=1.0,
        )
        assert rec == {
            "ts": 1.0,
            "event": "Stop",
            "window_key": "ccbot:@1",
            "session_id": SESSION_A,
        }

    def test_stop_failure_extras(self) -> None:
        rec = build_record(
            _payload(
                "StopFailure",
                error="rate_limit",
                error_details="429",
                last_assistant_message="partial",
            ),
            "ccbot:@1",
            now=1.0,
        )
        assert rec["error"] == "rate_limit"
        assert rec["error_details"] == "429"
        assert rec["last_assistant_message"] == "partial"

    def test_session_end_extras(self) -> None:
        rec = build_record(_payload("SessionEnd", reason="clear"), "ccbot:@1", now=1.0)
        assert rec["reason"] == "clear"
        assert set(rec) == {"ts", "event", "window_key", "session_id", "reason"}

    def test_unknown_keys_excluded(self) -> None:
        rec = build_record(
            _payload("SessionEnd", reason="other", transcript_path="/x"),
            "ccbot:@1",
            now=1.0,
        )
        assert "transcript_path" not in rec

    def test_long_string_truncated(self) -> None:
        rec = build_record(
            _payload("StopFailure", error="x" * (MAX_FIELD_CHARS + 50)),
            "ccbot:@1",
            now=1.0,
        )
        assert rec["error"] == "x" * MAX_FIELD_CHARS

    def test_non_string_extras_dropped(self) -> None:
        rec = build_record(
            _payload("StopFailure", error=5, error_details=None, reason="a"),
            "ccbot:@1",
            now=1.0,
        )
        assert "error" not in rec
        assert "error_details" not in rec

    def test_now_defaults_to_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(he.time, "time", lambda: 99.0)
        assert build_record(_payload("Stop"), "ccbot:@1")["ts"] == 99.0


class TestAppendEvent:
    def test_creates_file_with_mode_and_appends_lines(self, tmp_path: Path) -> None:
        events_file = tmp_path / "sub" / EVENTS_FILE_NAME
        append_event(events_file, build_record(_payload("Stop"), "ccbot:@1", now=1.0))
        append_event(
            events_file,
            build_record(_payload("SessionEnd", reason="é"), "ccbot:@1", now=2.0),
        )
        assert stat.S_IMODE(events_file.stat().st_mode) == 0o600
        lines = events_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert [json.loads(x)["event"] for x in lines] == ["Stop", "SessionEnd"]
        assert json.loads(lines[1])["reason"] == "é"
        assert (tmp_path / "sub" / "hook_events.lock").exists()


def _write(path: Path, text: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def _rec(event: str = "Stop", window: str = "ccbot:@1", **extra: object) -> dict:
    return {
        "ts": 1.0,
        "event": event,
        "window_key": window,
        "session_id": SESSION_A,
        **extra,
    }


class TestHookEventReader:
    def test_start_at_end_skips_existing_lines(self, tmp_path: Path) -> None:
        events_file = tmp_path / EVENTS_FILE_NAME
        _write(events_file, _line(_rec()))
        reader = HookEventReader(events_file, "ccbot")
        assert reader.read_new() == []
        _write(events_file, _line(_rec("SessionEnd", reason="clear")))
        events = reader.read_new()
        assert len(events) == 1
        assert events[0].event == "SessionEnd"

    def test_start_at_beginning_returns_all(self, tmp_path: Path) -> None:
        events_file = tmp_path / EVENTS_FILE_NAME
        _write(events_file, _line(_rec()) + _line(_rec("StopFailure", error="x")))
        reader = HookEventReader(events_file, "ccbot", start_at_end=False)
        events = reader.read_new()
        assert [e.event for e in events] == ["Stop", "StopFailure"]
        assert events[1].data == {"error": "x"}

    def test_partial_line_held_until_complete(self, tmp_path: Path) -> None:
        events_file = tmp_path / EVENTS_FILE_NAME
        full = _line(_rec("SessionEnd", reason="other"))
        events_file.write_text(full[:20])
        reader = HookEventReader(events_file, "ccbot", start_at_end=False)
        assert reader.read_new() == []
        _write(events_file, full[20:])
        events = reader.read_new()
        assert len(events) == 1
        assert events[0].data == {"reason": "other"}

    def test_malformed_and_foreign_lines_skipped(self, tmp_path: Path) -> None:
        events_file = tmp_path / EVENTS_FILE_NAME
        _write(
            events_file,
            "{not json\n"
            + "[1, 2]\n"
            + _line({"event": "Stop", "session_id": SESSION_A})  # no window_key
            + _line(_rec(window="other:@1"))
            + _line(_rec(window="ccbot:@1", reason="ok")),
        )
        reader = HookEventReader(events_file, "ccbot", start_at_end=False)
        events = reader.read_new()
        assert len(events) == 1
        assert events[0].window_id == "@1"
        assert events[0].data == {"reason": "ok"}

    def test_event_fields_parsed(self, tmp_path: Path) -> None:
        events_file = tmp_path / EVENTS_FILE_NAME
        _write(events_file, _line(_rec("Stop", window="ccbot:@1")))
        reader = HookEventReader(events_file, "ccbot", start_at_end=False)
        (event,) = reader.read_new()
        assert event.ts == 1.0
        assert event.event == "Stop"
        assert event.window_id == "@1"
        assert event.session_id == SESSION_A
        assert event.data == {}

    def test_missing_ts_falls_back_to_zero(self, tmp_path: Path) -> None:
        events_file = tmp_path / EVENTS_FILE_NAME
        record = _rec()
        del record["ts"]
        _write(events_file, _line(record))
        reader = HookEventReader(events_file, "ccbot", start_at_end=False)
        assert reader.read_new()[0].ts == 0.0

    def test_truncation_resets_offset(self, tmp_path: Path) -> None:
        events_file = tmp_path / EVENTS_FILE_NAME
        _write(events_file, _line(_rec()) * 5)
        reader = HookEventReader(events_file, "ccbot", start_at_end=False)
        assert len(reader.read_new()) == 5
        events_file.write_text(_line(_rec("SessionEnd", reason="clear")))
        events = reader.read_new()
        assert [e.event for e in events] == ["SessionEnd"]

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        reader = HookEventReader(tmp_path / "nope.jsonl", "ccbot", start_at_end=False)
        assert reader.read_new() == []
        reader = HookEventReader(tmp_path / "nope.jsonl", "ccbot")
        assert reader.read_new() == []

    def test_io_error_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        events_file = tmp_path / EVENTS_FILE_NAME
        _write(events_file, _line(_rec()))
        reader = HookEventReader(events_file, "ccbot", start_at_end=False)

        def boom(*args, **kwargs):
            raise OSError("disk gone")

        monkeypatch.setattr("builtins.open", boom)
        assert reader.read_new() == []


class TestMaybeTruncate:
    def test_truncates_only_when_large_and_consumed(self, tmp_path: Path) -> None:
        events_file = tmp_path / EVENTS_FILE_NAME
        _write(events_file, _line(_rec()) * 10)
        reader = HookEventReader(events_file, "ccbot", start_at_end=False)

        # Not fully consumed yet: no truncation.
        assert reader.maybe_truncate(max_bytes=10) is False
        assert events_file.stat().st_size > 0

        assert len(reader.read_new()) == 10
        # Small enough: no truncation.
        assert reader.maybe_truncate(max_bytes=10_000_000) is False
        # Large and consumed: truncated.
        assert reader.maybe_truncate(max_bytes=10) is True
        assert events_file.stat().st_size == 0

        _write(events_file, _line(_rec("SessionEnd", reason="clear")))
        events = reader.read_new()
        assert [e.event for e in events] == ["SessionEnd"]

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        reader = HookEventReader(tmp_path / EVENTS_FILE_NAME, "ccbot")
        assert reader.maybe_truncate(max_bytes=0) is False

    def test_unread_appended_data_not_truncated(self, tmp_path: Path) -> None:
        events_file = tmp_path / EVENTS_FILE_NAME
        _write(events_file, _line(_rec()) * 3)
        reader = HookEventReader(events_file, "ccbot", start_at_end=False)
        reader.read_new()
        # A writer appended after the reader consumed everything.
        _write(events_file, _line(_rec()))
        assert reader.maybe_truncate(max_bytes=1) is False
        assert events_file.stat().st_size > 0
        assert os.path.getsize(events_file) == len(_line(_rec())) * 4
