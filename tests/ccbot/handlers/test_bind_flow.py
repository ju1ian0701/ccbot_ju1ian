"""Tests for bind_flow: unbound-topic flow, auto-created topics, and their cancel paths."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import CallbackQuery, Message
from telegram.error import BadRequest

from ccbot.handlers.bind_flow import (
    AUTO_TOPIC_KEY,
    discard_auto_topic,
    rename_auto_topic,
    start_bind_flow,
    start_new_topic_flow,
)
from ccbot.handlers.callback_data import CB_DIR_CANCEL, CB_WIN_CANCEL
from ccbot.handlers.callback_directory import _handle_dir_cancel
from ccbot.handlers.callback_pickers import _handle_win_cancel
from ccbot.handlers.directory_browser import (
    BROWSE_PATH_KEY,
    STATE_BROWSING_DIRECTORY,
    STATE_KEY,
    STATE_SELECTING_WINDOW,
    UNBOUND_WINDOWS_KEY,
)
from ccbot.tmux_manager import TmuxWindow

_BF = "ccbot.handlers.bind_flow"
_MH = "ccbot.handlers.message_handlers"


def _ctx(user_data: dict | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = {} if user_data is None else user_data
    ctx.bot = AsyncMock()
    return ctx


def _sm(bound_windows: list[str] | None = None) -> MagicMock:
    sm = MagicMock()
    sm.iter_thread_bindings.return_value = [
        (1, tid, wid) for tid, wid in enumerate(bound_windows or [], start=1)
    ]
    sm.resolve_chat_id.return_value = -100
    return sm


def _tmux(windows: list[TmuxWindow]) -> MagicMock:
    tmux = MagicMock()
    tmux.list_windows = AsyncMock(return_value=windows)
    return tmux


def _win(wid: str, name: str = "proj") -> TmuxWindow:
    return TmuxWindow(window_id=wid, window_name=name, cwd="/tmp/proj")


# ── start_bind_flow ──────────────────────────────────────────────────────


async def test_start_bind_flow_shows_window_picker_when_unbound_windows_exist() -> None:
    ctx = _ctx()
    message = MagicMock()
    with (
        patch(f"{_BF}.tmux_manager", _tmux([_win("@1"), _win("@2", "other")])),
        patch(f"{_BF}.session_manager", _sm(bound_windows=["@1"])),
        patch(f"{_BF}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        await start_bind_flow(message, ctx, 1, 42, "hello")

    assert ctx.user_data[STATE_KEY] == STATE_SELECTING_WINDOW
    assert ctx.user_data[UNBOUND_WINDOWS_KEY] == ["@2"]
    assert ctx.user_data["_pending_thread_id"] == 42
    assert ctx.user_data["_pending_thread_text"] == "hello"
    reply.assert_awaited_once()
    assert reply.await_args.args[0] is message


async def test_start_bind_flow_shows_directory_browser_when_none_unbound() -> None:
    ctx = _ctx()
    with (
        patch(f"{_BF}.tmux_manager", _tmux([_win("@1")])),
        patch(f"{_BF}.session_manager", _sm(bound_windows=["@1"])),
        patch(f"{_BF}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        await start_bind_flow(MagicMock(), ctx, 1, 42, "hello")

    assert ctx.user_data[STATE_KEY] == STATE_BROWSING_DIRECTORY
    assert ctx.user_data[BROWSE_PATH_KEY] == str(Path.cwd())
    assert ctx.user_data["_pending_thread_id"] == 42
    assert ctx.user_data["_pending_thread_text"] == "hello"
    reply.assert_awaited_once()


async def test_start_bind_flow_without_pending_text_stores_no_text() -> None:
    ctx = _ctx({"_pending_thread_text": "stale"})
    with (
        patch(f"{_BF}.tmux_manager", _tmux([])),
        patch(f"{_BF}.session_manager", _sm()),
        patch(f"{_BF}.safe_reply", new_callable=AsyncMock),
    ):
        await start_bind_flow(MagicMock(), ctx, 1, 42, None)

    assert ctx.user_data[STATE_KEY] == STATE_BROWSING_DIRECTORY
    assert "_pending_thread_text" not in ctx.user_data


async def test_text_handler_unbound_topic_shows_picker_and_stores_text() -> None:
    from ccbot.handlers.message_handlers import text_handler

    upd = MagicMock()
    upd.message = MagicMock(text="build the thing", message_thread_id=42)
    upd.effective_chat = MagicMock(id=-100, type="supergroup")
    user = MagicMock(id=1)
    ctx = _ctx()
    sm = _sm(bound_windows=[])
    sm.get_window_for_thread.return_value = None
    with (
        patch(f"{_MH}.require_user", new_callable=AsyncMock, return_value=user),
        patch(f"{_MH}.session_manager", sm),
        patch(f"{_BF}.tmux_manager", _tmux([_win("@3", "free")])),
        patch(f"{_BF}.session_manager", sm),
        patch(f"{_BF}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        await text_handler(upd, ctx)

    assert ctx.user_data[STATE_KEY] == STATE_SELECTING_WINDOW
    assert ctx.user_data["_pending_thread_text"] == "build the thing"
    assert ctx.user_data["_pending_thread_id"] == 42
    reply.assert_awaited_once()
    sm.set_group_chat_id.assert_called_once_with(1, 42, -100)


# ── start_new_topic_flow ─────────────────────────────────────────────────


def _bot_with_topic(tid: int = 77) -> AsyncMock:
    bot = AsyncMock()
    bot.create_forum_topic = AsyncMock(
        return_value=SimpleNamespace(message_thread_id=tid)
    )
    return bot


async def test_start_new_topic_flow_creates_topic_and_opens_browser() -> None:
    bot = _bot_with_topic(77)
    ctx = _ctx({"_selected_path": "/x", STATE_KEY: "selecting_session"})
    sm = _sm()
    with (
        patch(f"{_BF}.session_manager", sm),
        patch(f"{_BF}.safe_send", new_callable=AsyncMock) as send,
    ):
        tid = await start_new_topic_flow(bot, ctx, 1, -100, None)

    assert tid == 77
    bot.create_forum_topic.assert_awaited_once_with(chat_id=-100, name="🆕 new session")
    sm.set_group_chat_id.assert_called_once_with(1, 77, -100)
    assert ctx.user_data[STATE_KEY] == STATE_BROWSING_DIRECTORY
    assert ctx.user_data["_pending_thread_id"] == 77
    assert ctx.user_data[AUTO_TOPIC_KEY] == 77
    assert "_pending_thread_text" not in ctx.user_data
    send.assert_awaited_once()
    args, kwargs = send.await_args
    assert args[0] is bot
    assert args[1] == -100
    assert kwargs["message_thread_id"] == 77
    assert "reply_markup" in kwargs


async def test_start_new_topic_flow_failure_returns_none_and_sends_nothing() -> None:
    bot = AsyncMock()
    bot.create_forum_topic = AsyncMock(side_effect=BadRequest("not enough rights"))
    ctx = _ctx()
    with (
        patch(f"{_BF}.session_manager", _sm()),
        patch(f"{_BF}.safe_send", new_callable=AsyncMock) as send,
    ):
        tid = await start_new_topic_flow(bot, ctx, 1, -100, None)

    assert tid is None
    send.assert_not_awaited()
    assert AUTO_TOPIC_KEY not in ctx.user_data


async def test_start_new_topic_flow_missing_start_path_falls_back_to_cwd() -> None:
    ctx = _ctx()
    with (
        patch(f"{_BF}.session_manager", _sm()),
        patch(f"{_BF}.safe_send", new_callable=AsyncMock),
    ):
        await start_new_topic_flow(
            _bot_with_topic(), ctx, 1, -100, "/definitely/not/a/dir"
        )

    assert ctx.user_data[BROWSE_PATH_KEY] == str(Path.cwd())


async def test_start_new_topic_flow_uses_existing_start_path(tmp_path) -> None:
    ctx = _ctx()
    with (
        patch(f"{_BF}.session_manager", _sm()),
        patch(f"{_BF}.safe_send", new_callable=AsyncMock),
    ):
        await start_new_topic_flow(_bot_with_topic(), ctx, 1, -100, str(tmp_path))

    assert ctx.user_data[BROWSE_PATH_KEY] == str(tmp_path)


# ── rename_auto_topic / discard_auto_topic ───────────────────────────────


async def test_rename_auto_topic_renames_on_match_and_pops_key() -> None:
    ctx = _ctx({AUTO_TOPIC_KEY: 42})
    with patch(f"{_BF}.session_manager", _sm()):
        await rename_auto_topic(ctx.bot, ctx, 1, 42, "proj")

    ctx.bot.edit_forum_topic.assert_awaited_once_with(
        chat_id=-100, message_thread_id=42, name="proj"
    )
    assert AUTO_TOPIC_KEY not in ctx.user_data


async def test_rename_auto_topic_ignores_other_topics() -> None:
    ctx = _ctx({AUTO_TOPIC_KEY: 42})
    await rename_auto_topic(ctx.bot, ctx, 1, 7, "proj")

    ctx.bot.edit_forum_topic.assert_not_awaited()
    assert ctx.user_data[AUTO_TOPIC_KEY] == 42


async def test_rename_auto_topic_swallows_telegram_error() -> None:
    ctx = _ctx({AUTO_TOPIC_KEY: 42})
    ctx.bot.edit_forum_topic = AsyncMock(side_effect=BadRequest("nope"))
    with patch(f"{_BF}.session_manager", _sm()):
        await rename_auto_topic(ctx.bot, ctx, 1, 42, "proj")  # must not raise


async def test_discard_auto_topic_deletes_on_match() -> None:
    ctx = _ctx({AUTO_TOPIC_KEY: 42})
    with patch(f"{_BF}.session_manager", _sm()):
        await discard_auto_topic(ctx.bot, ctx, 1, 42)

    ctx.bot.delete_forum_topic.assert_awaited_once_with(
        chat_id=-100, message_thread_id=42
    )
    assert AUTO_TOPIC_KEY not in ctx.user_data


async def test_discard_auto_topic_none_thread_is_noop() -> None:
    ctx = _ctx({AUTO_TOPIC_KEY: 42})
    await discard_auto_topic(ctx.bot, ctx, 1, None)

    ctx.bot.delete_forum_topic.assert_not_awaited()


async def test_discard_auto_topic_ignores_normal_topic() -> None:
    ctx = _ctx({})
    await discard_auto_topic(ctx.bot, ctx, 1, 42)

    ctx.bot.delete_forum_topic.assert_not_awaited()


async def test_discard_auto_topic_swallows_telegram_error() -> None:
    ctx = _ctx({AUTO_TOPIC_KEY: 42})
    ctx.bot.delete_forum_topic = AsyncMock(side_effect=BadRequest("nope"))
    with patch(f"{_BF}.session_manager", _sm()):
        await discard_auto_topic(ctx.bot, ctx, 1, 42)  # must not raise


# ── cancel handlers discard only auto-created topics ─────────────────────


def _cancel_update(thread_id: int = 42):
    upd = MagicMock()
    upd.message = None
    upd.effective_user = MagicMock(id=1)
    upd.effective_chat = MagicMock(id=-100, type="supergroup")
    query = MagicMock(spec=CallbackQuery)
    query.answer = AsyncMock()
    query.message = MagicMock(spec=Message, message_thread_id=thread_id)
    upd.callback_query = query
    return upd, MagicMock(id=1), query


async def test_dir_cancel_deletes_auto_topic() -> None:
    upd, user, query = _cancel_update()
    ctx = _ctx({"_pending_thread_id": 42, AUTO_TOPIC_KEY: 42, STATE_KEY: "x"})
    with (
        patch("ccbot.handlers.callback_directory.safe_edit", new_callable=AsyncMock),
        patch(f"{_BF}.session_manager", _sm()),
    ):
        await _handle_dir_cancel(upd, ctx, user, query, CB_DIR_CANCEL)

    ctx.bot.delete_forum_topic.assert_awaited_once_with(
        chat_id=-100, message_thread_id=42
    )


async def test_win_cancel_leaves_normal_topic_alone() -> None:
    upd, user, query = _cancel_update()
    ctx = _ctx(
        {
            "_pending_thread_id": 42,
            STATE_KEY: STATE_SELECTING_WINDOW,
            UNBOUND_WINDOWS_KEY: ["@1"],
        }
    )
    with (
        patch("ccbot.handlers.callback_pickers.safe_edit", new_callable=AsyncMock),
        patch(f"{_BF}.session_manager", _sm()),
    ):
        await _handle_win_cancel(upd, ctx, user, query, CB_WIN_CANCEL)

    ctx.bot.delete_forum_topic.assert_not_awaited()
    query.answer.assert_awaited_once_with("Cancelled")
