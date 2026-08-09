"""Screenshot callbacks (sr:*, key:*) — extracted from callback_router (ISS-011)."""

from __future__ import annotations

import asyncio
import io
import logging

from telegram import CallbackQuery, InputMediaDocument, Update, User
from telegram.ext import ContextTypes

from ..screenshot import text_to_image
from ..tmux_manager import tmux_manager
from .callback_data import (
    KeyCallback,
    ScreenshotRefreshCallback,
    parse_key,
    parse_screenshot_refresh,
)
from .screenshot_controls import KEY_LABELS, KEYS_SEND_MAP, build_screenshot_keyboard

logger = logging.getLogger(__name__)


async def _handle_screenshot_refresh(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Screenshot: refresh the pane capture in place."""
    ss_refresh = parse_screenshot_refresh(data)
    if not isinstance(ss_refresh, ScreenshotRefreshCallback):
        await query.answer("Invalid data")
        return
    window_id = ss_refresh.window_id
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        await query.answer("Window no longer exists", show_alert=True)
        return

    text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if not text:
        await query.answer("Failed to capture pane", show_alert=True)
        return

    png_bytes = await text_to_image(text, with_ansi=True)
    keyboard = build_screenshot_keyboard(window_id)
    try:
        await query.edit_message_media(
            media=InputMediaDocument(
                media=io.BytesIO(png_bytes), filename="screenshot.png"
            ),
            reply_markup=keyboard,
        )
        await query.answer("Refreshed")
    except Exception as e:
        logger.error(f"Failed to refresh screenshot: {e}")
        await query.answer("Failed to refresh", show_alert=True)


async def _handle_key(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    query: CallbackQuery,
    data: str,
) -> None:
    """Screenshot quick keys: send key to tmux window, refresh screenshot."""
    key_cb = parse_key(data)
    if not isinstance(key_cb, KeyCallback):
        await query.answer("Invalid data")
        return
    key_id = key_cb.key_id
    window_id = key_cb.window_id

    key_info = KEYS_SEND_MAP.get(key_id)
    if not key_info:
        await query.answer("Unknown key")
        return

    tmux_key, enter, literal = key_info
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        await query.answer("Window not found", show_alert=True)
        return

    await tmux_manager.send_keys(w.window_id, tmux_key, enter=enter, literal=literal)
    await query.answer(KEY_LABELS.get(key_id, key_id))

    # Refresh screenshot after key press
    await asyncio.sleep(0.5)
    text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if text:
        png_bytes = await text_to_image(text, with_ansi=True)
        keyboard = build_screenshot_keyboard(window_id)
        try:
            await query.edit_message_media(
                media=InputMediaDocument(
                    media=io.BytesIO(png_bytes),
                    filename="screenshot.png",
                ),
                reply_markup=keyboard,
            )
        except Exception:
            pass  # Screenshot unchanged or message too old
