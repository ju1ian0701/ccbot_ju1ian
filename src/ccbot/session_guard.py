"""Shared Telegram auth + topic→window session resolution for bot handlers.

Extracts the repeated pattern from bot.py:
  authorize user → resolve thread → resolve window_id → find live tmux window.

Key types:
  - SessionContext: authorized user + topic + window binding for a request
  - require_user: auth only
  - require_bound_window_id: auth + window_id (no live tmux check)
  - require_session: full path including live TmuxWindow
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from telegram import Update, User
from telegram.error import RetryAfter, TelegramError

from .config import config
from .handlers.message_sender import safe_reply
from .session import session_manager
from .tmux_manager import TmuxWindow, tmux_manager

logger = logging.getLogger(__name__)

DEFAULT_UNAUTHORIZED = "You are not authorized to use this bot."
DEFAULT_NO_SESSION = "❌ No session bound to this topic."
DEFAULT_MISSING_WINDOW = "❌ Window '{display}' no longer exists."


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Resolved session binding for a single Telegram update."""

    user: User
    thread_id: int | None
    window_id: str
    window: TmuxWindow


def is_user_allowed(user_id: int | None) -> bool:
    """Return True if user_id is present and allowed by config."""
    return user_id is not None and config.is_user_allowed(user_id)


def get_thread_id(update: Update) -> int | None:
    """Extract thread_id from an update; None if not in a named topic.

    Topic id ``1`` (General) is treated as non-topic (topic-only architecture).
    """
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if msg is None:
        return None
    thread_id = getattr(msg, "message_thread_id", None)
    if thread_id is None or thread_id == 1:
        return None
    return thread_id


async def require_user(
    update: Update,
    *,
    reply_unauthorized: bool = True,
    unauthorized_text: str = DEFAULT_UNAUTHORIZED,
    callback_unauthorized_text: str = "Not authorized",
) -> User | None:
    """Authorize the effective user or notify and return None."""
    user = update.effective_user
    if user and is_user_allowed(user.id):
        return user

    if not reply_unauthorized:
        return None

    if update.callback_query is not None:
        try:
            await update.callback_query.answer(callback_unauthorized_text)
        except RetryAfter:
            raise
        except TelegramError as exc:
            logger.warning("callback answer unauthorized failed: %s", exc)
        return None

    if update.message is not None:
        try:
            await safe_reply(update.message, unauthorized_text)
        except RetryAfter:
            raise
        except TelegramError as exc:
            logger.warning("safe_reply unauthorized failed: %s", exc)
    return None


async def require_bound_window_id(
    update: Update,
    *,
    reply_unauthorized: bool = False,
    require_message: bool = True,
    use_resolve: bool = True,
    no_session_text: str = DEFAULT_NO_SESSION,
    user: User | None = None,
) -> tuple[User, int | None, str] | None:
    """Auth + resolve window_id for the topic (no live tmux window required)."""
    if user is None:
        user = await require_user(update, reply_unauthorized=reply_unauthorized)
    if user is None:
        return None
    if require_message and update.message is None:
        return None

    thread_id = get_thread_id(update)
    if use_resolve:
        window_id = session_manager.resolve_window_for_thread(user.id, thread_id)
    else:
        if thread_id is None:
            window_id = None
        else:
            window_id = session_manager.get_window_for_thread(user.id, thread_id)

    if not window_id:
        if update.message is not None and no_session_text:
            try:
                await safe_reply(update.message, no_session_text)
            except RetryAfter:
                raise
            except TelegramError as exc:
                logger.warning("safe_reply no_session failed: %s", exc)
        return None

    return user, thread_id, window_id


async def require_session(
    update: Update,
    *,
    reply_unauthorized: bool = False,
    require_message: bool = True,
    use_resolve: bool = True,
    require_named_topic: bool = False,
    named_topic_text: str = (
        "❌ Please use a named topic. Create a new topic to start a session."
    ),
    no_session_text: str = DEFAULT_NO_SESSION,
    missing_window_text: str = DEFAULT_MISSING_WINDOW,
    unbind_if_missing: bool = False,
    user: User | None = None,
) -> SessionContext | None:
    """Full auth + window resolve + live tmux window lookup.

    Returns SessionContext on success. On failure, sends the appropriate
    user-facing message (when configured) and returns None.
    """
    if user is None:
        user = await require_user(update, reply_unauthorized=reply_unauthorized)
    if user is None:
        return None
    if require_message and update.message is None:
        return None

    thread_id = get_thread_id(update)

    if require_named_topic and thread_id is None:
        if update.message is not None:
            try:
                await safe_reply(update.message, named_topic_text)
            except RetryAfter:
                raise
            except TelegramError as exc:
                logger.warning("safe_reply named_topic failed: %s", exc)
        return None

    if use_resolve:
        window_id = session_manager.resolve_window_for_thread(user.id, thread_id)
    else:
        if thread_id is None:
            window_id = None
        else:
            window_id = session_manager.get_window_for_thread(user.id, thread_id)

    if not window_id:
        if update.message is not None and no_session_text:
            try:
                await safe_reply(update.message, no_session_text)
            except RetryAfter:
                raise
            except TelegramError as exc:
                logger.warning("safe_reply no_session failed: %s", exc)
        return None

    window = await tmux_manager.find_window_by_id(window_id)
    if not window:
        display = session_manager.get_display_name(window_id)
        if unbind_if_missing and thread_id is not None:
            session_manager.unbind_thread(user.id, thread_id)
        if update.message is not None and missing_window_text:
            text = missing_window_text.format(display=display, window_id=window_id)
            try:
                await safe_reply(update.message, text)
            except RetryAfter:
                raise
            except TelegramError as exc:
                logger.warning("safe_reply missing_window failed: %s", exc)
        return None

    return SessionContext(
        user=user,
        thread_id=thread_id,
        window_id=window_id,
        window=window,
    )
