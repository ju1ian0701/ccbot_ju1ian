"""Tests for session_lifecycle: shell detection, end_session, shell guard, keyboards."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from ccbot.handlers.callback_data import (
    CB_SL_CANCEL,
    CB_SL_KILL_ASK,
    CB_SL_KILL_CLOSE,
    CB_SL_KILL_OK,
    CB_SL_RESTART,
)
from ccbot.handlers.session_lifecycle import (
    build_kill_confirm_keyboard,
    build_not_running_keyboard,
    end_session,
    is_shell_command,
    reply_if_claude_not_running,
)
from ccbot.session_guard import SessionContext
from ccbot.tmux_manager import TmuxWindow

_M = "ccbot.handlers.session_lifecycle"
_MH = "ccbot.handlers.message_handlers"


def _win(cmd: str = "claude", wid: str = "@8") -> TmuxWindow:
    return TmuxWindow(
        window_id=wid, window_name="proj", cwd="/t", pane_current_command=cmd
    )


def _tmux(find_side_effect: list | None = None) -> MagicMock:
    tmux = MagicMock()
    tmux.find_window_by_id = AsyncMock(side_effect=find_side_effect)
    tmux.send_keys = AsyncMock(return_value=True)
    tmux.kill_window = AsyncMock(return_value=True)
    tmux.capture_pane = AsyncMock(return_value=None)
    return tmux


@pytest.fixture
def fake_sleep():
    """Replace asyncio inside the module under test so no real sleeping happens."""
    fake = MagicMock()
    fake.sleep = AsyncMock()
    with patch(f"{_M}.asyncio", fake):
        yield fake.sleep


# ── is_shell_command ─────────────────────────────────────────────────────


@pytest.mark.parametrize("cmd", ["bash", "-bash", "zsh", "-zsh", "sh", "fish"])
def test_is_shell_command_true(cmd: str) -> None:
    assert is_shell_command(cmd) is True


@pytest.mark.parametrize("cmd", ["claude", "node", ""])
def test_is_shell_command_false(cmd: str) -> None:
    assert is_shell_command(cmd) is False


# ── end_session ──────────────────────────────────────────────────────────


async def test_end_session_claude_running_exits_gracefully_and_kills(
    fake_sleep,
) -> None:
    tmux = _tmux(find_side_effect=[_win("claude"), None])
    bot = AsyncMock()
    user_data: dict = {}
    with (
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.session_manager") as sm,
        patch(f"{_M}.clear_topic_state", new_callable=AsyncMock) as clear,
    ):
        result = await end_session(bot, 1, 42, "@8", user_data=user_data)

    assert result is True
    assert tmux.send_keys.await_args_list == [
        call("@8", "Escape", enter=False, literal=False),
        call("@8", "/exit"),
    ]
    tmux.kill_window.assert_awaited_once_with("@8")
    sm.unbind_thread.assert_called_once_with(1, 42)
    clear.assert_awaited_once_with(1, 42, bot, user_data)


async def test_end_session_window_already_shell_no_keys(fake_sleep) -> None:
    tmux = _tmux(find_side_effect=[_win("bash")])
    with (
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.session_manager") as sm,
        patch(f"{_M}.clear_topic_state", new_callable=AsyncMock),
    ):
        result = await end_session(AsyncMock(), 1, 42, "@8")

    assert result is True
    tmux.send_keys.assert_not_awaited()
    tmux.kill_window.assert_awaited_once_with("@8")
    sm.unbind_thread.assert_called_once_with(1, 42)


async def test_end_session_window_gone_cleans_up_only(fake_sleep) -> None:
    tmux = _tmux(find_side_effect=[None])
    bot = AsyncMock()
    with (
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.session_manager") as sm,
        patch(f"{_M}.clear_topic_state", new_callable=AsyncMock) as clear,
    ):
        result = await end_session(bot, 1, 42, "@8")

    assert result is False
    tmux.send_keys.assert_not_awaited()
    tmux.kill_window.assert_not_awaited()
    sm.unbind_thread.assert_called_once_with(1, 42)
    clear.assert_awaited_once_with(1, 42, bot, None)


async def test_end_session_graceful_exception_still_kills_and_unbinds(
    fake_sleep,
) -> None:
    tmux = _tmux(find_side_effect=[_win("claude")])
    tmux.send_keys = AsyncMock(side_effect=RuntimeError("tmux exploded"))
    with (
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.session_manager") as sm,
        patch(f"{_M}.clear_topic_state", new_callable=AsyncMock) as clear,
    ):
        result = await end_session(AsyncMock(), 1, 42, "@8")

    assert result is True
    tmux.kill_window.assert_awaited_once_with("@8")
    sm.unbind_thread.assert_called_once_with(1, 42)
    clear.assert_awaited_once()


async def test_end_session_stops_polling_when_shell_appears(fake_sleep) -> None:
    tmux = _tmux(find_side_effect=[_win("claude"), _win("bash")])
    with (
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.session_manager"),
        patch(f"{_M}.clear_topic_state", new_callable=AsyncMock),
    ):
        await end_session(AsyncMock(), 1, 42, "@8")

    assert tmux.find_window_by_id.await_count == 2  # initial + 1 poll (shell seen)
    tmux.kill_window.assert_awaited_once_with("@8")


# ── keyboards ────────────────────────────────────────────────────────────


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_kill_confirm_keyboard() -> None:
    data = _callbacks(build_kill_confirm_keyboard("@12"))
    assert f"{CB_SL_KILL_OK}@12" in data
    assert f"{CB_SL_KILL_CLOSE}@12" in data
    assert CB_SL_CANCEL in data


def test_not_running_keyboard() -> None:
    data = _callbacks(build_not_running_keyboard("@12"))
    assert f"{CB_SL_RESTART}@12" in data
    assert f"{CB_SL_KILL_ASK}@12" in data


# ── shell guard ──────────────────────────────────────────────────────────


async def test_guard_shell_without_chrome_replies_and_blocks() -> None:
    tmux = _tmux()
    tmux.capture_pane = AsyncMock(return_value="$ ")
    msg = MagicMock()
    with (
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        blocked = await reply_if_claude_not_running(msg, _win("bash"))

    assert blocked is True
    reply.assert_awaited_once()
    assert reply.await_args.args[0] is msg
    assert "not running" in reply.await_args.args[1]
    assert "reply_markup" in reply.await_args.kwargs


async def test_guard_shell_with_chrome_allows(chrome: str) -> None:
    tmux = _tmux()
    tmux.capture_pane = AsyncMock(return_value=chrome)
    with (
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        assert await reply_if_claude_not_running(MagicMock(), _win("bash")) is False
    reply.assert_not_awaited()


async def test_guard_claude_without_chrome_allows() -> None:
    tmux = _tmux()
    tmux.capture_pane = AsyncMock(return_value="no chrome here")
    with (
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        assert await reply_if_claude_not_running(MagicMock(), _win("claude")) is False
    tmux.capture_pane.assert_not_awaited()
    reply.assert_not_awaited()


async def test_guard_shell_capture_failure_blocks() -> None:
    tmux = _tmux()
    tmux.capture_pane = AsyncMock(return_value=None)
    with (
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        assert await reply_if_claude_not_running(MagicMock(), _win("bash")) is True
    reply.assert_awaited_once()


async def test_guard_internal_error_returns_false() -> None:
    tmux = _tmux()
    tmux.capture_pane = AsyncMock(return_value="$ ")
    with (
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.has_chrome", side_effect=RuntimeError("boom")),
        patch(f"{_M}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        assert await reply_if_claude_not_running(MagicMock(), _win("bash")) is False
    reply.assert_not_awaited()


# ── text_handler integration with the guard ──────────────────────────────


def _text_update(text: str = "hello") -> MagicMock:
    upd = MagicMock()
    user = MagicMock(id=1)
    upd.effective_user = user
    upd.effective_chat = MagicMock(type="private")
    upd.callback_query = None
    msg = MagicMock()
    msg.text = text
    msg.message_thread_id = 42
    msg.chat.send_action = AsyncMock()
    upd.message = msg
    return upd


def _text_context() -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = {}
    ctx.bot = AsyncMock()
    return ctx


async def test_text_handler_guarded_window_does_not_send_to_tmux() -> None:
    from ccbot.handlers.message_handlers import text_handler

    upd = _text_update()
    ctx = _text_context()
    user = upd.effective_user
    sctx = SessionContext(user=user, thread_id=42, window_id="@8", window=_win("bash"))
    tmux = _tmux()
    tmux.capture_pane = AsyncMock(return_value="$ ")
    with (
        patch(f"{_MH}.require_user", new_callable=AsyncMock, return_value=user),
        patch(f"{_MH}.require_session", new_callable=AsyncMock, return_value=sctx),
        patch(f"{_MH}.session_manager") as sm,
        patch(f"{_MH}.enqueue_status_update", new_callable=AsyncMock),
        patch(f"{_MH}.tmux_manager", tmux),
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        sm.get_window_for_thread.return_value = "@8"
        sm.send_to_window = AsyncMock(return_value=(True, "ok"))
        await text_handler(upd, ctx)

    sm.send_to_window.assert_not_awaited()
    reply.assert_awaited_once()


async def test_text_handler_claude_window_sends_to_tmux() -> None:
    from ccbot.handlers.message_handlers import text_handler

    upd = _text_update()
    ctx = _text_context()
    user = upd.effective_user
    sctx = SessionContext(
        user=user, thread_id=42, window_id="@8", window=_win("claude")
    )
    tmux = _tmux()
    with (
        patch(f"{_MH}.require_user", new_callable=AsyncMock, return_value=user),
        patch(f"{_MH}.require_session", new_callable=AsyncMock, return_value=sctx),
        patch(f"{_MH}.session_manager") as sm,
        patch(f"{_MH}.enqueue_status_update", new_callable=AsyncMock),
        patch(f"{_MH}.tmux_manager", tmux),
        patch(f"{_M}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        sm.get_window_for_thread.return_value = "@8"
        sm.send_to_window = AsyncMock(return_value=(True, "ok"))
        await text_handler(upd, ctx)

    sm.send_to_window.assert_awaited_once_with("@8", "hello")
    reply.assert_not_awaited()


def test_session_panel_keyboard_callbacks() -> None:
    from ccbot.handlers.session_lifecycle import build_session_panel_keyboard

    markup = build_session_panel_keyboard("@8")
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert data == ["sl:sc:@8", "sl:es:@8", "sl:new", "sl:kq:@8"]
    assert all(len(d.encode()) <= 64 for d in data)


def test_new_session_keyboard_callbacks() -> None:
    from ccbot.handlers.session_lifecycle import build_new_session_keyboard

    markup = build_new_session_keyboard("@8")
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert data == ["sl:nh:@8", "sl:nt", "sl:x"]
