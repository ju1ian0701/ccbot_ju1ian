"""Unit tests for session_guard (REF-001 auth + session resolution helper)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.session_guard import (
    SessionContext,
    get_thread_id,
    is_user_allowed,
    require_bound_window_id,
    require_session,
    require_user,
)
from ccbot.tmux_manager import TmuxWindow


def _user(uid: int = 42) -> MagicMock:
    u = MagicMock()
    u.id = uid
    return u


def _message(*, thread_id: int | None = 7) -> MagicMock:
    msg = MagicMock()
    msg.message_thread_id = thread_id
    return msg


def _update(
    *,
    user: MagicMock | None = None,
    message: MagicMock | None = None,
    callback_query: MagicMock | None = None,
) -> MagicMock:
    upd = MagicMock()
    upd.effective_user = user
    upd.message = message
    upd.callback_query = callback_query
    return upd


class TestIsUserAllowed:
    def test_none_disallowed(self) -> None:
        assert is_user_allowed(None) is False

    def test_delegates_to_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "ccbot.session_guard.config.is_user_allowed",
            lambda uid: uid == 99,
        )
        assert is_user_allowed(99) is True
        assert is_user_allowed(1) is False


class TestGetThreadId:
    def test_from_message(self) -> None:
        upd = _update(message=_message(thread_id=12))
        assert get_thread_id(upd) == 12

    def test_general_topic_is_none(self) -> None:
        upd = _update(message=_message(thread_id=1))
        assert get_thread_id(upd) is None

    def test_from_callback(self) -> None:
        cq = MagicMock()
        cq.message = _message(thread_id=5)
        upd = _update(message=None, callback_query=cq)
        assert get_thread_id(upd) == 5


@pytest.mark.asyncio
async def test_require_user_unauthorized_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ccbot.session_guard.config.is_user_allowed", lambda _u: False)
    msg = _message()
    upd = _update(user=_user(1), message=msg)
    with patch("ccbot.session_guard.safe_reply", new_callable=AsyncMock) as reply:
        result = await require_user(upd, reply_unauthorized=True)
        assert result is None
        reply.assert_awaited_once()
        assert "not authorized" in reply.await_args.args[1].lower()


@pytest.mark.asyncio
async def test_require_user_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ccbot.session_guard.config.is_user_allowed", lambda _u: True)
    user = _user(7)
    upd = _update(user=user, message=_message())
    result = await require_user(upd)
    assert result is user


@pytest.mark.asyncio
async def test_require_session_authorized_bound_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ccbot.session_guard.config.is_user_allowed", lambda _u: True)
    user = _user(10)
    msg = _message(thread_id=3)
    upd = _update(user=user, message=msg)
    win = TmuxWindow(window_id="@9", window_name="w", cwd="/tmp")

    monkeypatch.setattr(
        "ccbot.session_guard.session_manager.resolve_window_for_thread",
        lambda _uid, _tid: "@9",
    )
    monkeypatch.setattr(
        "ccbot.session_guard.session_manager.get_display_name",
        lambda wid: "disp",
    )
    monkeypatch.setattr(
        "ccbot.session_guard.tmux_manager.find_window_by_id",
        AsyncMock(return_value=win),
    )

    ctx = await require_session(upd, reply_unauthorized=False)
    assert isinstance(ctx, SessionContext)
    assert ctx.user is user
    assert ctx.thread_id == 3
    assert ctx.window_id == "@9"
    assert ctx.window is win


@pytest.mark.asyncio
async def test_require_session_unbound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ccbot.session_guard.config.is_user_allowed", lambda _u: True)
    msg = _message(thread_id=3)
    upd = _update(user=_user(10), message=msg)
    monkeypatch.setattr(
        "ccbot.session_guard.session_manager.resolve_window_for_thread",
        lambda _uid, _tid: None,
    )
    with patch("ccbot.session_guard.safe_reply", new_callable=AsyncMock) as reply:
        ctx = await require_session(upd, reply_unauthorized=False)
        assert ctx is None
        reply.assert_awaited_once()
        assert "No session bound" in reply.await_args.args[1]


@pytest.mark.asyncio
async def test_require_session_missing_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ccbot.session_guard.config.is_user_allowed", lambda _u: True)
    msg = _message(thread_id=3)
    upd = _update(user=_user(10), message=msg)
    monkeypatch.setattr(
        "ccbot.session_guard.session_manager.resolve_window_for_thread",
        lambda _uid, _tid: "@gone",
    )
    monkeypatch.setattr(
        "ccbot.session_guard.session_manager.get_display_name",
        lambda wid: "mywin",
    )
    monkeypatch.setattr(
        "ccbot.session_guard.tmux_manager.find_window_by_id",
        AsyncMock(return_value=None),
    )
    with patch("ccbot.session_guard.safe_reply", new_callable=AsyncMock) as reply:
        ctx = await require_session(upd, reply_unauthorized=False)
        assert ctx is None
        reply.assert_awaited_once()
        assert "mywin" in reply.await_args.args[1]
        assert "no longer exists" in reply.await_args.args[1]


@pytest.mark.asyncio
async def test_require_bound_window_id_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ccbot.session_guard.config.is_user_allowed", lambda _u: True)
    user = _user(5)
    upd = _update(user=user, message=_message(thread_id=2))
    monkeypatch.setattr(
        "ccbot.session_guard.session_manager.resolve_window_for_thread",
        lambda _uid, _tid: "@1",
    )
    result = await require_bound_window_id(upd, reply_unauthorized=False)
    assert result is not None
    u, tid, wid = result
    assert u is user
    assert tid == 2
    assert wid == "@1"
