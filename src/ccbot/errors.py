"""Typed error helpers for Telegram / tmux hot paths (REF-003).

Centralizes which exceptions are expected/transient vs fatal, and how they
are logged. RetryAfter is always re-raised for rate-limit handling.

With ``PTB_TIMEDELTA=1`` (set in main / conftest), ``RetryAfter.retry_after``
is a ``datetime.timedelta``; ``retry_after_seconds`` normalizes both shapes.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from telegram.error import (
    BadRequest,
    Forbidden,
    NetworkError,
    RetryAfter,
    TelegramError,
    TimedOut,
)

# Expected Telegram failures (edit/send/delete race, network blips, etc.)
TELEGRAM_EXPECTED = (BadRequest, Forbidden, TimedOut, NetworkError, TelegramError)
# Message/topic already gone or no longer editable
TELEGRAM_STALE = (BadRequest, Forbidden)
# Transient transport failures worth retry/backoff at higher layers
TELEGRAM_TRANSIENT = (TimedOut, NetworkError)


def log_exception(
    log: logging.Logger,
    message: str,
    exc: BaseException,
    *,
    level: int = logging.DEBUG,
    **ctx: Any,
) -> None:
    """Log ``exc`` with type name and optional context keys."""
    parts = [f"{k}={v}" for k, v in ctx.items() if v is not None]
    suffix = f" ({', '.join(parts)})" if parts else ""
    log.log(level, "%s: %s: %s%s", message, type(exc).__name__, exc, suffix)


def reraise_retry_after(exc: BaseException) -> None:
    """Re-raise RetryAfter; no-op for other exceptions."""
    if isinstance(exc, RetryAfter):
        raise


def retry_after_seconds(exc: RetryAfter) -> float:
    """Return flood-control wait in seconds from a RetryAfter exception.

    Supports both legacy ``int`` and PTB v22.2+ ``timedelta`` values.
    """
    value = exc.retry_after
    if isinstance(value, timedelta):
        return float(value.total_seconds())
    return float(value)


__all__ = [
    "BadRequest",
    "Forbidden",
    "NetworkError",
    "RetryAfter",
    "TELEGRAM_EXPECTED",
    "TELEGRAM_STALE",
    "TELEGRAM_TRANSIENT",
    "TelegramError",
    "TimedOut",
    "log_exception",
    "reraise_retry_after",
    "retry_after_seconds",
]
