"""Session lifecycle callbacks (sl:*).

Routed handlers using the ``RouteHandler`` signature. Every ``sl:*:<window>``
action first checks that the topic is still bound to that window (stale-button
guard), then acts on the window by tmux window_id:
  - kill ask: show the end-session confirmation
  - kill confirm: end the session (optionally close the topic)
  - restart: start Claude again in the window
  - cancel: dismiss the confirmation
  - panel: /new choice (sl:new, sl:nh:, sl:nt), screenshot (sl:sc:), Escape (sl:es:)
"""

from __future__ import annotations

import io
import logging

from telegram import CallbackQuery, Message, Update, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..screenshot import text_to_image
from ..session import session_manager
from ..session_guard import get_thread_id
from ..tmux_manager import tmux_manager
from .bind_flow import start_new_topic_flow
from .callback_data import CB_SL_KILL_CLOSE, LifecycleCallback, parse_lifecycle
from .message_sender import safe_edit, safe_reply, safe_send
from .screenshot_controls import build_screenshot_keyboard
from .session_lifecycle import (
    NEW_SESSION_TEXT,
    build_kill_confirm_keyboard,
    build_kill_confirm_text,
    build_new_session_keyboard,
    end_session,
    is_shell_command,
    restart_claude,
)
from .window_bind import create_and_bind_window

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


async def _handle_new(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Panel "New session" (sl:new): offer restart-here or a new topic."""
    thread_id = get_thread_id(update)
    window_id = (
        session_manager.get_window_for_thread(user.id, thread_id)
        if thread_id is not None
        else None
    )
    if window_id is None:
        await query.answer("No session in this topic", show_alert=True)
        return
    if not isinstance(query.message, Message):
        await query.answer("Message is no longer available", show_alert=True)
        return
    await safe_reply(
        query.message,
        NEW_SESSION_TEXT,
        reply_markup=build_new_session_keyboard(window_id),
    )
    await query.answer()


async def _handle_new_here(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Restart here (sl:nh:): end this session and start a fresh one in the topic."""
    parsed = await _parse_bound_action(update, user, query, data)
    if parsed is None:
        return
    lc, thread_id = parsed

    cwd = session_manager.get_window_state(lc.window_id).cwd
    if not cwd:
        live = await tmux_manager.find_window_by_id(lc.window_id)
        cwd = live.cwd if live is not None else ""
    if not cwd:
        await query.answer("Unknown working directory", show_alert=True)
        return

    # create_and_bind_window edits this message and answers the query itself.
    await safe_edit(query, "🔄 Restarting session…")
    await end_session(
        context.bot,
        user.id,
        thread_id,
        lc.window_id,
        user_data=context.user_data,
    )
    await create_and_bind_window(query, context, user, cwd, thread_id)


async def _handle_new_topic(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Start a session in a brand-new topic (sl:nt)."""
    assert update.effective_chat is not None
    chat_id = update.effective_chat.id

    # Start in the directory of the session bound to this topic, if any.
    thread_id = get_thread_id(update)
    start_path: str | None = None
    if thread_id is not None:
        window_id = session_manager.get_window_for_thread(user.id, thread_id)
        if window_id is not None:
            start_path = session_manager.get_window_state(window_id).cwd or None

    new_tid = await start_new_topic_flow(
        context.bot, context, user.id, chat_id, start_path
    )
    if new_tid is None:
        await safe_edit(
            query,
            "❌ Could not create a topic (the bot needs the Manage Topics admin right).",
        )
    else:
        await safe_edit(query, "➕ Topic created — choose a directory there.")
    await query.answer()


async def _handle_panel_screenshot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Panel "Screenshot" (sl:sc:<window>): send the terminal as an image."""
    parsed = await _parse_bound_action(update, user, query, data)
    if parsed is None:
        return
    lc, _thread_id = parsed

    text = await tmux_manager.capture_pane(lc.window_id, with_ansi=True)
    if not text:
        await query.answer("Failed to capture pane", show_alert=True)
        return
    if not isinstance(query.message, Message):
        await query.answer("Message is no longer available", show_alert=True)
        return

    png_bytes = await text_to_image(text, with_ansi=True)
    await query.message.reply_document(
        document=io.BytesIO(png_bytes),
        filename="screenshot.png",
        reply_markup=build_screenshot_keyboard(lc.window_id),
    )
    await query.answer()


async def _handle_panel_esc(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Panel "Esc" (sl:es:<window>): send Escape to interrupt Claude."""
    parsed = await _parse_bound_action(update, user, query, data)
    if parsed is None:
        return
    lc, _thread_id = parsed

    await tmux_manager.send_keys(lc.window_id, "\x1b", enter=False)
    await query.answer("⎋ Sent Escape")
