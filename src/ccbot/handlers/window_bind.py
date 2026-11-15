"""Create a tmux window and bind it to a Telegram forum topic.

Shared by directory-confirm, session-new, session-select callback flows, and
the terminal-initiated `ccbot new` request handler.

Core responsibilities:
  - create_and_bind_window(): Telegram callback flow (create window, bind topic).
  - register_new_window(): wait for the SessionStart hook of a freshly created
    window and fix up --resume bookkeeping (reusable by any creation path).
"""

from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from ..session import session_manager
from ..tmux_manager import tmux_manager
from .bind_flow import rename_auto_topic
from .message_sender import safe_edit, safe_send
from .session_lifecycle import build_session_panel_keyboard

logger = logging.getLogger(__name__)


async def register_new_window(
    window_id: str,
    window_name: str,
    path: str,
    resume_session_id: str | None = None,
) -> bool:
    """Wait for the hook to register a freshly created window; fix up --resume.

    Returns hook_ok (True if the SessionStart hook registered the window in time).
    """
    # Wait for Claude Code's SessionStart hook to register in session_map.
    # Resume sessions take longer to start (loading session state), so use
    # a longer timeout to avoid silently dropping messages.
    hook_timeout = 15.0 if resume_session_id else 5.0
    hook_ok = await session_manager.wait_for_session_map_entry(
        window_id, timeout=hook_timeout
    )

    # --resume: messages keep writing to the resumed session's JSONL, and
    # current Claude Code reports the original session_id in the
    # SessionStart hook (source="resume"), so normally nothing to fix up.
    # If the hook timed out or reported a different id (older CC versions),
    # force both window_state AND session_map to the resumed id —
    # session_map drives the monitor's watch list, and load_session_map()
    # would revert a window_state-only override on the next poll cycle.
    if resume_session_id:
        ws = session_manager.get_window_state(window_id)
        if not hook_ok:
            logger.warning(
                "Hook timed out for resume window %s, "
                "manually setting session_id=%s cwd=%s",
                window_id,
                resume_session_id,
                path,
            )
            session_manager.set_window_session(
                window_id,
                resume_session_id,
                cwd=str(path),
                window_name=window_name,
            )
        elif ws.session_id != resume_session_id:
            logger.info(
                "Resume override: window %s session_id %s -> %s",
                window_id,
                ws.session_id,
                resume_session_id,
            )
            session_manager.set_window_session(window_id, resume_session_id)
        await session_manager.override_session_map_entry(
            window_id,
            resume_session_id,
            cwd=str(path),
            window_name=window_name,
        )
    return hook_ok


async def create_and_bind_window(
    query: object,
    context: ContextTypes.DEFAULT_TYPE,
    user: object,
    selected_path: str,
    pending_thread_id: int | None,
    resume_session_id: str | None = None,
) -> None:
    """Create a tmux window, bind it to a topic, and forward pending text.

    Shared by CB_DIR_CONFIRM (no sessions), CB_SESSION_NEW, and CB_SESSION_SELECT.
    """
    from telegram import CallbackQuery, User

    assert isinstance(query, CallbackQuery)
    assert isinstance(user, User)

    success, message, created_wname, created_wid = await tmux_manager.create_window(
        selected_path, resume_session_id=resume_session_id
    )
    if success:
        logger.info(
            "Window created: %s (id=%s) at %s (user=%d, thread=%s, resume=%s)",
            created_wname,
            created_wid,
            selected_path,
            user.id,
            pending_thread_id,
            resume_session_id,
        )
        await register_new_window(
            created_wid, created_wname, selected_path, resume_session_id
        )

        if pending_thread_id is not None:
            # Thread bind flow: bind thread to newly created window
            session_manager.bind_thread(
                user.id, pending_thread_id, created_wid, window_name=created_wname
            )

            status = "Resumed" if resume_session_id else "Created"
            await safe_edit(
                query,
                f"✅ {message}\n\n{status}. Send messages here.",
                reply_markup=build_session_panel_keyboard(created_wid),
            )
            await rename_auto_topic(
                context.bot, context, user.id, pending_thread_id, created_wname
            )

            # Send pending text if any
            pending_text = (
                context.user_data.get("_pending_thread_text")
                if context.user_data
                else None
            )
            if pending_text:
                logger.debug(
                    "Forwarding pending text to window %s (len=%d)",
                    created_wname,
                    len(pending_text),
                )
                if context.user_data is not None:
                    context.user_data.pop("_pending_thread_text", None)
                    context.user_data.pop("_pending_thread_id", None)
                send_ok, send_msg = await session_manager.send_to_window(
                    created_wid,
                    pending_text,
                )
                if not send_ok:
                    logger.warning("Failed to forward pending text: %s", send_msg)
                    resolved_chat = session_manager.resolve_chat_id(
                        user.id, pending_thread_id
                    )
                    await safe_send(
                        context.bot,
                        resolved_chat,
                        f"❌ Failed to send pending message: {send_msg}",
                        message_thread_id=pending_thread_id,
                    )
            elif context.user_data is not None:
                context.user_data.pop("_pending_thread_id", None)
        else:
            # Should not happen in topic-only mode, but handle gracefully
            await safe_edit(query, f"✅ {message}")
    else:
        await safe_edit(query, f"❌ {message}")
        if pending_thread_id is not None and context.user_data is not None:
            context.user_data.pop("_pending_thread_id", None)
            context.user_data.pop("_pending_thread_text", None)
    await query.answer("Created" if success else "Failed")
