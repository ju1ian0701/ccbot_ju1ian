"""Telegram document (file) upload handler.

Downloads files sent to a bound topic into ``~/.ccbot/documents/`` and
forwards the saved path to the Claude Code session as
``(file attached: <path>)``.

Auth and session resolution use the fork canon (``require_user`` /
``require_session``), not the pre-ISS-001 dual path.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from ..session import session_manager
from ..session_guard import get_thread_id, require_session, require_user
from ..utils import ccbot_dir
from .message_queue import clear_status_msg_info
from .message_sender import safe_reply

logger = logging.getLogger(__name__)

# Incoming files are saved here before the path is forwarded to Claude Code.
DOCS_DIR = ccbot_dir() / "documents"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

# Telegram Bot API caps bot downloads (getFile) at 20 MB; larger files cannot
# be fetched and must be rejected before attempting the download.
_MAX_DOC_BYTES = 20 * 1024 * 1024


def _format_size(num_bytes: int) -> str:
    """Render a byte count as a human-readable size (e.g. '24.3 MB')."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _safe_filename(name: str) -> str:
    """Sanitize a Telegram-provided filename for safe use as a path component."""
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name.strip("._") or "file"


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle documents (PDF, etc.): download and forward path to Claude Code."""
    user = await require_user(update, reply_unauthorized=True)
    if user is None:
        return

    if not update.message or not update.message.document:
        return

    chat = update.message.chat
    thread_id = get_thread_id(update)
    if chat.type in ("group", "supergroup") and thread_id is not None:
        session_manager.set_group_chat_id(user.id, thread_id, chat.id)

    ctx = await require_session(
        update,
        reply_unauthorized=False,
        user=user,
        use_resolve=False,
        require_named_topic=True,
        unbind_if_missing=True,
    )
    if ctx is None:
        return

    doc = update.message.document
    if doc.file_size and doc.file_size > _MAX_DOC_BYTES:
        await safe_reply(
            update.message,
            f"❌ File is too large ({_format_size(doc.file_size)}). "
            f"Telegram only lets bots download files up to "
            f"{_format_size(_MAX_DOC_BYTES)}.",
        )
        return

    original = doc.file_name or f"{doc.file_unique_id}"
    filename = f"{int(time.time())}_{_safe_filename(original)}"
    file_path = DOCS_DIR / filename

    try:
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(file_path)
    except BadRequest as exc:
        logger.warning("Document download failed for user %d: %s", user.id, exc)
        await safe_reply(
            update.message,
            f"❌ Could not download the file. It may exceed Telegram's "
            f"{_format_size(_MAX_DOC_BYTES)} download limit for bots.",
        )
        return

    caption = update.message.caption or ""
    if caption:
        text_to_send = f"{caption}\n\n(file attached: {file_path})"
    else:
        text_to_send = f"(file attached: {file_path})"

    try:
        await update.message.chat.send_action(ChatAction.TYPING)
    except Exception as e:
        logger.warning("send_action(TYPING) failed, continuing to injection: %s", e)
    clear_status_msg_info(user.id, ctx.thread_id)

    success, message = await session_manager.send_to_window(ctx.window_id, text_to_send)
    if not success:
        await safe_reply(update.message, f"❌ {message}")
        return

    await safe_reply(
        update.message,
        f"📎 File sent to Claude Code: {doc.file_name or filename}",
    )
