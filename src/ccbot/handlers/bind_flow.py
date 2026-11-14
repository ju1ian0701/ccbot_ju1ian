"""Bind flow for unbound topics and auto-created topics for new sessions.

Shared entry points for "start a session in a topic":
  - start_bind_flow: an unbound topic got a message (or /new) → window picker
    when unbound tmux windows exist, otherwise the directory browser.
  - start_new_topic_flow: create a forum topic and open the directory browser
    inside it (used by /new in General and the "New topic" panel button).
  - rename_auto_topic / discard_auto_topic: an auto-created topic is renamed to
    the bound window name on success, or deleted when the flow is cancelled.

Auto-created topics are tracked by ``user_data["_auto_topic_id"]``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from telegram import Bot, Message
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..session import session_manager
from ..tmux_manager import tmux_manager
from .directory_browser import (
    BROWSE_DIRS_KEY,
    BROWSE_PAGE_KEY,
    BROWSE_PATH_KEY,
    STATE_BROWSING_DIRECTORY,
    STATE_KEY,
    STATE_SELECTING_WINDOW,
    UNBOUND_WINDOWS_KEY,
    build_directory_browser,
    build_window_picker,
    clear_browse_state,
    clear_session_picker_state,
    clear_window_picker_state,
)
from .message_sender import safe_reply, safe_send

logger = logging.getLogger(__name__)

AUTO_TOPIC_KEY = "_auto_topic_id"
AUTO_TOPIC_NAME = "🆕 new session"


async def start_bind_flow(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    thread_id: int,
    pending_text: str | None,
) -> None:
    """Offer to bind an unbound topic: window picker, else directory browser.

    ``pending_text`` (the user's message) is forwarded to the session once the
    topic is bound; pass None when there is nothing to forward (e.g. /new).
    """
    all_windows = await tmux_manager.list_windows()
    bound_ids = {
        window_id for _, _, window_id in session_manager.iter_thread_bindings()
    }
    unbound = [
        (w.window_id, w.window_name, w.cwd)
        for w in all_windows
        if w.window_id not in bound_ids
    ]
    logger.debug(
        "Window picker check: all=%s, bound=%s, unbound=%s",
        [w.window_name for w in all_windows],
        bound_ids,
        [name for _, name, _ in unbound],
    )

    if unbound:
        # Show window picker
        logger.info(
            "Unbound topic: showing window picker (%d unbound windows, user=%d, thread=%d)",
            len(unbound),
            user_id,
            thread_id,
        )
        msg_text, keyboard, win_ids = build_window_picker(unbound)
        if context.user_data is not None:
            context.user_data[STATE_KEY] = STATE_SELECTING_WINDOW
            context.user_data[UNBOUND_WINDOWS_KEY] = win_ids
            context.user_data["_pending_thread_id"] = thread_id
            if pending_text is not None:
                context.user_data["_pending_thread_text"] = pending_text
            else:
                context.user_data.pop("_pending_thread_text", None)
        await safe_reply(message, msg_text, reply_markup=keyboard)
        return

    # No unbound windows — show directory browser to create a new session
    logger.info(
        "Unbound topic: showing directory browser (user=%d, thread=%d)",
        user_id,
        thread_id,
    )
    start_path = str(Path.cwd())
    msg_text, keyboard, subdirs = build_directory_browser(start_path)
    if context.user_data is not None:
        context.user_data[STATE_KEY] = STATE_BROWSING_DIRECTORY
        context.user_data[BROWSE_PATH_KEY] = start_path
        context.user_data[BROWSE_PAGE_KEY] = 0
        context.user_data[BROWSE_DIRS_KEY] = subdirs
        context.user_data["_pending_thread_id"] = thread_id
        if pending_text is not None:
            context.user_data["_pending_thread_text"] = pending_text
        else:
            context.user_data.pop("_pending_thread_text", None)
    await safe_reply(message, msg_text, reply_markup=keyboard)


async def start_new_topic_flow(
    bot: Bot,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    start_path: str | None,
) -> int | None:
    """Create a forum topic and open the directory browser inside it.

    Returns the new topic's thread id, or None when the topic could not be
    created (the bot needs the Manage Topics admin right).
    """
    try:
        topic = await bot.create_forum_topic(chat_id=chat_id, name=AUTO_TOPIC_NAME)
    except TelegramError as e:
        logger.warning("Could not create forum topic in chat %s: %s", chat_id, e)
        return None

    new_tid = topic.message_thread_id
    session_manager.set_group_chat_id(user_id, new_tid, chat_id)

    if start_path is not None and Path(start_path).is_dir():
        browse_start = start_path
    else:
        browse_start = str(Path.cwd())

    clear_browse_state(context.user_data)
    clear_window_picker_state(context.user_data)
    clear_session_picker_state(context.user_data)
    msg_text, keyboard, subdirs = build_directory_browser(browse_start)
    if context.user_data is not None:
        context.user_data[STATE_KEY] = STATE_BROWSING_DIRECTORY
        context.user_data[BROWSE_PATH_KEY] = browse_start
        context.user_data[BROWSE_PAGE_KEY] = 0
        context.user_data[BROWSE_DIRS_KEY] = subdirs
        context.user_data["_pending_thread_id"] = new_tid
        context.user_data.pop("_pending_thread_text", None)
        context.user_data[AUTO_TOPIC_KEY] = new_tid

    await safe_send(
        bot,
        chat_id,
        msg_text,
        message_thread_id=new_tid,
        reply_markup=keyboard,
    )
    logger.info(
        "Auto topic created: thread=%d, chat=%d, user=%d", new_tid, chat_id, user_id
    )
    return new_tid


async def rename_auto_topic(
    bot: Bot,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    thread_id: int,
    name: str,
) -> None:
    """Rename an auto-created topic to the bound window's name (once)."""
    if context.user_data is None or context.user_data.get(AUTO_TOPIC_KEY) != thread_id:
        return
    context.user_data.pop(AUTO_TOPIC_KEY, None)
    try:
        await bot.edit_forum_topic(
            chat_id=session_manager.resolve_chat_id(user_id, thread_id),
            message_thread_id=thread_id,
            name=name,
        )
    except TelegramError as e:
        logger.warning("Could not rename auto topic %d: %s", thread_id, e)


async def discard_auto_topic(
    bot: Bot,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    thread_id: int | None,
) -> None:
    """Delete an auto-created topic whose session flow was cancelled."""
    if thread_id is None:
        return
    if context.user_data is None or context.user_data.get(AUTO_TOPIC_KEY) != thread_id:
        return
    context.user_data.pop(AUTO_TOPIC_KEY, None)
    try:
        await bot.delete_forum_topic(
            chat_id=session_manager.resolve_chat_id(user_id, thread_id),
            message_thread_id=thread_id,
        )
    except TelegramError as e:
        logger.warning("Could not delete auto topic %d: %s", thread_id, e)


__all__ = [
    "AUTO_TOPIC_KEY",
    "discard_auto_topic",
    "rename_auto_topic",
    "start_bind_flow",
    "start_new_topic_flow",
]
