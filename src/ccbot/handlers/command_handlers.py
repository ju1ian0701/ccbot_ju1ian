"""Telegram command and topic lifecycle handlers.

Handlers:
  - /start, /history, /screenshot, /unbind, /esc, /usage
  - topic_closed_handler, topic_edited_handler
  - forward_command_handler (unknown /slash → Claude Code)
  - unsupported_content_handler
"""

from __future__ import annotations

import asyncio
import io
import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from ..screenshot import text_to_image
from ..session import session_manager
from ..session_guard import (
    get_thread_id,
    require_bound_window_id,
    require_session,
    require_user,
)
from ..tmux_manager import tmux_manager
from .cleanup import clear_topic_state
from .directory_browser import clear_browse_state
from .history import send_history
from .message_sender import safe_reply
from .screenshot_controls import build_screenshot_keyboard

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await require_user(update, reply_unauthorized=True)
    if user is None:
        return

    clear_browse_state(context.user_data)

    if update.message:
        await safe_reply(
            update.message,
            "🤖 *Claude Code Monitor*\n\n"
            "Each topic is a session. Create a new topic to start.",
        )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show message history for the active session or bound thread."""
    bound = await require_bound_window_id(update, reply_unauthorized=False)
    if bound is None:
        return
    _user, _thread_id, window_id = bound
    assert update.message is not None
    await send_history(update.message, window_id)


async def screenshot_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Capture the current tmux pane and send it as an image."""
    ctx = await require_session(update, reply_unauthorized=False)
    if ctx is None:
        return
    assert update.message is not None

    text = await tmux_manager.capture_pane(ctx.window.window_id, with_ansi=True)
    if not text:
        await safe_reply(update.message, "❌ Failed to capture pane content.")
        return

    png_bytes = await text_to_image(text, with_ansi=True)
    keyboard = build_screenshot_keyboard(ctx.window_id)
    await update.message.reply_document(
        document=io.BytesIO(png_bytes),
        filename="screenshot.png",
        reply_markup=keyboard,
    )


async def unbind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unbind this topic from its Claude session without killing the window."""
    user = await require_user(update, reply_unauthorized=False)
    if user is None:
        return
    if not update.message:
        return

    thread_id = get_thread_id(update)
    if thread_id is None:
        await safe_reply(update.message, "❌ This command only works in a topic.")
        return

    bound = await require_bound_window_id(
        update,
        reply_unauthorized=False,
        use_resolve=False,
        user=user,
    )
    if bound is None:
        return
    _user, _tid, window_id = bound

    display = session_manager.get_display_name(window_id)
    session_manager.unbind_thread(user.id, thread_id)
    await clear_topic_state(user.id, thread_id, context.bot, context.user_data)

    await safe_reply(
        update.message,
        f"✅ Topic unbound from window '{display}'.\n"
        "The Claude session is still running in tmux.\n"
        "Send a message to bind to a new session.",
    )


async def esc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send Escape key to interrupt Claude."""
    ctx = await require_session(update, reply_unauthorized=False)
    if ctx is None:
        return
    assert update.message is not None

    # Send Escape control character (no enter)
    await tmux_manager.send_keys(ctx.window.window_id, "\x1b", enter=False)
    await safe_reply(update.message, "⎋ Sent Escape")


async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch Claude Code usage stats from TUI and send to Telegram."""
    ctx = await require_session(update, reply_unauthorized=False)
    if ctx is None:
        return
    assert update.message is not None

    # Send /usage command to Claude Code TUI
    await tmux_manager.send_keys(ctx.window.window_id, "/usage")
    # Wait for the modal to render
    await asyncio.sleep(2.0)
    # Capture the pane content
    pane_text = await tmux_manager.capture_pane(ctx.window.window_id)
    # Dismiss the modal
    await tmux_manager.send_keys(
        ctx.window.window_id, "Escape", enter=False, literal=False
    )

    if not pane_text:
        await safe_reply(update.message, "Failed to capture usage info.")
        return

    # Try to parse structured usage info
    from ..terminal_parser import parse_usage_output

    usage = parse_usage_output(pane_text)
    if usage and usage.parsed_lines:
        text = "\n".join(usage.parsed_lines)
        await safe_reply(update.message, f"```\n{text}\n```")
    else:
        # Fallback: send raw pane capture trimmed
        trimmed = pane_text.strip()
        if len(trimmed) > 3000:
            trimmed = trimmed[:3000] + "\n... (truncated)"
        await safe_reply(update.message, f"```\n{trimmed}\n```")


async def topic_closed_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle topic closure — kill the associated tmux window and clean up state.

    Uses require_session for auth+bind+live window. Failures are silent
    (topic lifecycle must not spam UX). Always unbind/clear when a binding
    existed, including when the live window is already gone.
    """
    user = await require_user(update, reply_unauthorized=False)
    if user is None:
        return

    thread_id = get_thread_id(update)
    if thread_id is None:
        return

    # Silent: no_session / missing_window must not reply on topic close.
    ctx = await require_session(
        update,
        reply_unauthorized=False,
        require_message=False,
        use_resolve=False,
        no_session_text="",
        missing_window_text="",
        user=user,
    )
    if ctx is not None:
        display = session_manager.get_display_name(ctx.window_id)
        await tmux_manager.kill_window(ctx.window.window_id)
        logger.info(
            "Topic closed: killed window %s (user=%d, thread=%d)",
            display,
            user.id,
            thread_id,
        )
    else:
        window_id = session_manager.get_window_for_thread(user.id, thread_id)
        if not window_id:
            logger.debug(
                "Topic closed: no binding (user=%d, thread=%d)", user.id, thread_id
            )
            return
        display = session_manager.get_display_name(window_id)
        logger.info(
            "Topic closed: window %s already gone (user=%d, thread=%d)",
            display,
            user.id,
            thread_id,
        )

    session_manager.unbind_thread(user.id, thread_id)
    await clear_topic_state(user.id, thread_id, context.bot, context.user_data)


async def topic_edited_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle topic rename — sync new name to tmux window and internal state."""
    user = await require_user(update, reply_unauthorized=False)
    if user is None:
        return

    msg = update.message
    if not msg or not msg.forum_topic_edited:
        return

    new_name = msg.forum_topic_edited.name
    if new_name is None:
        # Icon-only change, no rename needed
        return

    thread_id = get_thread_id(update)
    if thread_id is None:
        return

    window_id = session_manager.get_window_for_thread(user.id, thread_id)
    if not window_id:
        logger.debug(
            "Topic edited: no binding (user=%d, thread=%d)", user.id, thread_id
        )
        return

    old_name = session_manager.get_display_name(window_id)
    await tmux_manager.rename_window(window_id, new_name)
    session_manager.update_display_name(window_id, new_name)
    logger.info(
        "Topic renamed: '%s' -> '%s' (window=%s, user=%d, thread=%d)",
        old_name,
        new_name,
        window_id,
        user.id,
        thread_id,
    )


async def forward_command_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Forward any non-bot command as a slash command to the active Claude Code session."""
    user = await require_user(update, reply_unauthorized=False)
    if user is None:
        return
    if not update.message:
        return

    thread_id = get_thread_id(update)

    # Capture group chat_id for supergroup forum topic routing.
    # Required: Telegram Bot API needs group chat_id (not user_id) to send
    # messages with message_thread_id. Do NOT remove — see session.py docs.
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        session_manager.set_group_chat_id(user.id, thread_id, chat.id)

    cmd_text = update.message.text or ""
    # The full text is already a slash command like "/clear" or "/compact foo"
    cc_slash = cmd_text.split("@")[0]  # strip bot mention

    ctx = await require_session(update, reply_unauthorized=False, user=user)
    if ctx is None:
        return

    display = session_manager.get_display_name(ctx.window_id)
    logger.info(
        "Forwarding command %s to window %s (user=%d)", cc_slash, display, user.id
    )
    try:
        await update.message.chat.send_action(ChatAction.TYPING)
    except Exception as e:
        logger.warning("send_action(TYPING) failed, continuing to injection: %s", e)
    success, message = await session_manager.send_to_window(ctx.window_id, cc_slash)
    if success:
        await safe_reply(update.message, f"⚡ [{display}] Sent: {cc_slash}")
        # If /clear command was sent, clear the session association
        # so we can detect the new session after first message
        if cc_slash.strip().lower() == "/clear":
            logger.info("Clearing session for window %s after /clear", display)
            session_manager.clear_window_session(ctx.window_id)

        # Interactive commands (e.g. /model) render a terminal-based UI
        # with no JSONL tool_use entry.  The status poller already detects
        # interactive UIs every 1s (status_polling.py), so no
        # proactive detection needed here — the poller handles it.
    else:
        await safe_reply(update.message, f"❌ {message}")


async def unsupported_content_handler(
    update: Update,
    _context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Reply to non-text messages (stickers, video, etc.)."""
    if not update.message:
        return
    user = await require_user(update, reply_unauthorized=False)
    if user is None:
        return
    logger.debug("Unsupported content from user %d", user.id)
    await safe_reply(
        update.message,
        "⚠ Only text, photo, voice, and document (file) messages are "
        "supported. Stickers, video, and other media cannot be forwarded "
        "to Claude Code.",
    )
