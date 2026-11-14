"""Tests for window_bind.register_new_window (hook wait and --resume fix-up)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ccbot.handlers import window_bind

RESUME_ID = "12345678-1234-1234-1234-123456789abc"


@pytest.fixture
def sessions(monkeypatch):
    mock = MagicMock()
    mock.wait_for_session_map_entry = AsyncMock(return_value=False)
    mock.override_session_map_entry = AsyncMock()
    monkeypatch.setattr(window_bind, "session_manager", mock)
    return mock


async def test_resume_hook_timeout_forces_session(sessions):
    hook_ok = await window_bind.register_new_window("@5", "proj", "/p", RESUME_ID)

    assert hook_ok is False
    sessions.wait_for_session_map_entry.assert_awaited_once_with("@5", timeout=15.0)
    sessions.set_window_session.assert_called_once_with(
        "@5", RESUME_ID, cwd="/p", window_name="proj"
    )
    sessions.override_session_map_entry.assert_awaited_once_with(
        "@5", RESUME_ID, cwd="/p", window_name="proj"
    )


async def test_plain_hook_timeout_uses_short_wait(sessions):
    hook_ok = await window_bind.register_new_window("@5", "proj", "/p")

    assert hook_ok is False
    sessions.wait_for_session_map_entry.assert_awaited_once_with("@5", timeout=5.0)
    sessions.set_window_session.assert_not_called()
    sessions.override_session_map_entry.assert_not_awaited()


# ── create_and_bind_window: panel keyboard and auto-created topics ────────


def _bind_env(monkeypatch, *, create_ok: bool = True):
    """Mock tmux/session/edit for create_and_bind_window; return (query, user, ctx, edit, sm)."""
    from telegram import CallbackQuery, User

    from ccbot.handlers import bind_flow

    query = MagicMock(spec=CallbackQuery)
    query.answer = AsyncMock()
    user = MagicMock(spec=User)
    user.id = 1
    ctx = MagicMock()
    ctx.user_data = {"_auto_topic_id": 42}
    ctx.bot = AsyncMock()

    tmux = MagicMock()
    if create_ok:
        tmux.create_window = AsyncMock(
            return_value=(True, "Created window 'proj'", "proj", "@5")
        )
    else:
        tmux.create_window = AsyncMock(return_value=(False, "boom", "", ""))
    sm = MagicMock()
    sm.resolve_chat_id.return_value = -100
    edit = AsyncMock()
    monkeypatch.setattr(window_bind, "tmux_manager", tmux)
    monkeypatch.setattr(window_bind, "session_manager", sm)
    monkeypatch.setattr(bind_flow, "session_manager", sm)
    monkeypatch.setattr(
        window_bind, "register_new_window", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(window_bind, "safe_edit", edit)
    return query, user, ctx, edit


async def test_bind_success_attaches_panel_and_renames_auto_topic(monkeypatch):
    query, user, ctx, edit = _bind_env(monkeypatch)

    await window_bind.create_and_bind_window(query, ctx, user, "/p", 42)

    markup = edit.await_args.kwargs["reply_markup"]
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert any(d.startswith("sl:sc:") for d in data)
    assert any(d.startswith("sl:es:") for d in data)
    assert "sl:new" in data
    assert any(d.startswith("sl:kq:") for d in data)
    ctx.bot.edit_forum_topic.assert_awaited_once_with(
        chat_id=-100, message_thread_id=42, name="proj"
    )
    assert "_auto_topic_id" not in ctx.user_data


async def test_bind_failure_keeps_auto_topic(monkeypatch):
    query, user, ctx, edit = _bind_env(monkeypatch, create_ok=False)

    await window_bind.create_and_bind_window(query, ctx, user, "/p", 42)

    assert edit.await_args.args[1] == "❌ boom"
    ctx.bot.delete_forum_topic.assert_not_called()
    ctx.bot.edit_forum_topic.assert_not_called()
    assert ctx.user_data["_auto_topic_id"] == 42
