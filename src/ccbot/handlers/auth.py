"""Shared auth and topic helpers for Telegram handlers.

Provides:
  - is_user_allowed: check ALLOWED_USERS config
  - get_thread_id: extract forum topic thread_id from an update
"""

from __future__ import annotations

from telegram import Update

from ..config import config


def is_user_allowed(user_id: int | None) -> bool:
    return user_id is not None and config.is_user_allowed(user_id)


def get_thread_id(update: Update) -> int | None:
    """Extract thread_id from an update, returning None if not in a named topic."""
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if msg is None:
        return None
    tid = getattr(msg, "message_thread_id", None)
    if tid is None or tid == 1:
        return None
    return tid
