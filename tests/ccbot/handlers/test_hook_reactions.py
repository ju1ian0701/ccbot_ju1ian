"""Tests for hook_reactions: Telegram reactions to Claude Code hook events."""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from ccbot.handlers import hook_reactions
from ccbot.handlers.hook_reactions import (
    UNRECOGNIZED_CAPTION,
    forget_window,
    handle_hook_event,
    last_event_for,
    send_prompt_screenshot,
)
from ccbot.hook_events import HookEvent
from ccbot.tmux_manager import TmuxWindow

_HR = "ccbot.handlers.hook_reactions"
_WINDOW = "@5"
_USER = 1
_THREAD = 42


@pytest.fixture(autouse=True)
def _reset_hook_reactions_state():
    hook_reactions._last_events.clear()
    hook_reactions._last_fallback.clear()
    yield
    hook_reactions._last_events.clear()
    hook_reactions._last_fallback.clear()


@pytest.fixture
def bot() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def sm():
    """session_manager stand-in with one binding for _WINDOW."""
    with patch(f"{_HR}.session_manager") as mock_sm:
        mock_sm.iter_thread_bindings.return_value = [(_USER, _THREAD, _WINDOW)]
        mock_sm.resolve_chat_id.return_value = 100
        yield mock_sm


@pytest.fixture
def no_sleep():
    with patch(f"{_HR}.asyncio.sleep", new_callable=AsyncMock) as sleep:
        yield sleep


def _event(event: str, window_id: str = _WINDOW, **data: str) -> HookEvent:
    return HookEvent(
        ts=1000.0,
        event=event,
        window_id=window_id,
        session_id="sess-1",
        data=dict(data),
    )


def _window(command: str = "claude") -> TmuxWindow:
    return TmuxWindow(
        window_id=_WINDOW, window_name="proj", cwd="/t", pane_current_command=command
    )


class TestNotification:
    @pytest.mark.asyncio
    async def test_recognized_on_first_try_no_screenshot(
        self, bot, sm, no_sleep
    ) -> None:
        with (
            patch(f"{_HR}.handle_interactive_ui", new_callable=AsyncMock) as ui,
            patch(f"{_HR}.send_prompt_screenshot", new_callable=AsyncMock) as shot,
        ):
            ui.return_value = True
            await handle_hook_event(
                bot, _event("Notification", notification_type="permission_prompt")
            )

        ui.assert_awaited_once_with(bot, _USER, _WINDOW, _THREAD)
        no_sleep.assert_not_awaited()
        shot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_recognized_on_second_try_no_screenshot(
        self, bot, sm, no_sleep
    ) -> None:
        with (
            patch(f"{_HR}.handle_interactive_ui", new_callable=AsyncMock) as ui,
            patch(f"{_HR}.send_prompt_screenshot", new_callable=AsyncMock) as shot,
        ):
            ui.side_effect = [False, True]
            await handle_hook_event(
                bot, _event("Notification", notification_type="elicitation_dialog")
            )

        assert ui.await_count == 2
        no_sleep.assert_awaited_once_with(1.0)
        shot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_never_recognized_logs_parser_miss_and_screenshots(
        self, bot, sm, no_sleep, caplog
    ) -> None:
        caplog.set_level(logging.WARNING, logger=hook_reactions.logger.name)
        with (
            patch(f"{_HR}.handle_interactive_ui", new_callable=AsyncMock) as ui,
            patch(f"{_HR}.send_prompt_screenshot", new_callable=AsyncMock) as shot,
        ):
            ui.return_value = False
            await handle_hook_event(
                bot, _event("Notification", notification_type="permission_prompt")
            )

        assert ui.await_count == 2
        no_sleep.assert_awaited_once_with(1.0)
        shot.assert_awaited_once_with(
            bot, _USER, _WINDOW, _THREAD, UNRECOGNIZED_CAPTION
        )
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= 30]
        assert any(
            "parser_miss" in m and _WINDOW in m and "permission_prompt" in m
            for m in warnings
        )

    @pytest.mark.asyncio
    async def test_idle_prompt_is_ignored(self, bot, sm, no_sleep) -> None:
        with (
            patch(f"{_HR}.handle_interactive_ui", new_callable=AsyncMock) as ui,
            patch(f"{_HR}.send_prompt_screenshot", new_callable=AsyncMock) as shot,
        ):
            await handle_hook_event(
                bot, _event("Notification", notification_type="idle_prompt")
            )

        ui.assert_not_awaited()
        shot.assert_not_awaited()
        no_sleep.assert_not_awaited()


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_clears_status(self, bot, sm) -> None:
        with patch(f"{_HR}.enqueue_status_update", new_callable=AsyncMock) as enq:
            await handle_hook_event(bot, _event("Stop"))

        enq.assert_awaited_once_with(bot, _USER, _WINDOW, None, thread_id=_THREAD)


class TestStopFailure:
    @pytest.mark.asyncio
    async def test_error_message_sent_to_topic(self, bot, sm) -> None:
        with patch(f"{_HR}.safe_send", new_callable=AsyncMock) as send:
            await handle_hook_event(
                bot,
                _event(
                    "StopFailure",
                    error="rate_limit",
                    last_assistant_message="partial answer",
                ),
            )

        send.assert_awaited_once()
        args, kwargs = send.await_args
        assert args[0] is bot
        assert args[1] == 100
        text = args[2]
        assert "rate_limit" in text
        assert "partial answer" in text
        assert "/login" not in text
        assert kwargs["message_thread_id"] == _THREAD

    @pytest.mark.asyncio
    async def test_auth_error_mentions_login(self, bot, sm) -> None:
        with patch(f"{_HR}.safe_send", new_callable=AsyncMock) as send:
            await handle_hook_event(
                bot, _event("StopFailure", error="authentication_failed")
            )

        text = send.await_args.args[2]
        assert "authentication_failed" in text
        assert "/login" in text


class TestSessionEnd:
    @pytest.mark.asyncio
    async def test_clear_is_ignored(self, bot, sm, no_sleep) -> None:
        with (
            patch(f"{_HR}.tmux_manager") as tmux,
            patch(f"{_HR}.safe_send", new_callable=AsyncMock) as send,
        ):
            await handle_hook_event(bot, _event("SessionEnd", reason="clear"))

        tmux.find_window_by_id.assert_not_called()
        send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exit_with_shell_offers_restart(self, bot, sm, no_sleep) -> None:
        with (
            patch(f"{_HR}.tmux_manager") as tmux,
            patch(f"{_HR}.safe_send", new_callable=AsyncMock) as send,
        ):
            tmux.find_window_by_id = AsyncMock(return_value=_window("bash"))
            await handle_hook_event(
                bot, _event("SessionEnd", reason="prompt_input_exit")
            )

        no_sleep.assert_awaited_once_with(1.0)
        send.assert_awaited_once()
        assert "prompt_input_exit" in send.await_args.args[2]
        assert send.await_args.kwargs["reply_markup"] is not None
        assert send.await_args.kwargs["message_thread_id"] == _THREAD

    @pytest.mark.asyncio
    async def test_exit_window_gone_sends_nothing(self, bot, sm, no_sleep) -> None:
        with (
            patch(f"{_HR}.tmux_manager") as tmux,
            patch(f"{_HR}.safe_send", new_callable=AsyncMock) as send,
        ):
            tmux.find_window_by_id = AsyncMock(return_value=None)
            await handle_hook_event(
                bot, _event("SessionEnd", reason="prompt_input_exit")
            )

        send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exit_window_still_running_claude_sends_nothing(
        self, bot, sm, no_sleep
    ) -> None:
        with (
            patch(f"{_HR}.tmux_manager") as tmux,
            patch(f"{_HR}.safe_send", new_callable=AsyncMock) as send,
        ):
            tmux.find_window_by_id = AsyncMock(return_value=_window("claude"))
            await handle_hook_event(
                bot, _event("SessionEnd", reason="prompt_input_exit")
            )

        send.assert_not_awaited()


class TestBindings:
    @pytest.mark.asyncio
    async def test_event_for_unbound_window_does_nothing(self, bot, sm) -> None:
        sm.iter_thread_bindings.return_value = [(_USER, _THREAD, "@9")]
        with patch(f"{_HR}.enqueue_status_update", new_callable=AsyncMock) as enq:
            await handle_hook_event(bot, _event("Stop", window_id="@77"))

        enq.assert_not_awaited()
        assert last_event_for("@77") == ("Stop", 1000.0)
        forget_window("@77")

    @pytest.mark.asyncio
    async def test_failing_reaction_does_not_block_second_binding(
        self, bot, sm
    ) -> None:
        sm.iter_thread_bindings.return_value = [
            (1, 42, _WINDOW),
            (2, 43, _WINDOW),
        ]
        with patch(f"{_HR}.enqueue_status_update", new_callable=AsyncMock) as enq:
            enq.side_effect = [RuntimeError("boom"), None]
            await handle_hook_event(bot, _event("Stop"))

        assert enq.await_count == 2
        assert enq.await_args_list[1].args[1] == 2


class TestSendPromptScreenshot:
    @pytest.mark.asyncio
    async def test_sends_document_with_caption_and_keyboard(self, bot, sm) -> None:
        with (
            patch(f"{_HR}.tmux_manager") as tmux,
            patch(f"{_HR}.text_to_image", new_callable=AsyncMock) as render,
        ):
            tmux.capture_pane = AsyncMock(return_value="pane text")
            render.return_value = b"png-bytes"
            ok = await send_prompt_screenshot(
                bot, _USER, _WINDOW, _THREAD, "caption here"
            )

        assert ok is True
        kwargs = bot.send_document.await_args.kwargs
        assert kwargs["chat_id"] == 100
        assert kwargs["caption"] == "caption here"
        assert kwargs["message_thread_id"] == _THREAD
        assert kwargs["filename"] == "screenshot.png"
        assert kwargs["reply_markup"] is not None
        assert kwargs["document"].read() == b"png-bytes"
        tmux.capture_pane.assert_awaited_once_with(_WINDOW, with_ansi=True)

    @pytest.mark.asyncio
    async def test_second_call_within_interval_is_skipped(self, bot, sm) -> None:
        with (
            patch(f"{_HR}.tmux_manager") as tmux,
            patch(f"{_HR}.text_to_image", new_callable=AsyncMock) as render,
        ):
            tmux.capture_pane = AsyncMock(return_value="pane text")
            render.return_value = b"png"
            assert await send_prompt_screenshot(bot, _USER, _WINDOW, _THREAD, "c")
            assert not await send_prompt_screenshot(bot, _USER, _WINDOW, _THREAD, "c")

        assert bot.send_document.await_count == 1

    @pytest.mark.asyncio
    async def test_empty_capture_returns_false(self, bot, sm) -> None:
        with patch(f"{_HR}.tmux_manager") as tmux:
            tmux.capture_pane = AsyncMock(return_value="")
            ok = await send_prompt_screenshot(bot, _USER, _WINDOW, _THREAD, "c")

        assert ok is False
        bot.send_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_failure_returns_false(self, bot, sm) -> None:
        bot.send_document.side_effect = RuntimeError("telegram down")
        with (
            patch(f"{_HR}.tmux_manager") as tmux,
            patch(f"{_HR}.text_to_image", new_callable=AsyncMock) as render,
        ):
            tmux.capture_pane = AsyncMock(return_value="pane text")
            render.return_value = b"png"
            ok = await send_prompt_screenshot(bot, _USER, _WINDOW, _THREAD, "c")

        assert ok is False


class TestWindowMemory:
    @pytest.mark.asyncio
    async def test_last_event_recorded_and_forgotten(self, bot, sm) -> None:
        with patch(f"{_HR}.enqueue_status_update", new_callable=AsyncMock):
            await handle_hook_event(bot, _event("Stop"))

        assert last_event_for(_WINDOW) == ("Stop", 1000.0)
        forget_window(_WINDOW)
        assert last_event_for(_WINDOW) is None
