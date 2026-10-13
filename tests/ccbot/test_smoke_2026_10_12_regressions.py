"""Regression tests for incidents found by the 2026-10-12 smoke run.

Covers: Claude Code session fork (continued-in) following in the monitor and the
hook diagnostics (incident 1), damaged state.json handling (incident 2), topic
rename surviving session_map sync (incident 4), and the voice install hint
(incident 6).
"""

import io
import json
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import ccbot.session as session_mod
import ccbot.transcribe as transcribe_mod
from ccbot.hook import hook_main
from ccbot.session import SessionManager
from ccbot.session_monitor import SessionMonitor

OLD_SID = "318e5ba6-39bb-4576-82da-ce2362d29fe6"
NEW_SID = "21171c80-7c8f-46ee-bbe1-bdd73a2196a2"


# ── Incident 1: session fork (continued-in) ──────────────────────────────


@pytest.fixture
def monitor(tmp_path) -> SessionMonitor:
    return SessionMonitor(
        projects_path=tmp_path / "projects",
        state_file=tmp_path / "monitor_state.json",
    )


class TestContinuedIn:
    def test_collect_records_new_session(self, monitor: SessionMonitor) -> None:
        entries = [
            {"type": "assistant"},
            {
                "type": "continued-in",
                "sessionId": OLD_SID,
                "continuedInSessionId": NEW_SID,
            },
        ]
        monitor._collect_continuations(OLD_SID, entries)
        assert monitor._continuations == {OLD_SID: NEW_SID}

    @pytest.mark.parametrize(
        "entry",
        [
            {"type": "continued-in"},
            {"type": "continued-in", "continuedInSessionId": ""},
            {"type": "continued-in", "continuedInSessionId": 123},
            {"type": "continued-in", "continuedInSessionId": OLD_SID},
            {"type": "assistant", "continuedInSessionId": NEW_SID},
        ],
        ids=["missing", "empty", "not-str", "self", "wrong-type"],
    )
    def test_collect_ignores_invalid(self, monitor: SessionMonitor, entry) -> None:
        monitor._collect_continuations(OLD_SID, [entry])
        assert monitor._continuations == {}

    @pytest.mark.asyncio
    async def test_read_new_lines_then_collect(
        self, monitor: SessionMonitor, tmp_path
    ) -> None:
        """The record written last in the old transcript is picked up end to end."""
        from ccbot.monitor_state import TrackedSession

        old_file = tmp_path / f"{OLD_SID}.jsonl"
        lines = [
            {"type": "user", "message": {"content": "hi"}},
            {
                "type": "continued-in",
                "timestamp": "2026-10-12T18:44:42.164Z",
                "sessionId": OLD_SID,
                "continuedInSessionId": NEW_SID,
            },
        ]
        old_file.write_bytes(
            ("\n".join(json.dumps(x) for x in lines) + "\n").encode("utf-8")
        )
        tracked = TrackedSession(
            session_id=OLD_SID, file_path=str(old_file), last_byte_offset=0
        )
        entries = await monitor._read_new_lines(tracked, old_file)
        monitor._collect_continuations(OLD_SID, entries)
        assert monitor._continuations == {OLD_SID: NEW_SID}

    @pytest.mark.asyncio
    async def test_apply_repoints_window_and_clears(
        self, monitor: SessionMonitor, monkeypatch
    ) -> None:
        override = AsyncMock()
        monkeypatch.setattr(
            session_mod.session_manager, "override_session_map_entry", override
        )
        monitor._continuations = {OLD_SID: NEW_SID}

        await monitor._apply_continuations({"@2": OLD_SID, "@5": "other-session"})

        override.assert_awaited_once_with("@2", NEW_SID)
        assert monitor._continuations == {}

    @pytest.mark.asyncio
    async def test_apply_drops_continuation_without_window(
        self, monitor: SessionMonitor, monkeypatch
    ) -> None:
        override = AsyncMock()
        monkeypatch.setattr(
            session_mod.session_manager, "override_session_map_entry", override
        )
        monitor._continuations = {OLD_SID: NEW_SID}

        await monitor._apply_continuations({"@5": "other-session"})

        override.assert_not_awaited()
        assert monitor._continuations == {}


class TestHookWithoutTmuxPane:
    def test_warning_carries_diagnostics_and_map_untouched(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path, caplog
    ) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        monkeypatch.setattr(sys, "argv", ["ccbot", "hook"])
        payload = {
            "session_id": NEW_SID,
            "cwd": "/tmp",
            "hook_event_name": "SessionStart",
            "source": "fork",
        }
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        monkeypatch.delenv("TMUX_PANE", raising=False)

        with caplog.at_level(logging.WARNING, logger="ccbot.hook"):
            hook_main()

        text = caplog.text
        assert "TMUX_PANE not set" in text
        assert NEW_SID in text
        assert "source=fork" in text
        assert not (tmp_path / "session_map.json").exists()


# ── Incident 2: damaged state.json ───────────────────────────────────────

GOOD_STATE = {
    "window_states": {"@1": {"session_id": "sid-1", "cwd": "/p", "window_name": "p"}},
    "thread_bindings": {"100": {"1": "@1"}},
    "window_display_names": {"@1": "p"},
}


@pytest.fixture
def state_path(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    monkeypatch.setattr(session_mod.config, "state_file", path)
    return path


class TestDamagedStateFile:
    @pytest.mark.parametrize(
        "content",
        [
            "",
            '{"window_states": {"@1": ',
            "[]",
            '"just a string"',
            "42",
            '{"window_states": []}',
            '{"window_states": {"@1": 5}}',
            '{"thread_bindings": []}',
            '{"thread_bindings": {"1": []}}',
            '{"thread_bindings": {"not-a-number": {}}}',
            '{"user_window_offsets": []}',
        ],
        ids=[
            "empty",
            "truncated",
            "root-list",
            "root-string",
            "root-number",
            "window-states-list",
            "window-state-not-object",
            "bindings-list",
            "user-bindings-list",
            "non-numeric-user-id",
            "offsets-list",
        ],
    )
    def test_startup_survives_and_preserves_file(self, state_path, content) -> None:
        state_path.write_text(content)

        mgr = SessionManager()  # must not raise

        assert mgr.thread_bindings == {}
        assert mgr.window_states == {}
        preserved = list(state_path.parent.glob("state.json.corrupt-*"))
        assert len(preserved) == 1
        assert preserved[0].read_text() == content

    def test_restores_from_backup(self, state_path) -> None:
        state_path.with_name("state.json.bak").write_text(json.dumps(GOOD_STATE))
        state_path.write_text("[]")

        mgr = SessionManager()

        assert mgr.get_window_for_thread(100, 1) == "@1"
        assert list(state_path.parent.glob("state.json.corrupt-*"))

    def test_unusable_backup_falls_back_to_empty(self, state_path) -> None:
        state_path.with_name("state.json.bak").write_text("also broken")
        state_path.write_text("[]")

        mgr = SessionManager()

        assert mgr.thread_bindings == {}

    def test_valid_state_loads_without_artifacts(self, state_path) -> None:
        state_path.write_text(json.dumps(GOOD_STATE))

        mgr = SessionManager()

        assert mgr.get_window_for_thread(100, 1) == "@1"
        assert not list(state_path.parent.glob("state.json.corrupt-*"))

    def test_save_keeps_last_valid_state_as_backup(self, state_path) -> None:
        state_path.write_text(json.dumps(GOOD_STATE))
        mgr = SessionManager()
        backup = state_path.with_name("state.json.bak")

        mgr._save_state()
        assert json.loads(backup.read_text()) == GOOD_STATE

        # A damaged file must never replace the last good backup.
        state_path.write_text("[]")
        mgr._save_state()
        assert json.loads(backup.read_text()) == GOOD_STATE


# ── Incident 4: topic rename must survive session_map sync ───────────────


class TestRenameSurvivesSync:
    @pytest.fixture
    def mgr(self, monkeypatch, tmp_path) -> SessionManager:
        monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
        monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
        map_file = tmp_path / "session_map.json"
        map_file.write_text(
            json.dumps(
                {
                    "ccbot:@2": {
                        "session_id": "sid-1",
                        "cwd": "/proj",
                        "window_name": "test",
                    }
                }
            )
        )
        monkeypatch.setattr(session_mod.config, "session_map_file", map_file)
        self.map_file = map_file
        return SessionManager()

    @pytest.mark.asyncio
    async def test_rename_is_stable_after_sync(self, mgr: SessionManager) -> None:
        await mgr.load_session_map()
        mgr.update_display_name("@2", "renamed")
        await mgr.rename_session_map_window("@2", "renamed")

        await mgr.load_session_map()

        assert mgr.get_display_name("@2") == "renamed"
        entry = json.loads(self.map_file.read_text())["ccbot:@2"]
        assert entry["window_name"] == "renamed"
        assert entry["session_id"] == "sid-1"

    @pytest.mark.asyncio
    async def test_rename_unknown_window_is_noop(self, mgr: SessionManager) -> None:
        before = self.map_file.read_text()
        await mgr.rename_session_map_window("@99", "x")
        assert self.map_file.read_text() == before


# ── Incident 6: voice install hint ───────────────────────────────────────


class TestVoiceInstallHint:
    @pytest.mark.asyncio
    async def test_openai_without_key_mentions_uv_tool(self, monkeypatch) -> None:
        monkeypatch.setattr(transcribe_mod.config, "openai_api_key", "")
        with pytest.raises(transcribe_mod.TranscriptionDisabled) as exc:
            await transcribe_mod._transcribe_openai(Path("unused.ogg"))
        message = str(exc.value)
        assert "OPENAI_API_KEY" in message
        assert "uv tool" in message

    def test_hint_covers_checkout_and_uv_tool(self) -> None:
        hint = transcribe_mod._VOICE_INSTALL_HINT
        assert "uv pip install" in hint
        assert "uv tool" in hint
