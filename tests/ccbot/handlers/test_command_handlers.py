"""Unit tests for command_handlers auth/session paths (REF-009 / ISS-001)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.handlers.command_handlers import (
    esc_command,
    health_command,
    kill_command,
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


def _win(window_id: str, command: str) -> TmuxWindow:
    return TmuxWindow(
        window_id=window_id,
        window_name="proj",
        cwd="/t",
        pane_current_command=command,
    )


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


@pytest.mark.asyncio
async def test_kill_bound_topic_shows_confirm_keyboard() -> None:
    upd = _update(thread_id=42)
    ctx = _context()
    user = _user()
    with (
        patch(
            f"{_CH}.require_bound_window_id",
            new_callable=AsyncMock,
            return_value=(user, 42, "@8"),
        ),
        patch(f"{_CH}.session_manager") as sm,
        patch(f"{_CH}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        sm.get_display_name.return_value = "proj"
        sm.get_window_state.return_value = MagicMock(cwd="/work/proj")
        await kill_command(upd, ctx)

    reply.assert_awaited_once()
    assert "End session 'proj'" in reply.await_args.args[1]
    markup = reply.await_args.kwargs["reply_markup"]
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "sl:ko:@8" in data
    assert "sl:kc:@8" in data
    assert "sl:x" in data


@pytest.mark.asyncio
async def test_kill_unbound_topic_no_keyboard() -> None:
    upd = _update(thread_id=42)
    ctx = _context()
    with (
        patch(
            f"{_CH}.require_bound_window_id", new_callable=AsyncMock, return_value=None
        ),
        patch(f"{_CH}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        await kill_command(upd, ctx)

    reply.assert_not_awaited()


_HEALTH_PANE = (
    "some output\n"
    "──────────────────────────────────────\n"
    "❯ \n"
    "──────────────────────────────────────\n"
    "  [Opus 4.6] Context: 50%\n"
)


@pytest.fixture
def hook_settings(tmp_path: Path) -> Path:
    """A tmp Claude settings file with only the SessionStart hook installed."""
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "ccbot hook"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return settings


@pytest.mark.asyncio
async def test_health_bound_topic_reports_window(hook_settings: Path) -> None:
    upd = _update(thread_id=42)
    ctx = _context()
    with (
        patch(f"{_CH}.require_user", new_callable=AsyncMock, return_value=_user()),
        patch(f"{_CH}.get_thread_id", return_value=42),
        patch(f"{_CH}.session_manager") as sm,
        patch(f"{_CH}.tmux_manager") as tmux,
        patch(f"{_CH}.safe_reply", new_callable=AsyncMock) as reply,
        patch("ccbot.hook._CLAUDE_SETTINGS_FILE", hook_settings),
        patch(
            f"{_CH}.hook_reactions.last_event_for",
            return_value=("Stop", time.time() - 12),
        ),
    ):
        sm.get_window_for_thread.return_value = "@5"
        sm.get_display_name.return_value = "proj"
        sm.get_window_state.return_value = MagicMock(
            session_id="4c46f570-aaaa-bbbb-cccc-dddddddddddd"
        )
        tmux.find_window_by_id = AsyncMock(return_value=_win("@5", "claude"))
        tmux.capture_pane = AsyncMock(return_value=_HEALTH_PANE)
        await health_command(upd, ctx)

    reply.assert_awaited_once()
    text = reply.await_args.args[1]
    assert text.startswith("```\nccbot health")
    assert "window: @5 'proj' process=claude chrome=yes" in text
    assert "session: 4c46f570 tracked=yes" in text
    assert "last hook event: Stop 12s ago" in text
    assert "hooks: SessionStart ok, SessionEnd MISSING" in text


@pytest.mark.asyncio
async def test_health_unbound_topic_has_no_window_lines(hook_settings: Path) -> None:
    upd = _update(thread_id=42)
    ctx = _context()
    with (
        patch(f"{_CH}.require_user", new_callable=AsyncMock, return_value=_user()),
        patch(f"{_CH}.get_thread_id", return_value=42),
        patch(f"{_CH}.session_manager") as sm,
        patch(f"{_CH}.tmux_manager") as tmux,
        patch(f"{_CH}.safe_reply", new_callable=AsyncMock) as reply,
        patch("ccbot.hook._CLAUDE_SETTINGS_FILE", hook_settings),
    ):
        sm.get_window_for_thread.return_value = None
        tmux.find_window_by_id = AsyncMock()
        await health_command(upd, ctx)

    tmux.find_window_by_id.assert_not_called()
    text = reply.await_args.args[1]
    assert "window: this topic is not bound to a session" in text
    assert "session:" not in text


@pytest.mark.asyncio
async def test_health_unauthorized_no_reply() -> None:
    upd = _update(user_id=99)
    ctx = _context()
    with (
        patch(f"{_CH}.require_user", new_callable=AsyncMock, return_value=None),
        patch(f"{_CH}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        await health_command(upd, ctx)
    reply.assert_not_awaited()
