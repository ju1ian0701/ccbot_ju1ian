"""Central callback query router for inline keyboards.

Dispatches all CB_* interactions: history pagination, directory browser,
window/session pickers, screenshot keys, and interactive UI navigation.

Routing is table-driven (ISS-004): ``callback_handler`` performs only
auth + group-chat capture, then resolves the handler via the
_EXACT_ROUTES / _PREFIX_ROUTES registry. The stale-topic guard for
picker/browser flows lives in a single helper, ``_check_same_topic``.
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from telegram import CallbackQuery, InputMediaDocument, Update, User
from telegram.ext import ContextTypes

from ..screenshot import text_to_image
from ..session import session_manager
from ..session_guard import get_thread_id, require_user
from ..tmux_manager import tmux_manager
from .callback_data import (
    CB_ASK_DOWN,
    CB_ASK_ENTER,
    CB_ASK_ESC,
    CB_ASK_LEFT,
    CB_ASK_REFRESH,
    CB_ASK_RIGHT,
    CB_ASK_SPACE,
    CB_ASK_TAB,
    CB_ASK_UP,
    CB_DIR_CANCEL,
    CB_DIR_CONFIRM,
    CB_DIR_PAGE,
    CB_DIR_SELECT,
    CB_DIR_UP,
    CB_HISTORY_NEXT,
    CB_HISTORY_PREV,
    CB_KEYS_PREFIX,
    CB_SCREENSHOT_REFRESH,
    CB_SESSION_CANCEL,
    CB_SESSION_NEW,
    CB_SESSION_SELECT,
    CB_WIN_BIND,
    CB_WIN_CANCEL,
    CB_WIN_NEW,
    AskCallback,
    DirPageCallback,
    DirSelectCallback,
    HistoryCallback,
    KeyCallback,
    ScreenshotRefreshCallback,
    SessionSelectCallback,
    WinBindCallback,
    parse_ask,
    parse_dir_page,
    parse_dir_select,
    parse_history,
    parse_key,
    parse_screenshot_refresh,
    parse_session_select,
    parse_win_bind,
)
from .directory_browser import (
    BROWSE_DIRS_KEY,
    BROWSE_PAGE_KEY,
    BROWSE_PATH_KEY,
    SESSIONS_KEY,
    STATE_BROWSING_DIRECTORY,
    STATE_KEY,
    STATE_SELECTING_SESSION,
    UNBOUND_WINDOWS_KEY,
    build_directory_browser,
    build_session_picker,
    clear_browse_state,
    clear_session_picker_state,
    clear_window_picker_state,
)
from .history import send_history
from .interactive_ui import clear_interactive_msg, handle_interactive_ui
from .message_sender import safe_edit, safe_send
from .screenshot_controls import (
    KEY_LABELS,
    KEYS_SEND_MAP,
    build_screenshot_keyboard,
)
from .window_bind import create_and_bind_window

logger = logging.getLogger(__name__)

# Handler signature for all routed callbacks: (update, context, user, query, data).
RouteHandler = Callable[
    [Update, ContextTypes.DEFAULT_TYPE, User, CallbackQuery, str],
    Awaitable[None],
]


@dataclass(frozen=True, slots=True)
class _TopicCheck:
    """Result of the stale-topic guard for picker/browser flows."""

    ok: bool
    pending_tid: int | None


async def _check_same_topic(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: CallbackQuery,
    *,
    recover: bool = False,
    stale_label: str = "picker",
    on_stale: Callable[[], None] | None = None,
) -> _TopicCheck:
    """Single stale-topic guard (ISS-004): pending flow vs callback topic.

    The callback must come from the same topic that started the pending
    flow (``_pending_thread_id`` in user_data). With ``recover``, a missing
    marker is recovered from the callback's own message context (e.g. after
    it was cleared by a message in another topic). On mismatch ``on_stale``
    runs first (state cleanup ordering), then the query is answered with a
    stale alert and ``ok=False`` is returned.
    """
    pending_tid = (
        context.user_data.get("_pending_thread_id") if context.user_data else None
    )
    if pending_tid is None and recover:
        pending_tid = get_thread_id(update)
    if pending_tid is not None and get_thread_id(update) != pending_tid:
        if on_stale is not None:
            on_stale()
        await query.answer(f"Stale {stale_label} (topic mismatch)", show_alert=True)
        return _TopicCheck(ok=False, pending_tid=None)
    return _TopicCheck(ok=True, pending_tid=pending_tid)


def _clear_pending_thread(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Drop the pending topic flow markers from user_data."""
    if context.user_data is not None:
        context.user_data.pop("_pending_thread_id", None)
        context.user_data.pop("_pending_thread_text", None)


async def _handle_history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """History pagination: hp|hn:<page>:<window_id>:<start>:<end>."""
    history_cb = parse_history(data)
    if not isinstance(history_cb, HistoryCallback):
        await query.answer("Invalid data")
        return
    window_id = history_cb.window_id
    w = await tmux_manager.find_window_by_id(window_id)
    if w:
        await send_history(
            query,
            window_id,
            offset=history_cb.page,
            edit=True,
            start_byte=history_cb.start_byte,
            end_byte=history_cb.end_byte,
            # Don't pass user_id for pagination - offset update only on initial view
            # This prevents offset from going backwards if new messages arrive while paging
        )
    else:
        await safe_edit(query, "Window no longer exists.")
    await query.answer("Page updated")


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


async def _handle_screenshot_refresh(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Screenshot: refresh the pane capture in place."""
    ss_refresh = parse_screenshot_refresh(data)
    if not isinstance(ss_refresh, ScreenshotRefreshCallback):
        await query.answer("Invalid data")
        return
    window_id = ss_refresh.window_id
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        await query.answer("Window no longer exists", show_alert=True)
        return

    text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if not text:
        await query.answer("Failed to capture pane", show_alert=True)
        return

    png_bytes = await text_to_image(text, with_ansi=True)
    keyboard = build_screenshot_keyboard(window_id)
    try:
        await query.edit_message_media(
            media=InputMediaDocument(
                media=io.BytesIO(png_bytes), filename="screenshot.png"
            ),
            reply_markup=keyboard,
        )
        await query.answer("Refreshed")
    except Exception as e:
        logger.error(f"Failed to refresh screenshot: {e}")
        await query.answer("Failed to refresh", show_alert=True)


async def _handle_noop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """No-op button (pagination labels etc.): acknowledge only."""
    await query.answer()


@dataclass(frozen=True, slots=True)
class _AskAction:
    """Behavior of one interactive-UI (aq:*) action."""

    tmux_key: str | None  # None → refresh display only (no key press)
    answer: str | None  # None → silent query.answer()
    clear: bool = False  # True → clear interactive message instead of UI refresh


_ASK_ACTIONS: dict[str, _AskAction] = {
    CB_ASK_UP: _AskAction("Up", None),
    CB_ASK_DOWN: _AskAction("Down", None),
    CB_ASK_LEFT: _AskAction("Left", None),
    CB_ASK_RIGHT: _AskAction("Right", None),
    CB_ASK_ESC: _AskAction("Escape", "⎋ Esc", clear=True),
    CB_ASK_ENTER: _AskAction("Enter", "⏎ Enter"),
    CB_ASK_SPACE: _AskAction("Space", "␣ Space"),
    CB_ASK_TAB: _AskAction("Tab", "⇥ Tab"),
    CB_ASK_REFRESH: _AskAction(None, "🔄"),
}


async def _handle_ask(
    prefix: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Interactive UI: single table-driven body for all aq:* actions."""
    ask_cb = parse_ask(data, prefix)
    if not isinstance(ask_cb, AskCallback):
        await query.answer("Invalid data")
        return
    action = _ASK_ACTIONS[prefix]
    window_id = ask_cb.window_id
    thread_id = get_thread_id(update)
    if action.tmux_key is None:
        # Refresh display: no key press, no window lookup.
        await handle_interactive_ui(context.bot, user.id, window_id, thread_id)
    else:
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, action.tmux_key, enter=False, literal=False
            )
            if action.clear:
                await clear_interactive_msg(user.id, context.bot, thread_id)
            else:
                await asyncio.sleep(0.5)
                await handle_interactive_ui(context.bot, user.id, window_id, thread_id)
    await query.answer(action.answer)


async def _handle_key(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Screenshot quick keys: send key to tmux window, refresh screenshot."""
    key_cb = parse_key(data)
    if not isinstance(key_cb, KeyCallback):
        await query.answer("Invalid data")
        return
    key_id = key_cb.key_id
    window_id = key_cb.window_id

    key_info = KEYS_SEND_MAP.get(key_id)
    if not key_info:
        await query.answer("Unknown key")
        return

    tmux_key, enter, literal = key_info
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        await query.answer("Window not found", show_alert=True)
        return

    await tmux_manager.send_keys(w.window_id, tmux_key, enter=enter, literal=literal)
    await query.answer(KEY_LABELS.get(key_id, key_id))

    # Refresh screenshot after key press
    await asyncio.sleep(0.5)
    text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if text:
        png_bytes = await text_to_image(text, with_ansi=True)
        keyboard = build_screenshot_keyboard(window_id)
        try:
            await query.edit_message_media(
                media=InputMediaDocument(
                    media=io.BytesIO(png_bytes),
                    filename="screenshot.png",
                ),
                reply_markup=keyboard,
            )
        except Exception:
            pass  # Screenshot unchanged or message too old


_EXACT_ROUTES: dict[str, RouteHandler] = {
    CB_DIR_UP: _handle_dir_up,
    CB_DIR_CONFIRM: _handle_dir_confirm,
    CB_DIR_CANCEL: _handle_dir_cancel,
    CB_SESSION_NEW: _handle_session_new,
    CB_SESSION_CANCEL: _handle_session_cancel,
    CB_WIN_NEW: _handle_win_new,
    CB_WIN_CANCEL: _handle_win_cancel,
    "noop": _handle_noop,
}

_ASK_ROUTES: tuple[tuple[str, RouteHandler], ...] = tuple(
    (prefix, partial(_handle_ask, prefix)) for prefix in _ASK_ACTIONS
)

# Prefix order is irrelevant (all prefixes are mutually exclusive); exact
# matches are checked first in _match_route.
_PREFIX_ROUTES: tuple[tuple[str, RouteHandler], ...] = (
    (CB_HISTORY_PREV, _handle_history),
    (CB_HISTORY_NEXT, _handle_history),
    (CB_DIR_SELECT, _handle_dir_select),
    (CB_DIR_PAGE, _handle_dir_page),
    (CB_SESSION_SELECT, _handle_session_select),
    (CB_WIN_BIND, _handle_win_bind),
    (CB_SCREENSHOT_REFRESH, _handle_screenshot_refresh),
    *_ASK_ROUTES,
    (CB_KEYS_PREFIX, _handle_key),
)


def _match_route(data: str) -> RouteHandler | None:
    """Resolve callback_data: exact-match table first, then prefix table."""
    handler = _EXACT_ROUTES.get(data)
    if handler is not None:
        return handler
    for prefix, prefix_handler in _PREFIX_ROUTES:
        if data.startswith(prefix):
            return prefix_handler
    return None


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point: auth + group-chat capture, then registry dispatch."""
    query = update.callback_query
    if not query or not query.data:
        return

    user = await require_user(update, reply_unauthorized=True)
    if user is None:
        return

    data = query.data

    # Capture group chat_id for supergroup forum topic routing.
    # Required: Telegram Bot API needs group chat_id (not user_id) to send
    # messages with message_thread_id. Do NOT remove — see session.py docs.
    cb_thread_id = get_thread_id(update)
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        session_manager.set_group_chat_id(user.id, cb_thread_id, chat.id)

    handler = _match_route(data)
    if handler is None:
        logger.debug("Unhandled callback data: %r", data)
        return
    await handler(update, context, user, query, data)
