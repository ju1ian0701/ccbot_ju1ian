"""Terminal status line polling for thread-bound windows.

Provides background polling of terminal status lines for all active users:
  - Detects Claude Code status (working, waiting, etc.)
  - Detects interactive UIs (permission prompts) not triggered via JSONL
  - Updates status messages in Telegram
  - Polls thread_bindings (each topic = one window)
  - Periodically probes topic existence via unpin_all_forum_topic_messages
    (silent no-op when no pins); cleans up deleted topics (kills tmux window
    + unbinds thread)
  - Consumes Claude Code hook events (hook_reactions) once per poll
  - Clears a stale status message when the screen shows Claude's chrome but
    no status line for several polls
  - Delivers Claude Code's /login sign-in link to the topic (login_flow)
  - Reports a parser canary: a window whose screen is neither a shell, Claude's
    chrome nor an interactive UI for many polls gets a fallback screenshot

Key components:
  - STATUS_POLL_INTERVAL: Polling frequency (1 second)
  - TOPIC_CHECK_INTERVAL: Topic existence probe frequency (60 seconds)
  - STATUS_CLEAR_POLLS / UNRECOGNIZED_SCREEN_POLLS: thresholds for the two rules
  - status_poll_loop: Background polling task
  - update_status_message: Poll and enqueue status updates
  - parser_state: Canary counters exposed to /health
"""

import asyncio
import logging
import time

from telegram import Bot
from telegram.error import BadRequest

from ..config import config
from ..health import claude_version
from ..hook_events import EVENTS_FILE_NAME, HookEventReader
from ..session import session_manager
from ..terminal_parser import has_chrome, is_interactive_ui, parse_status_line
from ..tmux_manager import TmuxWindow, tmux_manager
from . import hook_reactions, login_flow
from .cleanup import clear_topic_state
from .interactive_ui import (
    clear_interactive_msg,
    get_interactive_window,
    handle_interactive_ui,
)
from .hook_reactions import (
    UNRECOGNIZED_CAPTION,
    handle_hook_event,
    send_prompt_screenshot,
)
from .message_queue import enqueue_status_update, get_message_queue
from .session_lifecycle import is_shell_command

logger = logging.getLogger(__name__)

# Status polling interval
STATUS_POLL_INTERVAL = 1.0  # seconds - faster response (rate limiting at send layer)

# Topic existence probe interval
TOPIC_CHECK_INTERVAL = 60.0  # seconds

# Consecutive polls with Claude's chrome but no status line before the stale
# status message is cleared
STATUS_CLEAR_POLLS = 5

# Consecutive polls with an unrecognized screen before a fallback screenshot
UNRECOGNIZED_SCREEN_POLLS = 30

# Hook events are truncated (when fully consumed) once every this many polls
_HOOK_TRUNCATE_EVERY_POLLS = 600

# (user_id, thread_id_or_0) -> consecutive polls with chrome and no status line
_no_status_polls: dict[tuple[int, int], int] = {}

# window_id -> consecutive polls with an unrecognized screen
_unrecognized_polls: dict[str, int] = {}

# window_ids whose unrecognized screen was already reported (until it recognizes)
_reported_unrecognized: set[str] = set()


def parser_state(window_id: str) -> dict:
    """Parser canary state for one window (used by /health)."""
    return {
        "no_chrome_polls": _unrecognized_polls.get(window_id, 0),
        "reported": window_id in _reported_unrecognized,
    }


def _forget_binding(user_id: int, thread_id: int, window_id: str) -> None:
    """Drop all per-topic and per-window polling memory for a removed binding."""
    _no_status_polls.pop((user_id, thread_id or 0), None)
    _unrecognized_polls.pop(window_id, None)
    _reported_unrecognized.discard(window_id)
    hook_reactions.forget_window(window_id)
    login_flow.forget_topic(user_id, thread_id)


async def _check_screen_recognized(
    bot: Bot,
    user_id: int,
    window: TmuxWindow,
    pane_text: str,
    thread_id: int | None,
) -> None:
    """Count consecutive unrecognized screens and report the first burst of them."""
    window_id = window.window_id
    recognized = (
        is_shell_command(window.pane_current_command)
        or has_chrome(pane_text)
        or is_interactive_ui(pane_text)
        or login_flow.is_login_screen(pane_text)
    )
    if recognized:
        _unrecognized_polls[window_id] = 0
        _reported_unrecognized.discard(window_id)
        return

    count = _unrecognized_polls.get(window_id, 0) + 1
    _unrecognized_polls[window_id] = count
    if count != UNRECOGNIZED_SCREEN_POLLS or window_id in _reported_unrecognized:
        return

    _reported_unrecognized.add(window_id)
    logger.warning(
        "parser_miss: no chrome for %d polls on window %s (Claude Code %s)",
        count,
        window_id,
        claude_version() or "unknown",
    )
    if thread_id is not None:
        await send_prompt_screenshot(
            bot, user_id, window_id, thread_id, UNRECOGNIZED_CAPTION
        )


async def update_status_message(
    bot: Bot,
    user_id: int,
    window_id: str,
    thread_id: int | None = None,
    skip_status: bool = False,
) -> None:
    """Poll terminal and check for interactive UIs and status updates.

    UI detection always happens regardless of skip_status. When skip_status=True,
    only UI detection runs (used when message queue is non-empty to avoid
    flooding the queue with status updates).

    Also detects permission prompt UIs (not triggered via JSONL) and enters
    interactive mode when found.
    """
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        # Window gone, enqueue clear (unless skipping status)
        if not skip_status:
            await enqueue_status_update(
                bot, user_id, window_id, None, thread_id=thread_id
            )
        return

    pane_text = await tmux_manager.capture_pane(w.window_id)
    if not pane_text:
        # Transient capture failure - keep existing status message
        return

    await _check_screen_recognized(bot, user_id, w, pane_text, thread_id)

    try:
        await login_flow.on_pane(bot, user_id, thread_id, window_id, pane_text)
    except Exception as e:
        logger.debug("Login flow check failed: %s", type(e).__name__)

    interactive_window = get_interactive_window(user_id, thread_id)
    should_check_new_ui = True

    if interactive_window == window_id:
        # User is in interactive mode for THIS window
        if is_interactive_ui(pane_text):
            # Interactive UI still showing — skip status update (user is interacting)
            return
        # Interactive UI gone — clear interactive mode, fall through to status check.
        # Don't re-check for new UI this cycle (the old one just disappeared).
        await clear_interactive_msg(user_id, bot, thread_id)
        should_check_new_ui = False
    elif interactive_window is not None:
        # User is in interactive mode for a DIFFERENT window (window switched)
        # Clear stale interactive mode
        await clear_interactive_msg(user_id, bot, thread_id)

    # Check for permission prompt (interactive UI not triggered via JSONL)
    # ALWAYS check UI, regardless of skip_status
    if should_check_new_ui and is_interactive_ui(pane_text):
        logger.debug(
            "Interactive UI detected in polling (user=%d, window=%s, thread=%s)",
            user_id,
            window_id,
            thread_id,
        )
        await handle_interactive_ui(bot, user_id, window_id, thread_id)
        return

    # Normal status line check — skip if queue is non-empty
    if skip_status:
        return

    key = (user_id, thread_id or 0)
    status_line = parse_status_line(pane_text)

    if status_line:
        _no_status_polls.pop(key, None)
        await enqueue_status_update(
            bot,
            user_id,
            window_id,
            status_line,
            thread_id=thread_id,
        )
    elif has_chrome(pane_text):
        # Claude's chrome is on screen but no status line: after several polls
        # the status message is stale (the turn is over). Clear it once.
        # Without chrome, the screen is not Claude's: leave the status alone.
        count = _no_status_polls.get(key, 0) + 1
        _no_status_polls[key] = count
        if count == STATUS_CLEAR_POLLS:
            await enqueue_status_update(
                bot, user_id, window_id, None, thread_id=thread_id
            )


async def status_poll_loop(bot: Bot) -> None:
    """Background task to poll terminal status for all thread-bound windows."""
    logger.info("Status polling started (interval: %ss)", STATUS_POLL_INTERVAL)
    # Start at the end of the events file: old events are not replayed on start
    reader = HookEventReader(
        config.config_dir / EVENTS_FILE_NAME, config.tmux_session_name
    )
    poll_count = 0
    last_topic_check = 0.0
    while True:
        try:
            # Periodic topic existence probe
            now = time.monotonic()
            if now - last_topic_check >= TOPIC_CHECK_INTERVAL:
                last_topic_check = now
                for user_id, thread_id, window_id in list(
                    session_manager.iter_thread_bindings()
                ):
                    try:
                        await bot.unpin_all_forum_topic_messages(
                            chat_id=session_manager.resolve_chat_id(user_id, thread_id),
                            message_thread_id=thread_id,
                        )
                    except BadRequest as e:
                        if "Topic_id_invalid" in str(e):
                            # Topic deleted — kill window, unbind, and clean up state
                            w = await tmux_manager.find_window_by_id(window_id)
                            if w:
                                await tmux_manager.kill_window(w.window_id)
                            session_manager.unbind_thread(user_id, thread_id)
                            await clear_topic_state(user_id, thread_id, bot)
                            _forget_binding(user_id, thread_id, window_id)
                            logger.info(
                                "Topic deleted: killed window_id '%s' and "
                                "unbound thread %d for user %d",
                                window_id,
                                thread_id,
                                user_id,
                            )
                        else:
                            logger.debug(
                                "Topic probe error for %s: %s",
                                window_id,
                                e,
                            )
                    except Exception as e:
                        logger.debug(
                            "Topic probe error for %s: %s",
                            window_id,
                            e,
                        )

            # Structured hook events first, so reactions are not delayed by polling
            for event in reader.read_new():
                await handle_hook_event(bot, event)
            poll_count += 1
            if poll_count % _HOOK_TRUNCATE_EVERY_POLLS == 0:
                reader.maybe_truncate()

            for user_id, thread_id, window_id in list(
                session_manager.iter_thread_bindings()
            ):
                try:
                    # Clean up stale bindings (window no longer exists)
                    w = await tmux_manager.find_window_by_id(window_id)
                    if not w:
                        session_manager.unbind_thread(user_id, thread_id)
                        await clear_topic_state(user_id, thread_id, bot)
                        _forget_binding(user_id, thread_id, window_id)
                        logger.info(
                            "Cleaned up stale binding: user=%d thread=%d window_id=%s",
                            user_id,
                            thread_id,
                            window_id,
                        )
                        continue

                    # UI detection happens unconditionally in update_status_message.
                    # Status enqueue is skipped inside update_status_message when
                    # interactive UI is detected (returns early) or when queue is non-empty.
                    queue = get_message_queue(user_id)
                    skip_status = queue is not None and not queue.empty()

                    await update_status_message(
                        bot,
                        user_id,
                        window_id,
                        thread_id=thread_id,
                        skip_status=skip_status,
                    )
                except Exception as e:
                    logger.debug(
                        f"Status update error for user {user_id} "
                        f"thread {thread_id}: {e}"
                    )
        except Exception as e:
            logger.error(f"Status poll loop error: {e}")

        await asyncio.sleep(STATUS_POLL_INTERVAL)
