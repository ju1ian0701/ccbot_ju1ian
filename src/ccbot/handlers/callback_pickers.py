"""Session/window picker callbacks (rs:*, wb:*) — extracted from callback_router (ISS-011)."""

from __future__ import annotations

import logging
from pathlib import Path

from telegram import CallbackQuery, Update, User
from telegram.ext import ContextTypes

from ..session import session_manager
from ..session_guard import get_thread_id
from ..tmux_manager import tmux_manager
from .callback_data import (
    SessionSelectCallback,
    WinBindCallback,
    parse_session_select,
    parse_win_bind,
)
from .callback_topic_guard import _check_same_topic, _clear_pending_thread
from .directory_browser import (
    BROWSE_DIRS_KEY,
    BROWSE_PAGE_KEY,
    BROWSE_PATH_KEY,
    SESSIONS_KEY,
    STATE_BROWSING_DIRECTORY,
    STATE_KEY,
    UNBOUND_WINDOWS_KEY,
    build_directory_browser,
    clear_session_picker_state,
    clear_window_picker_state,
)
from .message_sender import safe_edit, safe_send
from .window_bind import create_and_bind_window

logger = logging.getLogger(__name__)


async def _handle_session_select(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Session picker: resume the selected existing session."""
    topic = await _check_same_topic(update, context, query, recover=True)
    if not topic.ok:
        return
    session_select = parse_session_select(data)
    if not isinstance(session_select, SessionSelectCallback):
        await query.answer("Invalid data")
        return
    idx = session_select.index

    cached_sessions = (
        context.user_data.get(SESSIONS_KEY, []) if context.user_data else []
    )
    if idx < 0 or idx >= len(cached_sessions):
        await query.answer("Session not found")
        return

    session = cached_sessions[idx]
    selected_path = (
        context.user_data.get("_selected_path", str(Path.cwd()))
        if context.user_data
        else str(Path.cwd())
    )
    clear_session_picker_state(context.user_data)
    if context.user_data is not None:
        context.user_data.pop("_selected_path", None)

    await create_and_bind_window(
        query,
        context,
        user,
        selected_path,
        topic.pending_tid,
        resume_session_id=session.session_id,
    )


async def _handle_session_new(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Session picker: start a new session in the selected directory."""
    topic = await _check_same_topic(update, context, query, recover=True)
    if not topic.ok:
        return
    selected_path = (
        context.user_data.get("_selected_path", str(Path.cwd()))
        if context.user_data
        else str(Path.cwd())
    )
    clear_session_picker_state(context.user_data)
    if context.user_data is not None:
        context.user_data.pop("_selected_path", None)

    await create_and_bind_window(query, context, user, selected_path, topic.pending_tid)


async def _handle_session_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Session picker: cancel selection."""
    topic = await _check_same_topic(update, context, query)
    if not topic.ok:
        return
    clear_session_picker_state(context.user_data)
    _clear_pending_thread(context)
    if context.user_data is not None:
        context.user_data.pop("_selected_path", None)
    await safe_edit(query, "Cancelled")
    await query.answer("Cancelled")


async def _handle_win_bind(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Window picker: bind an existing unbound window to this topic."""
    topic = await _check_same_topic(update, context, query)
    if not topic.ok:
        return
    win_bind = parse_win_bind(data)
    if not isinstance(win_bind, WinBindCallback):
        await query.answer("Invalid data")
        return
    idx = win_bind.index

    cached_windows: list[str] = (
        context.user_data.get(UNBOUND_WINDOWS_KEY, []) if context.user_data else []
    )
    if idx < 0 or idx >= len(cached_windows):
        await query.answer("Window list changed, please retry", show_alert=True)
        return
    selected_wid = cached_windows[idx]

    # Verify window still exists
    w = await tmux_manager.find_window_by_id(selected_wid)
    if not w:
        display = session_manager.get_display_name(selected_wid)
        await query.answer(f"Window '{display}' no longer exists", show_alert=True)
        return

    thread_id = get_thread_id(update)
    if thread_id is None:
        await query.answer("Not in a topic", show_alert=True)
        return

    display = w.window_name
    clear_window_picker_state(context.user_data)
    session_manager.bind_thread(user.id, thread_id, selected_wid, window_name=display)

    await safe_edit(
        query,
        f"✅ Bound to window `{display}`",
    )

    # Forward pending text if any
    pending_text = (
        context.user_data.get("_pending_thread_text") if context.user_data else None
    )
    _clear_pending_thread(context)
    if pending_text:
        send_ok, send_msg = await session_manager.send_to_window(
            selected_wid, pending_text
        )
        if not send_ok:
            logger.warning("Failed to forward pending text: %s", send_msg)
            resolved_chat = session_manager.resolve_chat_id(user.id, thread_id)
            await safe_send(
                context.bot,
                resolved_chat,
                f"❌ Failed to send pending message: {send_msg}",
                message_thread_id=thread_id,
            )
    await query.answer("Bound")


async def _handle_win_new(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Window picker: new session — transition to the directory browser."""
    topic = await _check_same_topic(update, context, query)
    if not topic.ok:
        return
    # Preserve pending thread info, clear only picker state
    clear_window_picker_state(context.user_data)
    start_path = str(Path.cwd())
    msg_text, keyboard, subdirs = build_directory_browser(start_path)
    if context.user_data is not None:
        context.user_data[STATE_KEY] = STATE_BROWSING_DIRECTORY
        context.user_data[BROWSE_PATH_KEY] = start_path
        context.user_data[BROWSE_PAGE_KEY] = 0
        context.user_data[BROWSE_DIRS_KEY] = subdirs
    await safe_edit(query, msg_text, reply_markup=keyboard)
    await query.answer()


async def _handle_win_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Window picker: cancel binding."""
    topic = await _check_same_topic(update, context, query)
    if not topic.ok:
        return
    clear_window_picker_state(context.user_data)
    _clear_pending_thread(context)
    await safe_edit(query, "Cancelled")
    await query.answer("Cancelled")
