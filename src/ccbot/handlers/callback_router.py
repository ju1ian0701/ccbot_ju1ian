"""Central callback query router for inline keyboards.

Dispatches all CB_* interactions: history pagination, directory browser,
window/session pickers, screenshot keys, and interactive UI navigation.

Routing is table-driven (ISS-004): ``callback_handler`` performs only
auth + group-chat capture, then resolves the handler via the
_EXACT_ROUTES / _PREFIX_ROUTES registry.

Sub-handlers live in the callback_* submodules (ISS-011); the stale-topic
guard lives in callback_topic_guard. This module keeps only auth,
group-chat capture, and registry dispatch.
"""

from __future__ import annotations

import logging
from functools import partial

from telegram import CallbackQuery, Update, User
from telegram.ext import ContextTypes

from ..session import session_manager
from ..session_guard import get_thread_id, require_user
from .callback_data import (
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
)
from .callback_directory import (
    _handle_dir_cancel,
    _handle_dir_confirm,
    _handle_dir_page,
    _handle_dir_select,
    _handle_dir_up,
)
from .callback_history import _handle_history
from .callback_interactive import _ASK_ACTIONS, _handle_ask
from .callback_pickers import (
    _handle_session_cancel,
    _handle_session_new,
    _handle_session_select,
    _handle_win_bind,
    _handle_win_cancel,
    _handle_win_new,
)
from .callback_screenshot import _handle_key, _handle_screenshot_refresh
from .callback_topic_guard import RouteHandler

logger = logging.getLogger(__name__)


async def _handle_noop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """No-op button (pagination labels etc.): acknowledge only."""
    await query.answer()


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
