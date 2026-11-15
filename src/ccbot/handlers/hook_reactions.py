"""React in Telegram to structured Claude Code hook events.

Consumes HookEvent records (Notification, Stop, StopFailure, SessionEnd) read
from hook_events.jsonl and turns them into bot actions for every topic bound
to the event's window:
  - prompt notifications open the interactive UI, or send a fallback screenshot
    when the terminal parser does not recognize the screen (parser_miss)
  - Stop clears a stale status message
  - StopFailure reports the API error
  - SessionEnd for a window that dropped back to a shell offers a restart

Key components:
  - handle_hook_event: dispatch one event to every bound topic
  - send_prompt_screenshot: rate-limited screenshot with the screenshot keyboard
  - last_event_for / forget_window: per-window memory used by /health
"""

import asyncio
import io
import logging
import time

from telegram import Bot

from ..hook_events import HookEvent
from ..session import session_manager
from ..screenshot import text_to_image
from ..tmux_manager import tmux_manager
from .interactive_ui import handle_interactive_ui
from .message_queue import enqueue_status_update
from .message_sender import safe_send
from .screenshot_controls import build_screenshot_keyboard
from .session_lifecycle import build_not_running_keyboard, is_shell_command

logger = logging.getLogger(__name__)

PROMPT_NOTIFICATION_TYPES = frozenset(
    {"permission_prompt", "elicitation_dialog", "agent_needs_input"}
)
AUTH_ERRORS = frozenset({"authentication_failed", "oauth_org_not_allowed"})
FALLBACK_MIN_INTERVAL = 30.0  # seconds between fallback screenshots per window
UNRECOGNIZED_CAPTION = (
    "⚠ Claude Code is showing a screen ccbot does not recognize. Use the keys below."
)

# Retry delay before giving up on a prompt the parser does not see yet
_PROMPT_RETRY_DELAY = 1.0
# Delay before re-checking whether the window fell back to a shell
_SESSION_END_DELAY = 1.0
# SessionEnd reasons after which the session continues in the same window
_CONTINUING_END_REASONS = frozenset({"clear", "resume"})

# window_id -> (event name, ts) of the newest event seen for the window
_last_events: dict[str, tuple[str, float]] = {}
# window_id -> time.monotonic() of the last fallback screenshot attempt
_last_fallback: dict[str, float] = {}


def last_event_for(window_id: str) -> tuple[str, float] | None:
    """(event name, ts) of the newest hook event seen for the window."""
    return _last_events.get(window_id)


def forget_window(window_id: str) -> None:
    """Drop per-window memory (last event, last fallback time)."""
    _last_events.pop(window_id, None)
    _last_fallback.pop(window_id, None)


async def send_prompt_screenshot(
    bot: Bot,
    user_id: int,
    window_id: str,
    thread_id: int,
    caption: str,
) -> bool:
    """Send the pane as a screenshot with the screenshot keyboard.

    At most one attempt per window every FALLBACK_MIN_INTERVAL seconds; a skipped
    or failed attempt returns False.
    """
    now = time.monotonic()
    last = _last_fallback.get(window_id)
    if last is not None and now - last < FALLBACK_MIN_INTERVAL:
        return False
    _last_fallback[window_id] = now

    try:
        text = await tmux_manager.capture_pane(window_id, with_ansi=True)
        if not text:
            return False
        png_bytes = await text_to_image(text, with_ansi=True)
        await bot.send_document(
            chat_id=session_manager.resolve_chat_id(user_id, thread_id),
            document=io.BytesIO(png_bytes),
            filename="screenshot.png",
            caption=caption,
            reply_markup=build_screenshot_keyboard(window_id),
            message_thread_id=thread_id,
        )
        return True
    except Exception as e:
        logger.warning("Fallback screenshot failed for window %s: %s", window_id, e)
        return False


async def handle_hook_event(bot: Bot, event: HookEvent) -> None:
    """Record the event and react to it in every topic bound to its window."""
    _last_events[event.window_id] = (event.event, event.ts)

    bindings = [
        (user_id, thread_id)
        for user_id, thread_id, window_id in list(
            session_manager.iter_thread_bindings()
        )
        if window_id == event.window_id
    ]
    for user_id, thread_id in bindings:
        try:
            await _react(bot, user_id, thread_id, event)
        except Exception:
            logger.exception(
                "Hook reaction failed: event=%s window=%s user=%d thread=%d",
                event.event,
                event.window_id,
                user_id,
                thread_id,
            )


async def _react(bot: Bot, user_id: int, thread_id: int, event: HookEvent) -> None:
    if event.event == "Notification":
        await _on_notification(bot, user_id, thread_id, event)
    elif event.event == "Stop":
        await enqueue_status_update(
            bot, user_id, event.window_id, None, thread_id=thread_id
        )
    elif event.event == "StopFailure":
        await _on_stop_failure(bot, user_id, thread_id, event)
    elif event.event == "SessionEnd":
        await _on_session_end(bot, user_id, thread_id, event)


async def _on_notification(
    bot: Bot, user_id: int, thread_id: int, event: HookEvent
) -> None:
    notification_type = event.data.get("notification_type")
    if notification_type not in PROMPT_NOTIFICATION_TYPES:
        return
    window_id = event.window_id

    shown = await handle_interactive_ui(bot, user_id, window_id, thread_id)
    if not shown:
        # The dialog may not be rendered yet; look once more
        await asyncio.sleep(_PROMPT_RETRY_DELAY)
        shown = await handle_interactive_ui(bot, user_id, window_id, thread_id)
    if shown:
        return

    logger.warning(
        "parser_miss: window %s notification %s not recognized as interactive UI",
        window_id,
        notification_type,
    )
    await send_prompt_screenshot(
        bot, user_id, window_id, thread_id, UNRECOGNIZED_CAPTION
    )


async def _on_stop_failure(
    bot: Bot, user_id: int, thread_id: int, event: HookEvent
) -> None:
    error = event.data.get("error", "unknown")
    lines = [f"⚠ Turn ended with an API error: {error}"]
    last_message = event.data.get("last_assistant_message")
    if last_message:
        lines.append(last_message)
    if error in AUTH_ERRORS:
        lines.append("Use /login to sign in again.")
    await safe_send(
        bot,
        session_manager.resolve_chat_id(user_id, thread_id),
        "\n".join(lines),
        message_thread_id=thread_id,
    )


async def _on_session_end(
    bot: Bot, user_id: int, thread_id: int, event: HookEvent
) -> None:
    reason = event.data.get("reason", "")
    if reason in _CONTINUING_END_REASONS:
        return

    # Give the shell a moment to take over the pane before judging
    await asyncio.sleep(_SESSION_END_DELAY)
    window = await tmux_manager.find_window_by_id(event.window_id)
    if window is None or not is_shell_command(window.pane_current_command):
        return

    await safe_send(
        bot,
        session_manager.resolve_chat_id(user_id, thread_id),
        f"Claude session ended ({reason or 'unknown'}).",
        message_thread_id=thread_id,
        reply_markup=build_not_running_keyboard(event.window_id),
    )
