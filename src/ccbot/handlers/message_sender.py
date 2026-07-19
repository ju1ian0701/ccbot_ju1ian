"""Safe message sending helpers with MarkdownV2 fallback.

Provides utility functions for sending Telegram messages with automatic
format conversion and fallback to plain text on failure.

Uses telegramify-markdown for MarkdownV2 formatting.

All Markdown→plain fallbacks go through a single ``MessageFormatter``
(``_run_markdown_fallback``) so RetryAfter handling stays consistent.

Functions:
  - send_with_fallback: Send with formatting → plain text fallback
  - edit_with_fallback: Edit with formatting → plain text fallback
  - send_photo: Photo sending (single or media group)
  - safe_reply: Reply with formatting, fallback to plain text
  - safe_edit: Edit message with formatting, fallback to plain text
  - safe_send: Send message with formatting, fallback to plain text

Rate limiting is handled globally by AIORateLimiter on the Application.
RetryAfter exceptions are re-raised so callers (queue worker) can handle them.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from telegram import Bot, InputMediaPhoto, LinkPreviewOptions, Message
from telegram.error import RetryAfter, TelegramError

from ..errors import log_exception
from ..markdown_v2 import convert_markdown
from ..transcript_parser import TranscriptParser

logger = logging.getLogger(__name__)

T = TypeVar("T")


def strip_sentinels(text: str) -> str:
    """Strip expandable quote sentinel markers for plain text fallback."""
    for s in (
        TranscriptParser.EXPANDABLE_QUOTE_START,
        TranscriptParser.EXPANDABLE_QUOTE_END,
    ):
        text = text.replace(s, "")
    return text


PARSE_MODE = "MarkdownV2"

# Disable link previews in all messages to reduce visual noise
NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)


class MessageFormatter:
    """Single place for MarkdownV2 formatting and plain-text fallback.

    All send/edit/reply helpers use this class so parse_mode, sentinel
    stripping, and RetryAfter re-raise behavior stay identical.
    """

    parse_mode: str = PARSE_MODE

    @staticmethod
    def to_markdown_v2(text: str) -> str:
        """Convert source markdown to Telegram MarkdownV2."""
        return convert_markdown(text)

    @staticmethod
    def to_plain(text: str) -> str:
        """Plain-text fallback body (sentinels stripped)."""
        return strip_sentinels(text)


# Module-level singleton — acceptance: single MessageFormatter used
formatter = MessageFormatter()


async def _run_markdown_fallback(
    text: str,
    markdown_call: Callable[[str], Awaitable[T]],
    plain_call: Callable[[str], Awaitable[T]],
    *,
    on_failure: Callable[[BaseException], T | None] | None = None,
    reraise_final: bool = False,
    failure_level: int = logging.ERROR,
    failure_message: str = "Markdown/plain fallback failed",
    **log_ctx: Any,
) -> T | None:
    """Try MarkdownV2 call, then plain text. Always re-raise RetryAfter.

    Args:
        text: Source message text (pre-conversion).
        markdown_call: Awaitable factory receiving MarkdownV2 text.
        plain_call: Awaitable factory receiving plain text.
        on_failure: Optional mapper when both attempts fail (return value).
        reraise_final: If True, re-raise the plain-path TelegramError.
        failure_level: Log level when both fail and not re-raised.
        failure_message: Log prefix when both fail.
        **log_ctx: Extra structured fields for failure logs.
    """
    try:
        return await markdown_call(formatter.to_markdown_v2(text))
    except RetryAfter:
        raise
    except TelegramError:
        try:
            return await plain_call(formatter.to_plain(text))
        except RetryAfter:
            raise
        except TelegramError as e:
            if reraise_final:
                logger.log(
                    failure_level,
                    "%s: %s: %s",
                    failure_message,
                    type(e).__name__,
                    e,
                )
                raise
            if log_ctx:
                ctx = " ".join(f"{k}={v}" for k, v in log_ctx.items() if v is not None)
                logger.log(
                    failure_level,
                    "%s: %s: %s (%s)",
                    failure_message,
                    type(e).__name__,
                    e,
                    ctx,
                )
            else:
                logger.log(
                    failure_level,
                    "%s: %s: %s",
                    failure_message,
                    type(e).__name__,
                    e,
                )
            if on_failure is not None:
                return on_failure(e)
            return None


# Back-compat alias used by older helpers / tests
_ensure_formatted = formatter.to_markdown_v2


async def send_with_fallback(
    bot: Bot,
    chat_id: int,
    text: str,
    **kwargs: Any,
) -> Message | None:
    """Send message with MarkdownV2, falling back to plain text on failure.

    Returns the sent Message on success, None on failure.
    RetryAfter is re-raised for caller handling.
    """
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)

    async def _md(body: str) -> Message:
        return await bot.send_message(
            chat_id=chat_id,
            text=body,
            parse_mode=formatter.parse_mode,
            **kwargs,
        )
    except RetryAfter:
        raise
    except TelegramError:
        try:
            return await bot.send_message(
                chat_id=chat_id, text=strip_sentinels(text), **kwargs
            )
        except RetryAfter:
            raise
        except TelegramError as e:
            log_exception(
                logger,
                "Failed to send message",
                e,
                level=logging.ERROR,
                chat_id=chat_id,
            )
            return None


async def edit_with_fallback(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    **kwargs: Any,
) -> bool:
    """Edit message with MarkdownV2, falling back to plain text on failure.

    Returns True on success, False if both attempts fail.
    RetryAfter is re-raised for caller handling.
    """
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=_ensure_formatted(text),
            parse_mode=PARSE_MODE,
            **kwargs,
        )
        return True
    except RetryAfter:
        raise
    except TelegramError:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=strip_sentinels(text),
                **kwargs,
            )
            return True
        except RetryAfter:
            raise
        except TelegramError as e:
            log_exception(
                logger,
                "Failed to edit message",
                e,
                level=logging.DEBUG,
                chat_id=chat_id,
                message_id=message_id,
            )
            return False


async def send_photo(
    bot: Bot,
    chat_id: int,
    image_data: list[tuple[str, bytes]],
    **kwargs: Any,
) -> None:
    """Send photo(s) to chat. Sends as media group if multiple images.

    Rate limiting is handled globally by AIORateLimiter on the Application.

    Args:
        bot: Telegram Bot instance
        chat_id: Target chat ID
        image_data: List of (media_type, raw_bytes) tuples
        **kwargs: Extra kwargs passed to send_photo/send_media_group
    """
    if not image_data:
        return
    try:
        if len(image_data) == 1:
            _media_type, raw_bytes = image_data[0]
            await bot.send_photo(
                chat_id=chat_id,
                photo=io.BytesIO(raw_bytes),
                **kwargs,
            )
        else:
            media = [
                InputMediaPhoto(media=io.BytesIO(raw_bytes))
                for _media_type, raw_bytes in image_data
            ]
            await bot.send_media_group(
                chat_id=chat_id,
                media=media,
                **kwargs,
            )
    except RetryAfter:
        raise
    except TelegramError as e:
        log_exception(
            logger,
            "Failed to send photo",
            e,
            level=logging.ERROR,
            chat_id=chat_id,
        )


async def safe_reply(message: Message, text: str, **kwargs: Any) -> Message:
    """Reply with formatting, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)

    async def _md(body: str) -> Message:
        return await message.reply_text(
            body,
            parse_mode=formatter.parse_mode,
            **kwargs,
        )
    except RetryAfter:
        raise
    except TelegramError:
        try:
            return await message.reply_text(strip_sentinels(text), **kwargs)
        except RetryAfter:
            raise
        except TelegramError as e:
            log_exception(logger, "Failed to reply", e, level=logging.ERROR)
            raise


async def safe_edit(target: Any, text: str, **kwargs: Any) -> None:
    """Edit message with formatting, falling back to plain text on failure."""
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)

    async def _md(body: str) -> bool:
        await target.edit_message_text(
            body,
            parse_mode=formatter.parse_mode,
            **kwargs,
        )
    except RetryAfter:
        raise
    except TelegramError:
        try:
            await target.edit_message_text(strip_sentinels(text), **kwargs)
        except RetryAfter:
            raise
        except TelegramError as e:
            log_exception(logger, "Failed to edit message", e, level=logging.ERROR)


async def safe_send(
    bot: Bot,
    chat_id: int,
    text: str,
    message_thread_id: int | None = None,
    **kwargs: Any,
) -> None:
    """Send message with formatting, falling back to plain text on failure."""
    if message_thread_id is not None:
        kwargs.setdefault("message_thread_id", message_thread_id)
    await send_with_fallback(bot, chat_id, text, **kwargs)
