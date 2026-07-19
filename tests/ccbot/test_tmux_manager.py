"""Unit tests for TmuxManager hot paths (REF-009) — no real tmux required."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ccbot.tmux_manager import TmuxManager, TmuxWindow


@pytest.fixture
def mgr() -> TmuxManager:
    return TmuxManager(session_name="ccbot-test")


def _fake_window(
    window_id: str,
    window_name: str,
    cwd: str = "/tmp",
    pane_cmd: str = "claude",
) -> MagicMock:
    win = MagicMock()
    win.window_id = window_id
    win.window_name = window_name
    pane = MagicMock()
    pane.pane_current_path = cwd
    pane.pane_current_command = pane_cmd
    pane.capture_pane.return_value = ["line1", "line2"]
    win.active_pane = pane
    return win


class TestGetSession:
    def test_returns_none_on_error(self, mgr: TmuxManager) -> None:
        server = MagicMock()
        server.sessions.get.side_effect = RuntimeError("no server")
        mgr._server = server
        assert mgr.get_session() is None

    def test_returns_session(self, mgr: TmuxManager) -> None:
        sess = MagicMock()
        server = MagicMock()
        server.sessions.get.return_value = sess
        mgr._server = server
        assert mgr.get_session() is sess


class TestListWindows:
    @pytest.mark.asyncio
    async def test_empty_when_no_session(self, mgr: TmuxManager) -> None:
        with patch.object(mgr, "get_session", return_value=None):
            assert await mgr.list_windows() == []

    @pytest.mark.asyncio
    async def test_skips_main_window(self, mgr: TmuxManager) -> None:
        from ccbot import config as cfg

        main_name = cfg.config.tmux_main_window_name
        sess = MagicMock()
        sess.windows = [
            _fake_window("@0", main_name),
            _fake_window("@2", "work", "/home/u/proj"),
        ]
        with patch.object(mgr, "get_session", return_value=sess):
            windows = await mgr.list_windows()
        assert len(windows) == 1
        assert windows[0] == TmuxWindow(
            window_id="@2",
            window_name="work",
            cwd="/home/u/proj",
            pane_current_command="claude",
        )

    @pytest.mark.asyncio
    async def test_find_window_by_id(self, mgr: TmuxManager) -> None:
        w1 = TmuxWindow(window_id="@1", window_name="a", cwd="/a")
        w2 = TmuxWindow(window_id="@2", window_name="b", cwd="/b")
        with patch.object(mgr, "list_windows", return_value=[w1, w2]):
            found = await mgr.find_window_by_id("@2")
            missing = await mgr.find_window_by_id("@9")
        assert found is w2
        assert missing is None

    @pytest.mark.asyncio
    async def test_find_window_by_name(self, mgr: TmuxManager) -> None:
        w1 = TmuxWindow(window_id="@1", window_name="alpha", cwd="/a")
        with patch.object(mgr, "list_windows", return_value=[w1]):
            assert await mgr.find_window_by_name("alpha") is w1
            assert await mgr.find_window_by_name("nope") is None


class TestScrubEnv:
    def test_scrub_swallows_missing_vars(self, mgr: TmuxManager) -> None:
        sess = MagicMock()
        sess.unset_environment.side_effect = Exception("not set")
        mgr._scrub_session_env(sess)
        assert sess.unset_environment.called


class TestCapturePane:
    @pytest.mark.asyncio
    async def test_plain_capture_joins_lines(self, mgr: TmuxManager) -> None:
        sess = MagicMock()
        win = _fake_window("@1", "w")
        win.active_pane.capture_pane.return_value = ["line1", "line2"]
        sess.windows.get.return_value = win
        with patch.object(mgr, "get_session", return_value=sess):
            text = await mgr.capture_pane("@1", with_ansi=False)
        assert text == "line1\nline2"

    @pytest.mark.asyncio
    async def test_plain_capture_none_without_session(self, mgr: TmuxManager) -> None:
        with patch.object(mgr, "get_session", return_value=None):
            assert await mgr.capture_pane("@1", with_ansi=False) is None


class TestCreateWindowValidation:
    @pytest.mark.asyncio
    async def test_rejects_missing_directory(self, mgr: TmuxManager) -> None:
        ok, msg, name, wid = await mgr.create_window(
            "/nonexistent/path/ccbot-ref009",
            start_claude=False,
        )
        assert ok is False
        assert "does not exist" in msg
        assert name == ""
        assert wid == ""

    @pytest.mark.asyncio
    async def test_rejects_non_uuid_resume(self, mgr: TmuxManager, tmp_path) -> None:
        ok, msg, name, wid = await mgr.create_window(
            str(tmp_path),
            start_claude=False,
            resume_session_id="not-a-uuid",
        )
        assert ok is False
        assert "Invalid session ID" in msg
