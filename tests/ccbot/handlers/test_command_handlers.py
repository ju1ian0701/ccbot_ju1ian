"""Unit tests for command_handlers auth/session paths (REF-009 / ISS-001)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.handlers.command_handlers import (
    esc_command,
    start_command,
    unbind_command,
)
from ccbot.session_guard import SessionContext
from ccbot.tmux_manager import TmuxWindow

_CH = "ccbot.handlers.command_handlers"


def _user(uid: int = 1) -> MagicMock:
    u = MagicMock()
    u.id = uid
    return u


def _update(
    *,
    user_id: int = 1,
    message: bool = True,
    thread_id: int | None = 42,
) -> MagicMock:
    upd = MagicMock()
    user = _user(user_id)
    upd.effective_user = user
    if message:
        msg = MagicMock()
        msg.message_thread_id = thread_id
        msg.reply_document = AsyncMock()
        upd.message = msg
    else:
        upd.message = None
    upd.callback_query = None
    return upd


def _context() -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = {}
    ctx.bot = AsyncMock()
    return ctx


def _session_ctx(
    *,
    user: MagicMock | None = None,
    thread_id: int = 42,
    window_id: str = "@8",
) -> SessionContext:
    user = user or _user()
    win = TmuxWindow(window_id=window_id, window_name="p", cwd="/t")
    return SessionContext(
        user=user, thread_id=thread_id, window_id=window_id, window=win
    )


@pytest.mark.asyncio
async def test_start_unauthorized_no_welcome() -> None:
    upd = _update(user_id=99)
    ctx = _context()
    with (
        patch(f"{_CH}.require_user", new_callable=AsyncMock, return_value=None),
        patch(f"{_CH}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        await start_command(upd, ctx)
        # Unauthorized reply is owned by require_user, not the handler.
        reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_authorized_sends_welcome() -> None:
    upd = _update()
    ctx = _context()
    with (
        patch(f"{_CH}.require_user", new_callable=AsyncMock, return_value=_user()),
        patch(f"{_CH}.clear_browse_state") as clear,
        patch(f"{_CH}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        await start_command(upd, ctx)
        clear.assert_called_once_with(ctx.user_data)
        reply.assert_awaited_once()
        assert "Claude Code Monitor" in reply.await_args.args[1]


@pytest.mark.asyncio
async def test_esc_requires_session() -> None:
    upd = _update()
    ctx = _context()
    with (
        patch(f"{_CH}.require_session", new_callable=AsyncMock, return_value=None),
        patch(f"{_CH}.tmux_manager") as tmux,
        patch(f"{_CH}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        await esc_command(upd, ctx)
        tmux.send_keys.assert_not_called()
        # No-session reply is owned by require_session.
        reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_esc_sends_escape_key() -> None:
    upd = _update()
    ctx = _context()
    sctx = _session_ctx()
    with (
        patch(f"{_CH}.require_session", new_callable=AsyncMock, return_value=sctx),
        patch(f"{_CH}.tmux_manager") as tmux,
        patch(f"{_CH}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        tmux.send_keys = AsyncMock(return_value=True)
        await esc_command(upd, ctx)
        tmux.send_keys.assert_awaited_once_with("@8", "\x1b", enter=False)
        reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_unbind_requires_topic() -> None:
    upd = _update(thread_id=None)
    ctx = _context()
    with (
        patch(f"{_CH}.require_user", new_callable=AsyncMock, return_value=_user()),
        patch(f"{_CH}.get_thread_id", return_value=None),
        patch(f"{_CH}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        await unbind_command(upd, ctx)
        assert "only works in a topic" in reply.await_args.args[1]


@pytest.mark.asyncio
async def test_unbind_clears_binding() -> None:
    upd = _update(thread_id=9)
    ctx = _context()
    user = _user()
    with (
        patch(f"{_CH}.require_user", new_callable=AsyncMock, return_value=user),
        patch(f"{_CH}.get_thread_id", return_value=9),
        patch(
            f"{_CH}.require_bound_window_id",
            new_callable=AsyncMock,
            return_value=(user, 9, "@3"),
        ),
        patch(f"{_CH}.session_manager") as sm,
        patch(f"{_CH}.clear_topic_state", new_callable=AsyncMock) as clear,
        patch(f"{_CH}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        sm.get_display_name.return_value = "proj"
        await unbind_command(upd, ctx)
        sm.unbind_thread.assert_called_once_with(1, 9)
        clear.assert_awaited_once()
        assert "unbound" in reply.await_args.args[1].lower()


@pytest.mark.asyncio
async def test_esc_missing_window() -> None:
    upd = _update()
    ctx = _context()
    with (
        patch(f"{_CH}.require_session", new_callable=AsyncMock, return_value=None),
        patch(f"{_CH}.tmux_manager") as tmux,
        patch(f"{_CH}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        await esc_command(upd, ctx)
        tmux.send_keys.assert_not_called()
        reply.assert_not_awaited()
