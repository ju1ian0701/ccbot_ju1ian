"""Tests for forward_command_handler — command forwarding to Claude Code."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.session_guard import SessionContext
from ccbot.tmux_manager import TmuxWindow


def _make_update(text: str, user_id: int = 1, thread_id: int = 42) -> MagicMock:
    """Build a minimal mock Update with message text in a forum topic."""
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.message = MagicMock()
    update.message.text = text
    update.message.message_thread_id = thread_id
    update.message.chat = MagicMock()
    update.message.chat.send_action = AsyncMock()
    update.callback_query = None
    update.effective_chat = MagicMock()
    update.effective_chat.type = "supergroup"
    update.effective_chat.id = 100
    return update


def _make_context() -> MagicMock:
    """Build a minimal mock context."""
    context = MagicMock()
    context.bot = AsyncMock()
    context.user_data = {}
    return context


def _sctx(user: MagicMock, window_id: str = "@5") -> SessionContext:
    win = TmuxWindow(window_id=window_id, window_name="project", cwd="/t")
    return SessionContext(user=user, thread_id=42, window_id=window_id, window=win)


_CH = "ccbot.handlers.command_handlers"


class TestForwardCommand:
    @pytest.mark.asyncio
    async def test_model_sends_command_to_tmux(self):
        """/model → send_to_window called with "/model"."""
        update = _make_update("/model")
        context = _make_context()
        user = update.effective_user

        with (
            patch(f"{_CH}.require_user", new_callable=AsyncMock, return_value=user),
            patch(f"{_CH}.get_thread_id", return_value=42),
            patch(
                f"{_CH}.require_session",
                new_callable=AsyncMock,
                return_value=_sctx(user),
            ),
            patch(f"{_CH}.session_manager") as mock_sm,
            patch(f"{_CH}.safe_reply", new_callable=AsyncMock),
        ):
            mock_sm.get_display_name.return_value = "project"
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))

            from ccbot.handlers.command_handlers import forward_command_handler

            await forward_command_handler(update, context)

            mock_sm.send_to_window.assert_called_once_with("@5", "/model")

    @pytest.mark.asyncio
    async def test_cost_sends_command_to_tmux(self):
        """/cost → send_to_window called with "/cost"."""
        update = _make_update("/cost")
        context = _make_context()
        user = update.effective_user

        with (
            patch(f"{_CH}.require_user", new_callable=AsyncMock, return_value=user),
            patch(f"{_CH}.get_thread_id", return_value=42),
            patch(
                f"{_CH}.require_session",
                new_callable=AsyncMock,
                return_value=_sctx(user),
            ),
            patch(f"{_CH}.session_manager") as mock_sm,
            patch(f"{_CH}.safe_reply", new_callable=AsyncMock),
        ):
            mock_sm.get_display_name.return_value = "project"
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))

            from ccbot.handlers.command_handlers import forward_command_handler

            await forward_command_handler(update, context)

            mock_sm.send_to_window.assert_called_once_with("@5", "/cost")

    @pytest.mark.asyncio
    async def test_clear_clears_session(self):
        """/clear → send_to_window + clear_window_session."""
        update = _make_update("/clear")
        context = _make_context()
        user = update.effective_user

        with (
            patch(f"{_CH}.require_user", new_callable=AsyncMock, return_value=user),
            patch(f"{_CH}.get_thread_id", return_value=42),
            patch(
                f"{_CH}.require_session",
                new_callable=AsyncMock,
                return_value=_sctx(user),
            ),
            patch(f"{_CH}.session_manager") as mock_sm,
            patch(f"{_CH}.safe_reply", new_callable=AsyncMock),
        ):
            mock_sm.get_display_name.return_value = "project"
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))

            from ccbot.handlers.command_handlers import forward_command_handler

            await forward_command_handler(update, context)

            mock_sm.send_to_window.assert_called_once_with("@5", "/clear")
            mock_sm.clear_window_session.assert_called_once_with("@5")
