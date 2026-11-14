"""Bot-side executor for file-based control requests from the `ccbot new` CLI.

The CLI cannot touch the bot's in-memory state, so it writes a request into the
control spool (see control.py). This module polls that spool, runs each request
with the bot's normal code paths, and writes a result file back.

Core responsibilities:
  - control_poll_loop(): background task polling the spool every second.
  - process_pending(): handle every queued request once (testable unit).
  - handle_request(): dispatch by action; never raises.
  - handle_new_session(): create a tmux window plus a bound forum topic.

Requests are answered with {"ok": bool, ...}. Failures carry "error".
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from telegram import Bot
from telegram.error import TelegramError

from .. import control
from ..config import config
from ..session import session_manager
from ..tmux_manager import tmux_manager
from .message_sender import safe_send
from .window_bind import register_new_window

logger = logging.getLogger(__name__)

CONTROL_POLL_INTERVAL = 1.0
# Stale request/result cleanup runs once per this many poll iterations (~10 min).
CONTROL_CLEANUP_EVERY = 600

_SESSION_ID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_NAME_MAX_LEN = 64


def _fail(error: str) -> dict[str, Any]:
    return {"ok": False, "error": error}


async def process_pending(bot: Bot) -> None:
    """Run every queued control request once, writing and removing each one."""
    pending = await asyncio.to_thread(control.list_requests)
    for request_id, req in pending:
        result = await handle_request(bot, req["action"], req.get("params") or {})
        await asyncio.to_thread(control.write_result, request_id, result)
        await asyncio.to_thread(control.delete_request, request_id)


async def control_poll_loop(bot: Bot) -> None:
    """Poll the control spool forever; a failing iteration never stops the loop."""
    iteration = 0
    while True:
        await asyncio.sleep(CONTROL_POLL_INTERVAL)
        try:
            await process_pending(bot)
            iteration += 1
            if iteration % CONTROL_CLEANUP_EVERY == 0:
                removed = await asyncio.to_thread(control.cleanup_stale)
                if removed:
                    logger.info("Removed %d stale control files", removed)
        except Exception:
            logger.exception("Control request loop iteration failed")


async def handle_request(bot: Bot, action: str, params: dict) -> dict:
    """Dispatch one control request. Unexpected exceptions become error results."""
    if action != "new_session":
        return _fail(f"unknown action: {action}")
    try:
        return await handle_new_session(bot, params)
    except Exception as e:
        logger.exception("Control request %r failed", action)
        return _fail(f"internal error: {type(e).__name__}")


async def _delete_topic(bot: Bot, chat_id: int, thread_id: int) -> None:
    """Best-effort removal of a topic created for a request that failed."""
    try:
        await bot.delete_forum_topic(chat_id=chat_id, message_thread_id=thread_id)
    except Exception:
        logger.exception("Failed to roll back topic %s in chat %s", thread_id, chat_id)


def _resolve_user_id(params: dict) -> int | dict[str, Any]:
    """Return the requesting user id, or an error result dict."""
    user_id = params.get("user_id")
    if user_id is None:
        if len(config.allowed_users) != 1:
            return _fail("user_id is required (several ALLOWED_USERS)")
        user_id = next(iter(config.allowed_users))
    elif isinstance(user_id, bool) or not isinstance(user_id, int):
        return _fail("invalid user_id")
    if not config.is_user_allowed(user_id):
        return _fail("user not allowed")
    return user_id


def _resolve_chat_id(user_id: int) -> int | None:
    """Forum group for a user: CCBOT_GROUP_CHAT_ID, else the last group they used."""
    if config.group_chat_id is not None:
        return config.group_chat_id
    prefix = f"{user_id}:"
    chat_id: int | None = None
    for key, value in session_manager.group_chat_ids.items():
        if key.startswith(prefix):
            chat_id = value
    return chat_id


async def handle_new_session(bot: Bot, params: dict) -> dict:
    """Create a tmux window and a bound forum topic for a terminal request."""
    # 1. Directory
    cwd = params.get("cwd")
    if not isinstance(cwd, str):
        return _fail("cwd must be a string")
    path = Path(cwd).expanduser()
    if not path.is_dir():
        return _fail(f"not a directory: {cwd}")

    # 2. Resume session id
    resume = params.get("resume")
    if resume is not None and (
        not isinstance(resume, str) or not _SESSION_ID_RE.fullmatch(resume)
    ):
        return _fail("invalid session id")

    # 3. Topic/window name
    name = params.get("name")
    if name is not None:
        if not isinstance(name, str):
            return _fail("invalid name")
        name = "".join(ch for ch in name if ch.isprintable()).strip()
        if not 1 <= len(name) <= _NAME_MAX_LEN:
            return _fail("invalid name")

    # 4. User
    resolved = _resolve_user_id(params)
    if isinstance(resolved, dict):
        return resolved
    user_id = resolved

    # 5. Forum group
    chat_id = _resolve_chat_id(user_id)
    if chat_id is None:
        return _fail(
            "no forum group known: set CCBOT_GROUP_CHAT_ID or send any message "
            "to the bot in the group first"
        )

    # 6. Topic
    topic_name = name or path.name or "session"
    try:
        topic = await bot.create_forum_topic(chat_id=chat_id, name=topic_name)
    except TelegramError as e:
        return _fail(
            f"cannot create topic: {e} (the bot needs the Manage Topics admin right)"
        )
    thread_id = topic.message_thread_id

    # 7+. Window and bindings; roll back the topic on any failure after this point.
    window_id: str | None = None
    bound = False
    try:
        success, message, wname, wid = await tmux_manager.create_window(
            str(path), window_name=name or None, resume_session_id=resume
        )
        if not success:
            await _delete_topic(bot, chat_id, thread_id)
            return _fail(message)
        window_id = wid

        hook_ok = await register_new_window(wid, wname, str(path), resume)
        session_manager.set_group_chat_id(user_id, thread_id, chat_id)
        session_manager.bind_thread(user_id, thread_id, wid, window_name=wname)
        bound = True
        logger.info(
            "Session created from terminal: window %s (%s), thread %s, user %d",
            wid,
            wname,
            thread_id,
            user_id,
        )

        try:
            await safe_send(
                bot,
                chat_id,
                f"🖥 Session started from terminal: {wname}\n{path}",
                message_thread_id=thread_id,
            )
        except Exception:
            logger.exception("Failed to send session notice to thread %s", thread_id)

        chat_str = str(chat_id)
        link = (
            f"https://t.me/c/{chat_str[4:]}/{thread_id}"
            if chat_str.startswith("-100")
            else None
        )
        return {
            "ok": True,
            "window_id": wid,
            "window_name": wname,
            "thread_id": thread_id,
            "tmux_session": config.tmux_session_name,
            "hook_registered": hook_ok,
            "link": link,
        }
    except Exception:
        if bound:
            session_manager.unbind_thread(user_id, thread_id)
        if window_id:
            try:
                await tmux_manager.kill_window(window_id)
            except Exception:
                logger.exception("Failed to roll back window %s", window_id)
        await _delete_topic(bot, chat_id, thread_id)
        raise
