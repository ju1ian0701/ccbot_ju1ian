"""Interactive UI callbacks (aq:*) — extracted from callback_router (ISS-011)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from telegram import CallbackQuery, Update, User
from telegram.ext import ContextTypes

from ..session_guard import get_thread_id
from ..tmux_manager import tmux_manager
from .callback_data import (
    CB_ASK_DOWN,
    CB_ASK_ENTER,
    CB_ASK_ESC,
    CB_ASK_LEFT,
    CB_ASK_REFRESH,
    CB_ASK_RIGHT,
    CB_ASK_SPACE,
    CB_ASK_TAB,
    CB_ASK_UP,
    AskCallback,
    parse_ask,
)
from .interactive_ui import clear_interactive_msg, handle_interactive_ui


@dataclass(frozen=True, slots=True)
class _AskAction:
    """Behavior of one interactive-UI (aq:*) action."""

    tmux_key: str | None  # None → refresh display only (no key press)
    answer: str | None  # None → silent query.answer()
    clear: bool = False  # True → clear interactive message instead of UI refresh


_ASK_ACTIONS: dict[str, _AskAction] = {
    CB_ASK_UP: _AskAction("Up", None),
    CB_ASK_DOWN: _AskAction("Down", None),
    CB_ASK_LEFT: _AskAction("Left", None),
    CB_ASK_RIGHT: _AskAction("Right", None),
    CB_ASK_ESC: _AskAction("Escape", "⎋ Esc", clear=True),
    CB_ASK_ENTER: _AskAction("Enter", "⏎ Enter"),
    CB_ASK_SPACE: _AskAction("Space", "␣ Space"),
    CB_ASK_TAB: _AskAction("Tab", "⇥ Tab"),
    CB_ASK_REFRESH: _AskAction(None, "🔄"),
}


async def _handle_ask(
    prefix: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Interactive UI: single table-driven body for all aq:* actions."""
    ask_cb = parse_ask(data, prefix)
    if not isinstance(ask_cb, AskCallback):
        await query.answer("Invalid data")
        return
    action = _ASK_ACTIONS[prefix]
    window_id = ask_cb.window_id
    thread_id = get_thread_id(update)
    if action.tmux_key is None:
        # Refresh display: no key press, no window lookup.
        await handle_interactive_ui(context.bot, user.id, window_id, thread_id)
    else:
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, action.tmux_key, enter=False, literal=False
            )
            if action.clear:
                await clear_interactive_msg(user.id, context.bot, thread_id)
            else:
                await asyncio.sleep(0.5)
                await handle_interactive_ui(context.bot, user.id, window_id, thread_id)
    await query.answer(action.answer)
