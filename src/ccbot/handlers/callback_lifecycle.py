"""Session lifecycle callbacks (sl:kq / sl:ko / sl:kc / sl:rs / sl:x).

Routed handlers using the ``RouteHandler`` signature. Every ``sl:*:<window>``
action first checks that the topic is still bound to that window (stale-button
guard), then acts on the window by tmux window_id:
  - kill ask: show the end-session confirmation
  - kill confirm: end the session (optionally close the topic)
  - restart: start Claude again in the window
  - cancel: dismiss the confirmation
"""

from __future__ import annotations

import logging

from telegram import CallbackQuery, Message, Update, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..session import session_manager
from ..session_guard import get_thread_id
from ..tmux_manager import tmux_manager
from .callback_data import CB_SL_KILL_CLOSE, LifecycleCallback, parse_lifecycle
from .message_sender import safe_edit, safe_reply, safe_send
from .session_lifecycle import (
    build_kill_confirm_keyboard,
    build_kill_confirm_text,
    end_session,
    is_shell_command,
    restart_claude,
)

logger = logging.getLogger(__name__)

_STALE_TEXT = "Stale button (session changed)"


async def _parse_bound_action(
    update: Update, user: User, query: CallbackQuery, data: str
) -> tuple[LifecycleCallback, int] | None:
    """Parse the callback and verify the topic is still bound to its window.

    Answers the query itself and returns None when the data is invalid or stale.
    """
    lc = parse_lifecycle(data)
    if lc is None:
        await query.answer("Invalid data")
        return None
    thread_id = get_thread_id(update)
    if (
        thread_id is None
        or session_manager.get_window_for_thread(user.id, thread_id) != lc.window_id
    ):
        await query.answer(_STALE_TEXT, show_alert=True)
        return None
    return lc, thread_id


async def _handle_kill_ask(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Show the end-session confirmation (same as /kill)."""
    parsed = await _parse_bound_action(update, user, query, data)
    if parsed is None:
        return
    lc, _thread_id = parsed
    if not isinstance(query.message, Message):
        await query.answer("Message is no longer available", show_alert=True)
        return
    display = session_manager.get_display_name(lc.window_id)
    cwd = session_manager.get_window_state(lc.window_id).cwd
    await safe_reply(
        query.message,
        build_kill_confirm_text(display, cwd),
        reply_markup=build_kill_confirm_keyboard(lc.window_id),
    )
    await query.answer()


async def _handle_kill_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Confirmed end (sl:ko:) or end-and-close-topic (sl:kc:)."""
    parsed = await _parse_bound_action(update, user, query, data)
    if parsed is None:
        return
    lc, thread_id = parsed
    assert update.effective_chat is not None
    chat_id = update.effective_chat.id

    await query.answer("Ending session…")
    await end_session(
        context.bot,
        user.id,
        thread_id,
        lc.window_id,
        user_data=context.user_data,
    )
    await safe_edit(query, "✅ Session ended. Send a message or /new to start another.")

    if lc.prefix == CB_SL_KILL_CLOSE:
        try:
            await context.bot.close_forum_topic(
                chat_id=chat_id, message_thread_id=thread_id
            )
        except TelegramError as e:
            logger.warning("close_forum_topic failed (thread=%d): %s", thread_id, e)
            await safe_send(
                context.bot,
                chat_id,
                "⚠ Session ended, but the topic could not be closed "
                "(the bot needs the Manage Topics admin right).",
                message_thread_id=thread_id,
            )


async def _handle_restart(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Start Claude again in the bound window."""
    parsed = await _parse_bound_action(update, user, query, data)
    if parsed is None:
        return
    lc, _thread_id = parsed
    window = await tmux_manager.find_window_by_id(lc.window_id)
    if window is None:
        await query.answer("Window not found", show_alert=True)
        return
    # The start command is typed into the pane: only safe at a shell prompt,
    # otherwise it would be sent to the running Claude as a prompt.
    if not is_shell_command(window.pane_current_command):
        await query.answer("Claude is already running", show_alert=True)
        return
    await restart_claude(lc.window_id)
    await safe_edit(query, "▶ Starting Claude…")
    await query.answer()


async def _handle_lifecycle_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Dismiss a lifecycle confirmation."""
    await safe_edit(query, "Cancelled")
    await query.answer("Cancelled")
