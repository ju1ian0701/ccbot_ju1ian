"""Tests for forward_command_handler — command forwarding to Claude Code."""

from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


@contextmanager
def _forward_stack(mock_sm: MagicMock, mock_tmux: MagicMock):
    """Patch session_guard + bot for require_session / send."""
    with ExitStack() as stack:
        stack.enter_context(
            patch("ccbot.session_guard.config.is_user_allowed", return_value=True)
        )
        stack.enter_context(patch("ccbot.session_guard.get_thread_id", return_value=42))
        stack.enter_context(patch("ccbot.session_guard.session_manager", mock_sm))
        stack.enter_context(patch("ccbot.session_guard.tmux_manager", mock_tmux))
        stack.enter_context(patch("ccbot.bot.session_manager", mock_sm))
        stack.enter_context(patch("ccbot.bot.tmux_manager", mock_tmux))
        stack.enter_context(
            patch("ccbot.session_guard.safe_reply", new_callable=AsyncMock)
        )
        stack.enter_context(patch("ccbot.bot.safe_reply", new_callable=AsyncMock))
        yield


class TestForwardCommand:
    @pytest.mark.asyncio
    async def test_model_sends_command_to_tmux(self):
        """/model → send_to_window called with "/model"."""
        update = _make_update("/model")
        context = _make_context()
        mock_sm = MagicMock()
        mock_tmux = MagicMock()
        mock_sm.resolve_window_for_thread.return_value = "@5"
        mock_sm.get_display_name.return_value = "project"
        mock_tmux.find_window_by_id = AsyncMock(
            return_value=TmuxWindow(window_id="@5", window_name="project", cwd="/tmp")
        )
        mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))

        with _forward_stack(mock_sm, mock_tmux):
            from ccbot.bot import forward_command_handler

            await forward_command_handler(update, context)

            mock_sm.send_to_window.assert_called_once_with("@5", "/model")

    @pytest.mark.asyncio
    async def test_cost_sends_command_to_tmux(self):
        """/cost → send_to_window called with "/cost"."""
        update = _make_update("/cost")
        context = _make_context()
        mock_sm = MagicMock()
        mock_tmux = MagicMock()
        mock_sm.resolve_window_for_thread.return_value = "@5"
        mock_sm.get_display_name.return_value = "project"
        mock_tmux.find_window_by_id = AsyncMock(
            return_value=TmuxWindow(window_id="@5", window_name="project", cwd="/tmp")
        )
        mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))

        with _forward_stack(mock_sm, mock_tmux):
            from ccbot.bot import forward_command_handler

            await forward_command_handler(update, context)

            mock_sm.send_to_window.assert_called_once_with("@5", "/cost")

    @pytest.mark.asyncio
    async def test_clear_clears_session(self):
        """/clear → send_to_window + clear_window_session."""
        update = _make_update("/clear")
        context = _make_context()
        mock_sm = MagicMock()
        mock_tmux = MagicMock()
        mock_sm.resolve_window_for_thread.return_value = "@5"
        mock_sm.get_display_name.return_value = "project"
        mock_tmux.find_window_by_id = AsyncMock(
            return_value=TmuxWindow(window_id="@5", window_name="project", cwd="/tmp")
        )
        mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))

        with _forward_stack(mock_sm, mock_tmux):
            from ccbot.bot import forward_command_handler

            await forward_command_handler(update, context)

            mock_sm.send_to_window.assert_called_once_with("@5", "/clear")
            mock_sm.clear_window_session.assert_called_once_with("@5")
