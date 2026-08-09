"""Directory browser callbacks (db:*) — extracted from callback_router (ISS-011)."""

from __future__ import annotations

from pathlib import Path

from telegram import CallbackQuery, Update, User
from telegram.ext import ContextTypes

from ..session import session_manager
from .callback_data import (
    DirPageCallback,
    DirSelectCallback,
    parse_dir_page,
    parse_dir_select,
)
from .callback_topic_guard import _check_same_topic, _clear_pending_thread
from .directory_browser import (
    BROWSE_DIRS_KEY,
    BROWSE_PAGE_KEY,
    BROWSE_PATH_KEY,
    SESSIONS_KEY,
    STATE_KEY,
    STATE_SELECTING_SESSION,
    build_directory_browser,
    build_session_picker,
    clear_browse_state,
)
from .message_sender import safe_edit
from .window_bind import create_and_bind_window


async def _handle_dir_select(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Directory browser: enter the selected subdirectory."""
    topic = await _check_same_topic(update, context, query, stale_label="browser")
    if not topic.ok:
        return
    # callback_data contains index, not dir name (to avoid 64-byte limit)
    dir_select = parse_dir_select(data)
    if not isinstance(dir_select, DirSelectCallback):
        await query.answer("Invalid data")
        return
    idx = dir_select.index

    # Look up dir name from cached subdirs
    cached_dirs: list[str] = (
        context.user_data.get(BROWSE_DIRS_KEY, []) if context.user_data else []
    )
    if idx < 0 or idx >= len(cached_dirs):
        await query.answer("Directory list changed, please refresh", show_alert=True)
        return
    subdir_name = cached_dirs[idx]

    default_path = str(Path.cwd())
    current_path = (
        context.user_data.get(BROWSE_PATH_KEY, default_path)
        if context.user_data
        else default_path
    )
    new_path = (Path(current_path) / subdir_name).resolve()

    if not new_path.exists() or not new_path.is_dir():
        await query.answer("Directory not found", show_alert=True)
        return

    new_path_str = str(new_path)
    if context.user_data is not None:
        context.user_data[BROWSE_PATH_KEY] = new_path_str
        context.user_data[BROWSE_PAGE_KEY] = 0

    msg_text, keyboard, subdirs = build_directory_browser(new_path_str)
    if context.user_data is not None:
        context.user_data[BROWSE_DIRS_KEY] = subdirs
    await safe_edit(query, msg_text, reply_markup=keyboard)
    await query.answer()


async def _handle_dir_up(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Directory browser: navigate to the parent directory."""
    topic = await _check_same_topic(update, context, query, stale_label="browser")
    if not topic.ok:
        return
    default_path = str(Path.cwd())
    current_path = (
        context.user_data.get(BROWSE_PATH_KEY, default_path)
        if context.user_data
        else default_path
    )
    current = Path(current_path).resolve()
    parent = current.parent
    # No restriction - allow navigating anywhere

    parent_path = str(parent)
    if context.user_data is not None:
        context.user_data[BROWSE_PATH_KEY] = parent_path
        context.user_data[BROWSE_PAGE_KEY] = 0

    msg_text, keyboard, subdirs = build_directory_browser(parent_path)
    if context.user_data is not None:
        context.user_data[BROWSE_DIRS_KEY] = subdirs
    await safe_edit(query, msg_text, reply_markup=keyboard)
    await query.answer()


async def _handle_dir_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Directory browser: switch page of the current directory listing."""
    topic = await _check_same_topic(update, context, query, stale_label="browser")
    if not topic.ok:
        return
    dir_page = parse_dir_page(data)
    if not isinstance(dir_page, DirPageCallback):
        await query.answer("Invalid data")
        return
    pg = dir_page.page
    default_path = str(Path.cwd())
    current_path = (
        context.user_data.get(BROWSE_PATH_KEY, default_path)
        if context.user_data
        else default_path
    )
    if context.user_data is not None:
        context.user_data[BROWSE_PAGE_KEY] = pg

    msg_text, keyboard, subdirs = build_directory_browser(current_path, pg)
    if context.user_data is not None:
        context.user_data[BROWSE_DIRS_KEY] = subdirs
    await safe_edit(query, msg_text, reply_markup=keyboard)
    await query.answer()


async def _handle_dir_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Directory browser: confirm path; pick existing session or bind new."""

    def _clear_stale_state() -> None:
        clear_browse_state(context.user_data)
        _clear_pending_thread(context)

    topic = await _check_same_topic(
        update, context, query, stale_label="browser", on_stale=_clear_stale_state
    )
    default_path = str(Path.cwd())
    selected_path = (
        context.user_data.get(BROWSE_PATH_KEY, default_path)
        if context.user_data
        else default_path
    )
    if not topic.ok:
        return

    clear_browse_state(context.user_data)

    # Check for existing sessions in this directory
    sessions = await session_manager.list_sessions_for_directory(selected_path)
    if sessions:
        # Show session picker — store state for later
        if context.user_data is not None:
            context.user_data[STATE_KEY] = STATE_SELECTING_SESSION
            context.user_data[SESSIONS_KEY] = sessions
            context.user_data["_selected_path"] = selected_path
        text, keyboard = build_session_picker(sessions)
        await safe_edit(query, text, reply_markup=keyboard)
        await query.answer()
        return

    # No existing sessions — create new window directly
    await create_and_bind_window(query, context, user, selected_path, topic.pending_tid)


async def _handle_dir_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Directory browser: cancel browsing."""
    topic = await _check_same_topic(update, context, query, stale_label="browser")
    if not topic.ok:
        return
    clear_browse_state(context.user_data)
    _clear_pending_thread(context)
    await safe_edit(query, "Cancelled")
    await query.answer("Cancelled")
