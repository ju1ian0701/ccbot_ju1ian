"""Shared stale-topic guard for picker/browser callback flows (ISS-011).

Also hosts ``RouteHandler`` — the common signature of all routed
callback sub-handlers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from telegram import CallbackQuery, Update, User
from telegram.ext import ContextTypes

from ..session_guard import get_thread_id

# Handler signature for all routed callbacks: (update, context, user, query, data).
RouteHandler = Callable[
    [Update, ContextTypes.DEFAULT_TYPE, User, CallbackQuery, str],
    Awaitable[None],
]


@dataclass(frozen=True, slots=True)
class _TopicCheck:
    """Result of the stale-topic guard for picker/browser flows."""

    ok: bool
    pending_tid: int | None


async def _check_same_topic(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: CallbackQuery,
    *,
    recover: bool = False,
    stale_label: str = "picker",
    on_stale: Callable[[], None] | None = None,
) -> _TopicCheck:
    """Single stale-topic guard (ISS-004): pending flow vs callback topic.

    The callback must come from the same topic that started the pending
    flow (``_pending_thread_id`` in user_data). With ``recover``, a missing
    marker is recovered from the callback's own message context (e.g. after
    it was cleared by a message in another topic). On mismatch ``on_stale``
    runs first (state cleanup ordering), then the query is answered with a
    stale alert and ``ok=False`` is returned.
    """
    pending_tid = (
        context.user_data.get("_pending_thread_id") if context.user_data else None
    )
    if pending_tid is None and recover:
        pending_tid = get_thread_id(update)
    if pending_tid is not None and get_thread_id(update) != pending_tid:
        if on_stale is not None:
            on_stale()
        await query.answer(f"Stale {stale_label} (topic mismatch)", show_alert=True)
        return _TopicCheck(ok=False, pending_tid=None)
    return _TopicCheck(ok=True, pending_tid=pending_tid)


def _clear_pending_thread(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Drop the pending topic flow markers from user_data."""
    if context.user_data is not None:
        context.user_data.pop("_pending_thread_id", None)
        context.user_data.pop("_pending_thread_text", None)
