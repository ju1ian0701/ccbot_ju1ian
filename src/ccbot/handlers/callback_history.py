"""History pagination callback (hp:/hn:) — extracted from callback_router (ISS-011)."""

from __future__ import annotations

from telegram import CallbackQuery, Update, User
from telegram.ext import ContextTypes

from ..tmux_manager import tmux_manager
from .callback_data import HistoryCallback, parse_history
from .history import send_history
from .message_sender import safe_edit


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
