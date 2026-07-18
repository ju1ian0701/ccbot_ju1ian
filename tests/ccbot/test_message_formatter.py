"""Tests for MessageFormatter / unified markdown fallback (REF-008)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest, NetworkError, RetryAfter

from ccbot.handlers import message_sender as ms
from ccbot.handlers.message_sender import MessageFormatter, formatter
from ccbot.transcript_parser import TranscriptParser


def test_single_message_formatter_singleton() -> None:
    assert isinstance(formatter, MessageFormatter)
    assert ms.formatter is formatter
    assert formatter.parse_mode == "MarkdownV2"
    sample = f"x{TranscriptParser.EXPANDABLE_QUOTE_START}y"
    assert formatter.to_plain(sample) == ms.strip_sentinels(sample)
    assert TranscriptParser.EXPANDABLE_QUOTE_START not in formatter.to_plain(sample)


def test_formatter_to_markdown_v2_uses_convert() -> None:
    # Plain word should still produce a string (may be escaped)
    out = formatter.to_markdown_v2("hello")
    assert isinstance(out, str)
    assert len(out) > 0


@pytest.mark.asyncio
async def test_send_with_fallback_uses_formatter_path() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(
        side_effect=[BadRequest("can't parse entities"), MagicMock(name="msg")]
    )
    result = await ms.send_with_fallback(bot, 1, "**hi**")
    assert result is not None
    assert bot.send_message.await_count == 2
    first_kwargs = bot.send_message.await_args_list[0].kwargs
    second_kwargs = bot.send_message.await_args_list[1].kwargs
    assert first_kwargs.get("parse_mode") == formatter.parse_mode
    assert second_kwargs.get("parse_mode") is None


@pytest.mark.asyncio
async def test_send_with_fallback_reraises_retry_after() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=RetryAfter(timedelta(seconds=3)))
    with pytest.raises(RetryAfter):
        await ms.send_with_fallback(bot, 1, "x")


@pytest.mark.asyncio
async def test_send_with_fallback_none_when_both_fail() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(
        side_effect=[BadRequest("bad md"), NetworkError("down")]
    )
    assert await ms.send_with_fallback(bot, 1, "x") is None


@pytest.mark.asyncio
async def test_edit_with_fallback_markdown_then_plain() -> None:
    bot = MagicMock()
    bot.edit_message_text = AsyncMock(
        side_effect=[BadRequest("can't parse entities"), None]
    )
    ok = await ms.edit_with_fallback(bot, 1, 42, "**x**")
    assert ok is True
    assert bot.edit_message_text.await_count == 2
    assert bot.edit_message_text.await_args_list[0].kwargs.get("parse_mode") == (
        formatter.parse_mode
    )


@pytest.mark.asyncio
async def test_edit_with_fallback_false_when_both_fail() -> None:
    bot = MagicMock()
    bot.edit_message_text = AsyncMock(
        side_effect=[BadRequest("bad"), NetworkError("down")]
    )
    assert await ms.edit_with_fallback(bot, 1, 42, "hi") is False


@pytest.mark.asyncio
async def test_safe_send_delegates_to_send_with_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, object] = {}

    async def fake_send(bot: object, chat_id: int, text: str, **kwargs: object) -> None:
        called["chat_id"] = chat_id
        called["text"] = text
        called["thread"] = kwargs.get("message_thread_id")
        called["called"] = True

    monkeypatch.setattr(ms, "send_with_fallback", fake_send)
    await ms.safe_send(MagicMock(), 7, "hello", message_thread_id=99)
    assert called == {
        "chat_id": 7,
        "text": "hello",
        "thread": 99,
        "called": True,
    }


@pytest.mark.asyncio
async def test_safe_reply_reraises_retry_after_on_plain() -> None:
    message = MagicMock()
    message.reply_text = AsyncMock(
        side_effect=[BadRequest("bad md"), RetryAfter(timedelta(seconds=2))]
    )
    with pytest.raises(RetryAfter):
        await ms.safe_reply(message, "hello")


@pytest.mark.asyncio
async def test_run_markdown_fallback_shared_by_safe_edit() -> None:
    target = MagicMock()
    target.edit_message_text = AsyncMock(side_effect=[BadRequest("bad"), None])
    await ms.safe_edit(target, "**z**")
    assert target.edit_message_text.await_count == 2
    assert target.edit_message_text.await_args_list[0].kwargs.get("parse_mode") == (
        formatter.parse_mode
    )
