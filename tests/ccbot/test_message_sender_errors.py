"""Tests for typed Telegram error handling in message_sender (REF-003)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest, NetworkError, RetryAfter

from ccbot.errors import retry_after_seconds
from ccbot.handlers import message_sender as ms


@pytest.mark.asyncio
async def test_send_with_fallback_retries_plain_on_bad_request() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(
        side_effect=[BadRequest("can't parse entities"), MagicMock()]
    )
    result = await ms.send_with_fallback(bot, 1, "**hi**")
    assert result is not None
    assert bot.send_message.await_count == 2
    second_kwargs = bot.send_message.await_args_list[1].kwargs
    assert second_kwargs.get("parse_mode") is None
    assert "parse_mode" not in second_kwargs or second_kwargs["parse_mode"] is None


@pytest.mark.asyncio
async def test_send_with_fallback_reraises_retry_after() -> None:
    bot = MagicMock()
    wait = timedelta(seconds=5)
    bot.send_message = AsyncMock(side_effect=RetryAfter(wait))
    with pytest.raises(RetryAfter) as exc_info:
        await ms.send_with_fallback(bot, 1, "x")
    assert retry_after_seconds(exc_info.value) == 5.0


@pytest.mark.asyncio
async def test_send_with_fallback_returns_none_when_both_fail() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(
        side_effect=[BadRequest("bad md"), NetworkError("down")]
    )
    result = await ms.send_with_fallback(bot, 1, "x")
    assert result is None
    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_safe_reply_reraises_retry_after_on_plain() -> None:
    message = MagicMock()
    message.chat = MagicMock()
    wait = timedelta(seconds=2)
    message.reply_text = AsyncMock(side_effect=[BadRequest("bad md"), RetryAfter(wait)])
    with pytest.raises(RetryAfter) as exc_info:
        await ms.safe_reply(message, "hello")
    assert retry_after_seconds(exc_info.value) == 2.0


@pytest.mark.asyncio
async def test_edit_with_fallback_markdown_then_plain() -> None:
    bot = MagicMock()
    bot.edit_message_text = AsyncMock(
        side_effect=[BadRequest("can't parse entities"), None]
    )
    ok = await ms.edit_with_fallback(bot, 1, 42, "**x**")
    assert ok is True
    assert bot.edit_message_text.await_count == 2
    second = bot.edit_message_text.await_args_list[1].kwargs
    assert second.get("parse_mode") is None


@pytest.mark.asyncio
async def test_edit_with_fallback_false_when_both_fail() -> None:
    bot = MagicMock()
    bot.edit_message_text = AsyncMock(
        side_effect=[BadRequest("bad"), NetworkError("down")]
    )
    ok = await ms.edit_with_fallback(bot, 1, 42, "hi")
    assert ok is False


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
    bot = MagicMock()
    await ms.safe_send(bot, 7, "hello", message_thread_id=99)
    assert called.get("called") is True
    assert called["chat_id"] == 7
    assert called["text"] == "hello"
    assert called["thread"] == 99


def test_retry_after_seconds_accepts_int_and_timedelta() -> None:
    """Helper stays compatible if PTB_TIMEDELTA is off or on."""
    from unittest.mock import MagicMock as MM

    td_exc = MM(spec=RetryAfter)
    td_exc.retry_after = timedelta(seconds=3)
    assert retry_after_seconds(td_exc) == 3.0

    int_exc = MM(spec=RetryAfter)
    int_exc.retry_after = 7
    assert retry_after_seconds(int_exc) == 7.0
