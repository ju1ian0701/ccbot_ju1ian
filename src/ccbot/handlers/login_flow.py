"""Claude Code /login flow: deliver the sign-in link to Telegram and take the code back.

Called once per status poll for every bound window with the captured pane:
  - when the /login URL screen shows a sign-in URL, sends it to the topic as an
    inline "Open sign-in page" button (once per distinct URL)
  - reports "sign-in screen closed" after the login screen has been gone for
    several polls, and forgets the login after LOGIN_TTL seconds
  - tells text_handler when the window waits for a pasted code, so the user's
    next message is passed to Claude Code as that code and deleted from the chat

Key components:
  - LoginState: per-topic login memory (window, URL, send time, closed-poll count)
  - on_pane: per-poll state machine, called from status_polling
  - expects_code / after_code_sent: used by text_handler for the code message
  - forget_topic: drops a topic's login state when its binding goes away

Privacy: neither the sign-in URL nor the code is ever written to the log.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..session import session_manager
from ..terminal_parser import is_login_code_prompt, parse_login_url
from ..tmux_manager import tmux_manager
from .message_sender import safe_send

logger = logging.getLogger(__name__)

LOGIN_TTL = 600.0  # seconds a login stays "in progress" after the URL was sent
LOGIN_GONE_POLLS = (
    3  # consecutive polls without the login screen before it counts as closed
)

_SIGN_IN_TEXT = (
    "🔐 Claude Code is waiting for sign-in.\n"
    "1. Open the sign-in page with the button below.\n"
    "2. Sign in and copy the code you are given.\n"
    "3. Send the code here as a message — it will be passed to Claude Code "
    "and your message deleted."
)
_CLOSED_TEXT = "🔐 Sign-in screen closed. Use /screenshot to check the result."
_CODE_PASSED_TEXT = "🔑 Code passed to Claude Code. Your message was deleted."
_CODE_PASSED_NOT_DELETED_TEXT = (
    "🔑 Code passed to Claude Code. I could not delete your message — "
    "delete it yourself."
)


@dataclass
class LoginState:
    """One topic's sign-in in progress."""

    window_id: str
    url: str
    sent_at: float  # time.monotonic()
    gone_polls: int = 0


# (user_id, thread_id_or_0) -> LoginState
_states: dict[tuple[int, int], LoginState] = {}


def _key(user_id: int, thread_id: int | None) -> tuple[int, int]:
    return (user_id, thread_id or 0)


def _is_expired(state: LoginState) -> bool:
    return time.monotonic() - state.sent_at > LOGIN_TTL


def is_login_screen(pane_text: str) -> bool:
    """True if the pane shows the /login sign-in URL screen or code prompt."""
    return parse_login_url(pane_text) is not None or is_login_code_prompt(pane_text)


async def on_pane(
    bot: Bot,
    user_id: int,
    thread_id: int | None,
    window_id: str,
    pane_text: str,
) -> None:
    """Advance the topic's login state with the latest pane of its window."""
    if thread_id is None:
        return

    key = _key(user_id, thread_id)
    state = _states.get(key)
    if state is not None and (_is_expired(state) or state.window_id != window_id):
        _states.pop(key, None)
        state = None

    url = parse_login_url(pane_text)
    if url is not None and (state is None or state.url != url):
        await _start_login(bot, user_id, thread_id, window_id, url)
        return

    if state is None:
        return

    if is_login_screen(pane_text):
        state.gone_polls = 0
        return

    state.gone_polls += 1
    if state.gone_polls < LOGIN_GONE_POLLS:
        return

    _states.pop(key, None)
    try:
        chat_id = session_manager.resolve_chat_id(user_id, thread_id)
        await safe_send(bot, chat_id, _CLOSED_TEXT, message_thread_id=thread_id)
    except Exception as e:
        logger.warning("Could not report closed sign-in screen: %s", type(e).__name__)


async def _start_login(
    bot: Bot,
    user_id: int,
    thread_id: int,
    window_id: str,
    url: str,
) -> None:
    """Remember a new sign-in and send its link to the topic."""
    key = _key(user_id, thread_id)
    _states[key] = LoginState(window_id=window_id, url=url, sent_at=time.monotonic())
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔐 Open sign-in page", url=url)]]
    )
    try:
        chat_id = session_manager.resolve_chat_id(user_id, thread_id)
        await safe_send(
            bot,
            chat_id,
            _SIGN_IN_TEXT,
            message_thread_id=thread_id,
            reply_markup=keyboard,
        )
    except Exception as e:
        # Forget the login so the next poll retries the send
        _states.pop(key, None)
        logger.warning("Could not send sign-in link: %s", type(e).__name__)


async def expects_code(user_id: int, thread_id: int | None, window_id: str) -> bool:
    """True if a live login in this topic waits for a pasted code in this window."""
    try:
        state = _states.get(_key(user_id, thread_id))
        if state is None or state.window_id != window_id or _is_expired(state):
            return False
        pane = await tmux_manager.capture_pane(window_id)
        return is_login_code_prompt(pane or "")
    except Exception:
        return False


async def after_code_sent(
    bot: Bot,
    message: Message,
    user_id: int,
    thread_id: int | None,
) -> None:
    """Delete the user's code message and confirm the code was passed on. Never raises."""
    try:
        try:
            await message.delete()
            text = _CODE_PASSED_TEXT
        except Exception as e:
            logger.warning(
                "Could not delete sign-in code message: %s", type(e).__name__
            )
            text = _CODE_PASSED_NOT_DELETED_TEXT

        chat_id = session_manager.resolve_chat_id(user_id, thread_id)
        await safe_send(bot, chat_id, text, message_thread_id=thread_id)
    except Exception as e:
        logger.warning("Could not confirm code delivery: %s", type(e).__name__)


def forget_topic(user_id: int, thread_id: int | None) -> None:
    """Drop a topic's login state (its binding was removed)."""
    _states.pop(_key(user_id, thread_id), None)
