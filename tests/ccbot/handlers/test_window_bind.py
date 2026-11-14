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
