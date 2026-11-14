"""Tests for login_flow — /login sign-in link delivery and code hand-off."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import BadRequest, TimedOut

from ccbot.handlers import login_flow
from ccbot.handlers.login_flow import (
    LOGIN_GONE_POLLS,
    LOGIN_TTL,
    after_code_sent,
    expects_code,
    forget_topic,
    is_login_screen,
    on_pane,
)
from ccbot.terminal_parser import parse_login_url

FIXTURES = Path(__file__).parent.parent / "fixtures" / "panes" / "2.1.311"
URL_PANE = (FIXTURES / "login_url_w110.txt").read_text(encoding="utf-8")
METHOD_PANE = (FIXTURES / "login_method_w110.txt").read_text(encoding="utf-8")
PLAIN_PANE = "random screen\nnothing recognizable here\n"
_LF = "ccbot.handlers.login_flow"


@pytest.fixture(autouse=True)
def _reset_states():
    login_flow._states.clear()
    yield
    login_flow._states.clear()


@pytest.fixture
def bot() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def sender():
    """Mock the outgoing send and the chat id lookup."""
    with (
        patch(f"{_LF}.safe_send", new_callable=AsyncMock) as send,
        patch(f"{_LF}.session_manager") as sm,
    ):
        sm.resolve_chat_id.return_value = 100
        yield send


def _url_pane_with_other_url() -> str:
    return URL_PANE.replace("9d1c250a", "aaaaaaaa")


# ── URL message ──────────────────────────────────────────────────────────


async def test_url_screen_sends_one_message_with_url_button(bot, sender) -> None:
    await on_pane(bot, 1, 42, "@1", URL_PANE)

    sender.assert_awaited_once()
    args = sender.await_args.args
    assert args[0] is bot
    assert args[1] == 100
    assert args[2].startswith("🔐 Claude Code is waiting for sign-in.")
    kwargs = sender.await_args.kwargs
    assert kwargs["message_thread_id"] == 42
    buttons = [b for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert len(buttons) == 1
    assert buttons[0].url == parse_login_url(URL_PANE)
    assert buttons[0].url is not None


async def test_same_url_on_next_poll_sends_nothing_more(bot, sender) -> None:
    await on_pane(bot, 1, 42, "@1", URL_PANE)
    await on_pane(bot, 1, 42, "@1", URL_PANE)
    await on_pane(bot, 1, 42, "@1", URL_PANE)

    sender.assert_awaited_once()


async def test_different_url_sends_new_message(bot, sender) -> None:
    await on_pane(bot, 1, 42, "@1", URL_PANE)
    other = _url_pane_with_other_url()
    assert parse_login_url(other) != parse_login_url(URL_PANE)

    await on_pane(bot, 1, 42, "@1", other)

    assert sender.await_count == 2
    second = sender.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert second.url == parse_login_url(other)


async def test_failed_send_forgets_login_and_next_poll_retries(bot, sender) -> None:
    sender.side_effect = [TimedOut("network"), None]

    await on_pane(bot, 1, 42, "@1", URL_PANE)
    assert (1, 42) not in login_flow._states

    await on_pane(bot, 1, 42, "@1", URL_PANE)
    assert sender.await_count == 2
    assert (1, 42) in login_flow._states


async def test_thread_none_does_nothing(bot, sender) -> None:
    await on_pane(bot, 1, None, "@1", URL_PANE)

    sender.assert_not_awaited()
    assert login_flow._states == {}


# ── closing the sign-in screen ───────────────────────────────────────────


async def test_screen_gone_reports_closed_once_and_forgets(bot, sender) -> None:
    assert LOGIN_GONE_POLLS == 3
    await on_pane(bot, 1, 42, "@1", URL_PANE)
    for _ in range(LOGIN_GONE_POLLS):
        await on_pane(bot, 1, 42, "@1", PLAIN_PANE)

    assert sender.await_count == 2
    closed = sender.await_args.args[2]
    assert "Sign-in screen closed" in closed
    assert (1, 42) not in login_flow._states

    await on_pane(bot, 1, 42, "@1", PLAIN_PANE)
    assert sender.await_count == 2


async def test_login_screen_resets_gone_counter(bot, sender) -> None:
    await on_pane(bot, 1, 42, "@1", URL_PANE)
    await on_pane(bot, 1, 42, "@1", PLAIN_PANE)
    await on_pane(bot, 1, 42, "@1", PLAIN_PANE)
    await on_pane(bot, 1, 42, "@1", URL_PANE)
    await on_pane(bot, 1, 42, "@1", PLAIN_PANE)
    await on_pane(bot, 1, 42, "@1", PLAIN_PANE)

    assert sender.await_count == 1
    assert login_flow._states[(1, 42)].gone_polls == 2


# ── TTL ──────────────────────────────────────────────────────────────────


async def test_expired_login_dropped_without_message(bot, sender) -> None:
    with patch(f"{_LF}.time.monotonic", return_value=1000.0):
        await on_pane(bot, 1, 42, "@1", URL_PANE)
    with patch(f"{_LF}.time.monotonic", return_value=1000.0 + LOGIN_TTL + 1):
        await on_pane(bot, 1, 42, "@1", PLAIN_PANE)

    sender.assert_awaited_once()
    assert (1, 42) not in login_flow._states


# ── screen detection ─────────────────────────────────────────────────────


def test_is_login_screen_url_screen() -> None:
    assert is_login_screen(URL_PANE) is True


def test_is_login_screen_method_picker_is_not_login_screen() -> None:
    assert is_login_screen(METHOD_PANE) is False


def test_is_login_screen_ordinary_pane() -> None:
    assert is_login_screen(PLAIN_PANE) is False


# ── expects_code ─────────────────────────────────────────────────────────


@pytest.fixture
def tmux():
    with patch(f"{_LF}.tmux_manager") as tm:
        tm.capture_pane = AsyncMock(return_value=URL_PANE)
        yield tm


async def _start(bot) -> None:
    with patch(f"{_LF}.safe_send", new_callable=AsyncMock):
        with patch(f"{_LF}.session_manager") as sm:
            sm.resolve_chat_id.return_value = 100
            await on_pane(bot, 1, 42, "@1", URL_PANE)


async def test_expects_code_true_with_state_and_code_prompt(bot, tmux) -> None:
    await _start(bot)
    assert await expects_code(1, 42, "@1") is True
    tmux.capture_pane.assert_awaited_once_with("@1")


async def test_expects_code_false_without_state(bot, tmux) -> None:
    assert await expects_code(1, 42, "@1") is False
    tmux.capture_pane.assert_not_awaited()


async def test_expects_code_false_for_other_window(bot, tmux) -> None:
    await _start(bot)
    assert await expects_code(1, 42, "@2") is False


async def test_expects_code_false_without_code_prompt(bot, tmux) -> None:
    await _start(bot)
    tmux.capture_pane.return_value = METHOD_PANE
    assert await expects_code(1, 42, "@1") is False


async def test_expects_code_false_when_capture_raises(bot, tmux) -> None:
    await _start(bot)
    tmux.capture_pane.side_effect = RuntimeError("tmux gone")
    assert await expects_code(1, 42, "@1") is False


async def test_expects_code_false_after_ttl(bot, tmux) -> None:
    with patch(f"{_LF}.time.monotonic", return_value=1000.0):
        await _start(bot)
    with patch(f"{_LF}.time.monotonic", return_value=1000.0 + LOGIN_TTL + 1):
        assert await expects_code(1, 42, "@1") is False


# ── after_code_sent ──────────────────────────────────────────────────────


async def test_after_code_sent_deletes_and_confirms(bot, sender) -> None:
    message = MagicMock()
    message.delete = AsyncMock()

    await after_code_sent(bot, message, 1, 42)

    message.delete.assert_awaited_once()
    sender.assert_awaited_once()
    assert "Your message was deleted" in sender.await_args.args[2]
    assert sender.await_args.kwargs["message_thread_id"] == 42


async def test_after_code_sent_delete_fails_still_confirms(bot, sender) -> None:
    message = MagicMock()
    message.delete = AsyncMock(side_effect=BadRequest("Message can't be deleted"))

    await after_code_sent(bot, message, 1, 42)

    sender.assert_awaited_once()
    assert "delete it yourself" in sender.await_args.args[2]


# ── log privacy ──────────────────────────────────────────────────────────


async def test_logs_never_contain_url_or_code(bot, tmux, caplog) -> None:
    code = "XK-7741-secret-code"
    url = parse_login_url(URL_PANE)
    assert url is not None
    caplog.set_level(logging.DEBUG)

    with (
        patch(f"{_LF}.safe_send", new_callable=AsyncMock) as send,
        patch(f"{_LF}.session_manager") as sm,
    ):
        sm.resolve_chat_id.return_value = 100
        # A failing send whose error text carries the URL must not reach the log
        send.side_effect = RuntimeError(f"boom {url}")
        await on_pane(bot, 1, 42, "@1", URL_PANE)
        send.side_effect = None
        await on_pane(bot, 1, 42, "@1", URL_PANE)

    message = MagicMock()
    message.delete = AsyncMock(side_effect=BadRequest(f"cannot delete {code}"))
    with patch(f"{_LF}.safe_send", new_callable=AsyncMock):
        with patch(f"{_LF}.session_manager") as sm:
            sm.resolve_chat_id.return_value = 100
            await after_code_sent(bot, message, 1, 42)

    assert caplog.records
    assert "oauth/authorize" not in caplog.text
    assert url not in caplog.text
    assert code not in caplog.text


def test_forget_topic_drops_state(bot) -> None:
    login_flow._states[(1, 42)] = login_flow.LoginState(
        window_id="@1", url="https://claude.com/x", sent_at=0.0
    )
    forget_topic(1, 42)
    assert (1, 42) not in login_flow._states
    forget_topic(1, None)  # no state: no error
