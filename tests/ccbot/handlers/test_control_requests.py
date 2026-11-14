"""Tests for the bot-side control request handlers (`ccbot new` executor)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

from ccbot import control
from ccbot.config import config
from ccbot.handlers import control_requests as cr

CHAT_ID = -1003978703836
THREAD_ID = 77
RESUME_ID = "12345678-1234-1234-1234-123456789abc"


@pytest.fixture
def cwd(tmp_path):
    path = tmp_path / "proj"
    path.mkdir()
    return path


@pytest.fixture
def bot():
    b = MagicMock()
    b.create_forum_topic = AsyncMock(
        return_value=SimpleNamespace(message_thread_id=THREAD_ID)
    )
    b.delete_forum_topic = AsyncMock(return_value=True)
    return b


@pytest.fixture
def env(monkeypatch, bot, tmp_path):
    """Mock tmux, session manager, hook wait and notice; allow user 111."""
    monkeypatch.setattr(config, "allowed_users", {111})
    monkeypatch.setattr(config, "group_chat_id", None)
    monkeypatch.setattr(config, "tmux_session_name", "ccbot")

    tmux = MagicMock()
    tmux.create_window = AsyncMock(
        return_value=(True, "Created window 'proj'", "proj", "@5")
    )
    tmux.kill_window = AsyncMock(return_value=True)
    monkeypatch.setattr(cr, "tmux_manager", tmux)

    sessions = MagicMock()
    sessions.group_chat_ids = {"111:5": CHAT_ID}
    monkeypatch.setattr(cr, "session_manager", sessions)

    register = AsyncMock(return_value=True)
    monkeypatch.setattr(cr, "register_new_window", register)

    send = AsyncMock()
    monkeypatch.setattr(cr, "safe_send", send)

    return SimpleNamespace(
        bot=bot,
        tmux=tmux,
        sessions=sessions,
        register=register,
        send=send,
    )


async def test_happy_path(env, cwd):
    result = await cr.handle_new_session(env.bot, {"cwd": str(cwd)})

    assert result == {
        "ok": True,
        "window_id": "@5",
        "window_name": "proj",
        "thread_id": THREAD_ID,
        "tmux_session": "ccbot",
        "hook_registered": True,
        "link": "https://t.me/c/3978703836/77",
    }
    env.bot.create_forum_topic.assert_awaited_once_with(chat_id=CHAT_ID, name="proj")
    env.tmux.create_window.assert_awaited_once_with(
        str(cwd), window_name=None, resume_session_id=None
    )
    env.register.assert_awaited_once_with("@5", "proj", str(cwd), None)
    env.sessions.set_group_chat_id.assert_called_once_with(111, THREAD_ID, CHAT_ID)
    env.sessions.bind_thread.assert_called_once_with(
        111, THREAD_ID, "@5", window_name="proj"
    )
    env.send.assert_awaited_once()
    args, kwargs = env.send.call_args
    assert args[0] is env.bot
    assert args[1] == CHAT_ID
    assert "Session started from terminal: proj" in args[2]
    assert kwargs["message_thread_id"] == THREAD_ID
    env.bot.delete_forum_topic.assert_not_awaited()


async def test_name_and_resume_are_passed_through(env, cwd):
    result = await cr.handle_new_session(
        env.bot,
        {"cwd": str(cwd), "name": "  api\x07 server ", "resume": RESUME_ID},
    )

    assert result["ok"] is True
    env.bot.create_forum_topic.assert_awaited_once_with(
        chat_id=CHAT_ID, name="api server"
    )
    env.tmux.create_window.assert_awaited_once_with(
        str(cwd), window_name="api server", resume_session_id=RESUME_ID
    )
    env.register.assert_awaited_once_with("@5", "proj", str(cwd), RESUME_ID)


async def test_hook_not_registered_is_reported(env, cwd):
    env.register.return_value = False

    result = await cr.handle_new_session(env.bot, {"cwd": str(cwd)})

    assert result["ok"] is True
    assert result["hook_registered"] is False


async def test_no_link_for_non_supergroup_chat(env, cwd, monkeypatch):
    monkeypatch.setattr(config, "group_chat_id", 12345)

    result = await cr.handle_new_session(env.bot, {"cwd": str(cwd)})

    assert result["link"] is None


async def test_notice_failure_does_not_fail_request(env, cwd):
    env.send.side_effect = RuntimeError("telegram down")

    result = await cr.handle_new_session(env.bot, {"cwd": str(cwd)})

    assert result["ok"] is True


async def test_missing_cwd(env):
    result = await cr.handle_new_session(env.bot, {})
    assert result == {"ok": False, "error": "cwd must be a string"}


async def test_cwd_not_a_directory(env, tmp_path):
    missing = str(tmp_path / "nope")
    result = await cr.handle_new_session(env.bot, {"cwd": missing})
    assert result == {"ok": False, "error": f"not a directory: {missing}"}
    env.tmux.create_window.assert_not_awaited()


async def test_bad_resume_id(env, cwd):
    result = await cr.handle_new_session(
        env.bot, {"cwd": str(cwd), "resume": "not-a-uuid"}
    )
    assert result == {"ok": False, "error": "invalid session id"}
    env.bot.create_forum_topic.assert_not_awaited()


@pytest.mark.parametrize("name", ["", "   ", "\x00\x07", "x" * 65])
async def test_bad_name(env, cwd, name):
    result = await cr.handle_new_session(env.bot, {"cwd": str(cwd), "name": name})
    assert result == {"ok": False, "error": "invalid name"}
    env.bot.create_forum_topic.assert_not_awaited()


async def test_several_users_need_user_id(env, cwd, monkeypatch):
    monkeypatch.setattr(config, "allowed_users", {111, 222})

    result = await cr.handle_new_session(env.bot, {"cwd": str(cwd)})

    assert result == {
        "ok": False,
        "error": "user_id is required (several ALLOWED_USERS)",
    }


async def test_user_not_allowed(env, cwd):
    result = await cr.handle_new_session(env.bot, {"cwd": str(cwd), "user_id": 999})
    assert result == {"ok": False, "error": "user not allowed"}


async def test_no_forum_group_known(env, cwd):
    env.sessions.group_chat_ids = {"222:5": CHAT_ID}

    result = await cr.handle_new_session(env.bot, {"cwd": str(cwd)})

    assert result["ok"] is False
    assert result["error"].startswith("no forum group known")
    env.bot.create_forum_topic.assert_not_awaited()


async def test_create_topic_bad_request(env, cwd):
    env.bot.create_forum_topic.side_effect = BadRequest("Not enough rights")

    result = await cr.handle_new_session(env.bot, {"cwd": str(cwd)})

    assert result["ok"] is False
    assert result["error"].startswith("cannot create topic:")
    assert "Manage Topics" in result["error"]
    env.tmux.create_window.assert_not_awaited()


async def test_window_failure_rolls_back_topic(env, cwd):
    env.tmux.create_window.return_value = (False, "Failed to create window", "", "")

    result = await cr.handle_new_session(env.bot, {"cwd": str(cwd)})

    assert result == {"ok": False, "error": "Failed to create window"}
    env.bot.delete_forum_topic.assert_awaited_once_with(
        chat_id=CHAT_ID, message_thread_id=THREAD_ID
    )
    env.sessions.bind_thread.assert_not_called()


async def test_chat_from_config_wins(env, cwd, monkeypatch):
    monkeypatch.setattr(config, "group_chat_id", -1001234567890)

    result = await cr.handle_new_session(env.bot, {"cwd": str(cwd)})

    env.bot.create_forum_topic.assert_awaited_once_with(
        chat_id=-1001234567890, name="proj"
    )
    assert result["link"] == "https://t.me/c/1234567890/77"


async def test_chat_from_stored_groups_uses_last_for_user(env, cwd):
    env.sessions.group_chat_ids = {
        "111:5": -1000000000001,
        "222:9": -1000000000009,
        "111:6": -1000000000002,
        "222:3": -1000000000003,
    }

    await cr.handle_new_session(env.bot, {"cwd": str(cwd)})

    env.bot.create_forum_topic.assert_awaited_once_with(
        chat_id=-1000000000002, name="proj"
    )


async def test_failure_after_window_rolls_back_window_and_topic(env, cwd):
    env.register.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await cr.handle_new_session(env.bot, {"cwd": str(cwd)})

    env.tmux.kill_window.assert_awaited_once_with("@5")
    env.bot.delete_forum_topic.assert_awaited_once_with(
        chat_id=CHAT_ID, message_thread_id=THREAD_ID
    )


async def test_handle_request_unknown_action():
    result = await cr.handle_request(MagicMock(), "reboot", {})
    assert result == {"ok": False, "error": "unknown action: reboot"}


async def test_handle_request_turns_exception_into_error(monkeypatch):
    monkeypatch.setattr(
        cr, "handle_new_session", AsyncMock(side_effect=ValueError("bad"))
    )

    result = await cr.handle_request(MagicMock(), "new_session", {})

    assert result == {"ok": False, "error": "internal error: ValueError"}


async def test_process_pending_answers_and_removes_request(
    env, cwd, tmp_path, monkeypatch
):
    monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
    rid = control.write_request("new_session", {"cwd": str(cwd)})

    await cr.process_pending(env.bot)

    assert control.read_result(rid) == {
        "ok": True,
        "window_id": "@5",
        "window_name": "proj",
        "thread_id": THREAD_ID,
        "tmux_session": "ccbot",
        "hook_registered": True,
        "link": "https://t.me/c/3978703836/77",
    }
    assert control.list_requests() == []
    assert not (control.control_dir() / f"{rid}.json").exists()


async def test_poll_loop_survives_iteration_error(monkeypatch):
    calls = {"process": 0, "sleep": 0}

    async def fake_process(bot):
        calls["process"] += 1
        raise RuntimeError("spool broken")

    async def fake_sleep(_seconds):
        calls["sleep"] += 1
        if calls["sleep"] > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(cr, "process_pending", fake_process)
    monkeypatch.setattr(cr.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await cr.control_poll_loop(MagicMock())

    assert calls["process"] == 2
