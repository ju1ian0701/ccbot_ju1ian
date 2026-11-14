"""Session lifecycle: end a Claude session from Telegram and guard against bare shells.

Shared logic for the ``/kill`` command and the ``sl:*`` callbacks:
  - end_session: graceful Claude exit, kill the tmux window, unbind the topic
  - restart_claude: start the configured Claude command in an existing window
  - reply_if_claude_not_running: shell guard that stops user text from being
    typed into a bare shell (e.g. after Claude exited via ``/exit``)
  - keyboards and confirmation text shared by the command and the callbacks

Invariant: 1 topic = 1 tmux window = 1 Claude session; all routing by window_id.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..config import config
from ..session import session_manager
from ..terminal_parser import has_chrome
from ..tmux_manager import TmuxWindow, tmux_manager
from .callback_data import (
    CB_SL_CANCEL,
    CB_SL_KILL_ASK,
    CB_SL_KILL_CLOSE,
    CB_SL_KILL_OK,
    CB_SL_RESTART,
    LifecycleCallback,
    safe_callback_data,
)
from .cleanup import clear_topic_state
from .message_sender import safe_reply

logger = logging.getLogger(__name__)

SHELL_COMMANDS = frozenset({"bash", "zsh", "sh", "fish", "dash", "ksh", "csh", "tcsh"})
GRACEFUL_EXIT_POLLS = 6  # × GRACEFUL_EXIT_POLL_INTERVAL seconds
GRACEFUL_EXIT_POLL_INTERVAL = 0.5

NOT_RUNNING_TEXT = (
    "⚠ Claude is not running in this window (shell prompt). Your message was not sent."
)


def is_shell_command(command: str) -> bool:
    """True if ``command`` (a pane's current command) is a bare shell."""
    name = command.strip()
    if name.startswith("-"):  # login shells show as "-bash"
        name = name[1:]
    return name in SHELL_COMMANDS


def build_kill_confirm_text(display: str, cwd: str) -> str:
    return (
        f"⏹ End session '{display}'?\n{cwd}\n"
        "The tmux window and the Claude process in it will be closed."
    )


def build_kill_confirm_keyboard(window_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ End session",
                    callback_data=safe_callback_data(
                        LifecycleCallback(prefix=CB_SL_KILL_OK, window_id=window_id)
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "🗑 End and close topic",
                    callback_data=safe_callback_data(
                        LifecycleCallback(prefix=CB_SL_KILL_CLOSE, window_id=window_id)
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "Cancel", callback_data=safe_callback_data(CB_SL_CANCEL)
                )
            ],
        ]
    )


def build_not_running_keyboard(window_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "▶ Restart Claude",
                    callback_data=safe_callback_data(
                        LifecycleCallback(prefix=CB_SL_RESTART, window_id=window_id)
                    ),
                ),
                InlineKeyboardButton(
                    "⏹ End session",
                    callback_data=safe_callback_data(
                        LifecycleCallback(prefix=CB_SL_KILL_ASK, window_id=window_id)
                    ),
                ),
            ]
        ]
    )


async def _graceful_exit(window_id: str) -> None:
    """Ask Claude to exit (so it flushes its transcript) and wait briefly for it."""
    await tmux_manager.send_keys(window_id, "Escape", enter=False, literal=False)
    await asyncio.sleep(0.5)
    await tmux_manager.send_keys(window_id, "/exit")
    for _ in range(GRACEFUL_EXIT_POLLS):
        await asyncio.sleep(GRACEFUL_EXIT_POLL_INTERVAL)
        current = await tmux_manager.find_window_by_id(window_id)
        if current is None or is_shell_command(current.pane_current_command):
            return


async def end_session(
    bot: Bot,
    user_id: int,
    thread_id: int,
    window_id: str,
    *,
    user_data: dict[str, Any] | None = None,
) -> bool:
    """End the Claude session bound to a topic.

    Returns True if a window was killed, False if it was already gone. Binding
    and topic state are cleaned up in both cases.
    """
    window = await tmux_manager.find_window_by_id(window_id)
    if window is not None and not is_shell_command(window.pane_current_command):
        try:
            await _graceful_exit(window_id)
        except Exception as e:
            logger.warning("Graceful exit failed for window %s: %s", window_id, e)

    killed = False
    if window is not None:
        killed = await tmux_manager.kill_window(window_id)

    session_manager.unbind_thread(user_id, thread_id)
    await clear_topic_state(user_id, thread_id, bot, user_data)

    logger.info(
        "Session end: window=%s killed=%s (user=%d, thread=%d)",
        window_id,
        killed,
        user_id,
        thread_id,
    )
    return killed


async def restart_claude(window_id: str) -> bool:
    """Start the configured Claude command in an existing window."""
    return await tmux_manager.send_keys(window_id, config.claude_command)


async def reply_if_claude_not_running(message: Message, window: TmuxWindow) -> bool:
    """True (and a warning reply with buttons) if the window shows a bare shell.

    Both signals are required: the pane's current command must be a shell AND
    the pane must lack Claude's chrome. Any unexpected error returns False so
    the guard can never block message delivery by itself.
    """
    try:
        if not is_shell_command(window.pane_current_command):
            return False
        pane = await tmux_manager.capture_pane(window.window_id)
        if has_chrome(pane or ""):
            return False
    except Exception as e:
        logger.warning("Shell guard check failed, sending anyway: %s", e)
        return False

    try:
        await safe_reply(
            message,
            NOT_RUNNING_TEXT,
            reply_markup=build_not_running_keyboard(window.window_id),
        )
    except Exception as e:
        # The decision is already made: never fall through to tmux.
        logger.warning("Shell guard reply failed: %s", e)
    return True
