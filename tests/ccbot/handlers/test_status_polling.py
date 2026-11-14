"""Tests for status_polling — Settings UI detection via the poller path.

Simulates the user workflow: /model is sent to Claude Code, the Settings
model picker renders in the terminal, and the status poller detects it
on its next 1s tick.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.handlers import status_polling
from ccbot.handlers.status_polling import (
    STATUS_CLEAR_POLLS,
    UNRECOGNIZED_SCREEN_POLLS,
    parser_state,
    status_poll_loop,
    update_status_message,
)
from ccbot.hook_events import HookEvent
from ccbot.tmux_manager import TmuxWindow


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    sent_msg = MagicMock()
    sent_msg.message_id = 999
    bot.send_message.return_value = sent_msg
    return bot


@pytest.fixture
def _clear_interactive_state():
    """Ensure interactive state is clean before and after each test."""
    from ccbot.handlers.interactive_ui import _interactive_mode, _interactive_msgs

    _interactive_mode.clear()
    _interactive_msgs.clear()
    yield
    _interactive_mode.clear()
    _interactive_msgs.clear()


@pytest.mark.usefixtures("_clear_interactive_state")
class TestStatusPollerSettingsDetection:
    """Simulate the status poller detecting a Settings UI in the terminal.

    This is the actual code path for /model: no JSONL tool_use entry exists,
    so the status poller (update_status_message) is the only detector.
    """

    @pytest.mark.asyncio
    async def test_settings_ui_detected_and_keyboard_sent(
        self, mock_bot: AsyncMock, sample_pane_settings: str
    ):
        """Poller captures Settings pane → handle_interactive_ui sends keyboard."""
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id

        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tmux,
            patch(
                "ccbot.handlers.status_polling.handle_interactive_ui",
                new_callable=AsyncMock,
            ) as mock_handle_ui,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(return_value=sample_pane_settings)
            mock_handle_ui.return_value = True

            await update_status_message(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

            mock_handle_ui.assert_called_once_with(mock_bot, 1, window_id, 42)

    @pytest.mark.asyncio
    async def test_normal_pane_no_interactive_ui(self, mock_bot: AsyncMock):
        """Normal pane text → no handle_interactive_ui call, just status check."""
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id
        normal_pane = (
            "some output\n"
            "✻ Reading file\n"
            "──────────────────────────────────────\n"
            "❯ \n"
            "──────────────────────────────────────\n"
            "  [Opus 4.6] Context: 50%\n"
        )

        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tmux,
            patch(
                "ccbot.handlers.status_polling.handle_interactive_ui",
                new_callable=AsyncMock,
            ) as mock_handle_ui,
            patch(
                "ccbot.handlers.status_polling.enqueue_status_update",
                new_callable=AsyncMock,
            ),
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux.capture_pane = AsyncMock(return_value=normal_pane)

            await update_status_message(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

            mock_handle_ui.assert_not_called()

    @pytest.mark.asyncio
    async def test_settings_ui_end_to_end_sends_telegram_keyboard(
        self, mock_bot: AsyncMock, sample_pane_settings: str
    ):
        """Full end-to-end: poller → is_interactive_ui → handle_interactive_ui
        → bot.send_message with keyboard.

        Uses real handle_interactive_ui (not mocked) to verify the full path.
        """
        window_id = "@5"
        mock_window = MagicMock()
        mock_window.window_id = window_id

        with (
            patch("ccbot.handlers.status_polling.tmux_manager") as mock_tmux_poll,
            patch("ccbot.handlers.interactive_ui.tmux_manager") as mock_tmux_ui,
            patch("ccbot.handlers.interactive_ui.session_manager") as mock_sm,
        ):
            mock_tmux_poll.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux_poll.capture_pane = AsyncMock(return_value=sample_pane_settings)
            mock_tmux_ui.find_window_by_id = AsyncMock(return_value=mock_window)
            mock_tmux_ui.capture_pane = AsyncMock(return_value=sample_pane_settings)
            mock_sm.resolve_chat_id.return_value = 100

            await update_status_message(
                mock_bot, user_id=1, window_id=window_id, thread_id=42
            )

            # Verify bot.send_message was called with keyboard
            mock_bot.send_message.assert_called_once()
            call_kwargs = mock_bot.send_message.call_args.kwargs
            assert call_kwargs["chat_id"] == 100
            assert call_kwargs["message_thread_id"] == 42
            keyboard = call_kwargs["reply_markup"]
            assert keyboard is not None
            # Verify the message text contains model picker content
            assert "Select model" in call_kwargs["text"]


# --- Stale status clear, parser canary, hook event consumption -------------

# --- Stale status clear, parser canary, hook event consumption -------------

_SP = "ccbot.handlers.status_polling"
_CHROME_PANE = (
    "some output\n"
    "──────────────────────────────────────\n"
    "❯ \n"
    "──────────────────────────────────────\n"
    "  [Opus 4.6] Context: 50%\n"
)
_STATUS_PANE = (
    "some output\n"
    "✻ Reading file\n"
    "──────────────────────────────────────\n"
    "❯ \n"
    "──────────────────────────────────────\n"
    "  [Opus 4.6] Context: 50%\n"
)
_PLAIN_PANE = "random screen\nnothing recognizable here\n"


@pytest.fixture(autouse=True)
def _reset_polling_state():
    status_polling._no_status_polls.clear()
    status_polling._unrecognized_polls.clear()
    status_polling._reported_unrecognized.clear()
    yield
    status_polling._no_status_polls.clear()
    status_polling._unrecognized_polls.clear()
    status_polling._reported_unrecognized.clear()


def _tw(command: str = "claude") -> TmuxWindow:
    return TmuxWindow(
        window_id="@5", window_name="p", cwd="/t", pane_current_command=command
    )


def _clear_calls(enq: AsyncMock) -> int:
    return sum(1 for c in enq.await_args_list if c.args[3] is None)


class TestStaleStatusClear:
    @pytest.mark.asyncio
    async def test_clear_enqueued_once_on_fifth_poll(self, mock_bot: AsyncMock):
        assert STATUS_CLEAR_POLLS == 5
        with (
            patch(f"{_SP}.tmux_manager") as mock_tmux,
            patch(f"{_SP}.enqueue_status_update", new_callable=AsyncMock) as enq,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=_tw())
            mock_tmux.capture_pane = AsyncMock(return_value=_CHROME_PANE)
            counts = []
            for _ in range(6):
                await update_status_message(mock_bot, 1, "@5", thread_id=42)
                counts.append(_clear_calls(enq))

        assert counts == [0, 0, 0, 0, 1, 1]
        clear_call = [c for c in enq.await_args_list if c.args[3] is None][0]
        assert clear_call.kwargs["thread_id"] == 42

    @pytest.mark.asyncio
    async def test_status_line_resets_counter(self, mock_bot: AsyncMock):
        with (
            patch(f"{_SP}.tmux_manager") as mock_tmux,
            patch(f"{_SP}.enqueue_status_update", new_callable=AsyncMock) as enq,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=_tw())
            mock_tmux.capture_pane = AsyncMock(return_value=_CHROME_PANE)
            for _ in range(4):
                await update_status_message(mock_bot, 1, "@5", thread_id=42)
            mock_tmux.capture_pane = AsyncMock(return_value=_STATUS_PANE)
            await update_status_message(mock_bot, 1, "@5", thread_id=42)
            mock_tmux.capture_pane = AsyncMock(return_value=_CHROME_PANE)
            for _ in range(4):
                await update_status_message(mock_bot, 1, "@5", thread_id=42)

        assert _clear_calls(enq) == 0

    @pytest.mark.asyncio
    async def test_without_chrome_nothing_is_cleared(self, mock_bot: AsyncMock):
        with (
            patch(f"{_SP}.tmux_manager") as mock_tmux,
            patch(f"{_SP}.enqueue_status_update", new_callable=AsyncMock) as enq,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=_tw())
            mock_tmux.capture_pane = AsyncMock(return_value=_PLAIN_PANE)
            for _ in range(10):
                await update_status_message(mock_bot, 1, "@5", thread_id=42)

        assert _clear_calls(enq) == 0
        enq.assert_not_awaited()


class TestParserCanary:
    @pytest.mark.asyncio
    async def test_unrecognized_screen_reported_once_at_thirtieth_poll(
        self, mock_bot: AsyncMock
    ):
        assert UNRECOGNIZED_SCREEN_POLLS == 30
        with (
            patch(f"{_SP}.tmux_manager") as mock_tmux,
            patch(f"{_SP}.enqueue_status_update", new_callable=AsyncMock),
            patch(f"{_SP}.send_prompt_screenshot", new_callable=AsyncMock) as shot,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=_tw())
            mock_tmux.capture_pane = AsyncMock(return_value=_PLAIN_PANE)
            for _ in range(29):
                await update_status_message(mock_bot, 1, "@5", thread_id=42)
            shot.assert_not_awaited()
            await update_status_message(mock_bot, 1, "@5", thread_id=42)
            shot.assert_awaited_once()
            assert shot.await_args.args[1:4] == (1, "@5", 42)
            assert parser_state("@5") == {"no_chrome_polls": 30, "reported": True}
            for _ in range(5):
                await update_status_message(mock_bot, 1, "@5", thread_id=42)

        shot.assert_awaited_once()
        assert parser_state("@5") == {"no_chrome_polls": 35, "reported": True}

    @pytest.mark.asyncio
    async def test_chrome_resets_and_window_can_be_reported_again(
        self, mock_bot: AsyncMock
    ):
        with (
            patch(f"{_SP}.tmux_manager") as mock_tmux,
            patch(f"{_SP}.enqueue_status_update", new_callable=AsyncMock),
            patch(f"{_SP}.send_prompt_screenshot", new_callable=AsyncMock) as shot,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=_tw())
            mock_tmux.capture_pane = AsyncMock(return_value=_PLAIN_PANE)
            for _ in range(UNRECOGNIZED_SCREEN_POLLS):
                await update_status_message(mock_bot, 1, "@5", thread_id=42)
            assert shot.await_count == 1

            mock_tmux.capture_pane = AsyncMock(return_value=_CHROME_PANE)
            await update_status_message(mock_bot, 1, "@5", thread_id=42)
            assert parser_state("@5") == {"no_chrome_polls": 0, "reported": False}

            mock_tmux.capture_pane = AsyncMock(return_value=_PLAIN_PANE)
            for _ in range(UNRECOGNIZED_SCREEN_POLLS):
                await update_status_message(mock_bot, 1, "@5", thread_id=42)

        assert shot.await_count == 2

    @pytest.mark.asyncio
    async def test_shell_window_never_counts(self, mock_bot: AsyncMock):
        with (
            patch(f"{_SP}.tmux_manager") as mock_tmux,
            patch(f"{_SP}.enqueue_status_update", new_callable=AsyncMock),
            patch(f"{_SP}.send_prompt_screenshot", new_callable=AsyncMock) as shot,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=_tw("bash"))
            mock_tmux.capture_pane = AsyncMock(return_value=_PLAIN_PANE)
            for _ in range(UNRECOGNIZED_SCREEN_POLLS + 5):
                await update_status_message(mock_bot, 1, "@5", thread_id=42)

        shot.assert_not_awaited()
        assert parser_state("@5")["no_chrome_polls"] == 0

    @pytest.mark.asyncio
    async def test_recognized_interactive_ui_never_counts(
        self, mock_bot: AsyncMock, sample_pane_settings: str
    ):
        with (
            patch(f"{_SP}.tmux_manager") as mock_tmux,
            patch(f"{_SP}.handle_interactive_ui", new_callable=AsyncMock) as ui,
            patch(f"{_SP}.send_prompt_screenshot", new_callable=AsyncMock) as shot,
        ):
            ui.return_value = True
            mock_tmux.find_window_by_id = AsyncMock(return_value=_tw())
            mock_tmux.capture_pane = AsyncMock(return_value=sample_pane_settings)
            for _ in range(UNRECOGNIZED_SCREEN_POLLS + 5):
                await update_status_message(mock_bot, 1, "@5", thread_id=42)

        shot.assert_not_awaited()
        assert parser_state("@5")["no_chrome_polls"] == 0


class TestHookEventConsumption:
    @pytest.mark.asyncio
    async def test_loop_hands_new_events_to_reactions(self, mock_bot: AsyncMock):
        event = HookEvent(ts=1.0, event="Stop", window_id="@5", session_id="s", data={})
        reader = MagicMock()
        reader.read_new.return_value = [event]
        with (
            patch(f"{_SP}.HookEventReader", return_value=reader),
            patch(f"{_SP}.handle_hook_event", new_callable=AsyncMock) as handle,
            patch(f"{_SP}.session_manager") as sm,
            # Stop the loop after the first iteration's sleep
            patch(
                f"{_SP}.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError,
            ),
        ):
            sm.iter_thread_bindings.return_value = []
            with pytest.raises(asyncio.CancelledError):
                await status_poll_loop(mock_bot)

        handle.assert_awaited_once_with(mock_bot, event)
