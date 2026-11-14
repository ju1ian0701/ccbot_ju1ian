"""Tests for callback_lifecycle handlers and the sl:* router entries."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Message
from telegram.error import BadRequest

from ccbot.handlers.callback_data import (
    CB_SL_CANCEL,
    CB_SL_ESC,
    CB_SL_KILL_ASK,
    CB_SL_KILL_CLOSE,
    CB_SL_KILL_OK,
    CB_SL_NEW,
    CB_SL_NEW_HERE,
    CB_SL_NEW_TOPIC,
    CB_SL_PREFIXES,
    CB_SL_RESTART,
    CB_SL_SCREENSHOT,
)
from ccbot.handlers.callback_lifecycle import (
    _handle_kill_ask,
    _handle_kill_confirm,
    _handle_lifecycle_cancel,
    _handle_new,
    _handle_new_here,
    _handle_new_topic,
    _handle_panel_esc,
    _handle_panel_screenshot,
    _handle_restart,
)
from ccbot.handlers.callback_router import _match_route
from ccbot.handlers.session_lifecycle import NEW_SESSION_TEXT
from ccbot.tmux_manager import TmuxWindow

_M = "ccbot.handlers.callback_lifecycle"


def _cb(thread_id: int | None = 42):
    """Callback update in a topic: thread id comes from query.message."""
    upd = MagicMock()
    user = MagicMock(id=1)
    upd.effective_user = user
    upd.effective_chat = MagicMock(id=-100, type="supergroup")
    upd.message = None
    query = MagicMock()
    query.answer = AsyncMock()
    query.message = MagicMock(spec=Message, message_thread_id=thread_id)
    upd.callback_query = query
    return upd, user, query


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = {}
    ctx.bot = AsyncMock()
    return ctx


def _sm(bound: str | None = "@8") -> MagicMock:
    sm = MagicMock()
    sm.get_window_for_thread.return_value = bound
    sm.get_display_name.return_value = "proj"
    sm.get_window_state.return_value = MagicMock(cwd="/t")
    return sm


async def test_kill_confirm_ends_session_and_edits() -> None:
    upd, user, query = _cb()
    ctx = _ctx()
    with (
        patch(f"{_M}.session_manager", _sm()),
        patch(f"{_M}.end_session", new_callable=AsyncMock, return_value=True) as end,
        patch(f"{_M}.safe_edit", new_callable=AsyncMock) as edit,
    ):
        await _handle_kill_confirm(upd, ctx, user, query, f"{CB_SL_KILL_OK}@8")

    end.assert_awaited_once_with(ctx.bot, 1, 42, "@8", user_data=ctx.user_data)
    edit.assert_awaited_once()
    assert edit.await_args.args[0] is query
    ctx.bot.close_forum_topic.assert_not_called()


async def test_kill_close_variant_closes_topic() -> None:
    upd, user, query = _cb()
    ctx = _ctx()
    with (
        patch(f"{_M}.session_manager", _sm()),
        patch(f"{_M}.end_session", new_callable=AsyncMock, return_value=True),
        patch(f"{_M}.safe_edit", new_callable=AsyncMock),
    ):
        await _handle_kill_confirm(upd, ctx, user, query, f"{CB_SL_KILL_CLOSE}@8")

    ctx.bot.close_forum_topic.assert_awaited_once_with(
        chat_id=-100, message_thread_id=42
    )


async def test_kill_close_failure_sends_warning() -> None:
    upd, user, query = _cb()
    ctx = _ctx()
    ctx.bot.close_forum_topic = AsyncMock(side_effect=BadRequest("not enough rights"))
    with (
        patch(f"{_M}.session_manager", _sm()),
        patch(f"{_M}.end_session", new_callable=AsyncMock, return_value=True),
        patch(f"{_M}.safe_edit", new_callable=AsyncMock),
        patch(f"{_M}.safe_send", new_callable=AsyncMock) as send,
    ):
        await _handle_kill_confirm(upd, ctx, user, query, f"{CB_SL_KILL_CLOSE}@8")

    send.assert_awaited_once()
    args, kwargs = send.await_args
    assert args[0] is ctx.bot
    assert args[1] == -100
    assert "could not be closed" in args[2]
    assert kwargs["message_thread_id"] == 42


async def test_stale_button_alerts_and_does_not_end() -> None:
    upd, user, query = _cb()
    ctx = _ctx()
    with (
        patch(f"{_M}.session_manager", _sm(bound="@9")),
        patch(f"{_M}.end_session", new_callable=AsyncMock) as end,
        patch(f"{_M}.safe_edit", new_callable=AsyncMock) as edit,
    ):
        await _handle_kill_confirm(upd, ctx, user, query, f"{CB_SL_KILL_OK}@8")

    query.answer.assert_awaited_once_with(
        "Stale button (session changed)", show_alert=True
    )
    end.assert_not_awaited()
    edit.assert_not_awaited()


async def test_stale_when_no_thread_id() -> None:
    upd, user, query = _cb(thread_id=None)
    ctx = _ctx()
    with (
        patch(f"{_M}.session_manager", _sm()),
        patch(f"{_M}.end_session", new_callable=AsyncMock) as end,
    ):
        await _handle_kill_confirm(upd, ctx, user, query, f"{CB_SL_KILL_OK}@8")

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True
    end.assert_not_awaited()


async def test_cancel_edits_to_cancelled() -> None:
    upd, user, query = _cb()
    with patch(f"{_M}.safe_edit", new_callable=AsyncMock) as edit:
        await _handle_lifecycle_cancel(upd, _ctx(), user, query, CB_SL_CANCEL)

    edit.assert_awaited_once()
    assert edit.await_args.args[1] == "Cancelled"


async def test_restart_calls_restart_claude() -> None:
    upd, user, query = _cb()
    window = TmuxWindow(
        window_id="@8", window_name="proj", cwd="/t", pane_current_command="bash"
    )
    tmux = MagicMock()
    tmux.find_window_by_id = AsyncMock(return_value=window)
    with (
        patch(f"{_M}.session_manager", _sm()),
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.restart_claude", new_callable=AsyncMock, return_value=True) as rc,
        patch(f"{_M}.safe_edit", new_callable=AsyncMock),
    ):
        await _handle_restart(upd, _ctx(), user, query, f"{CB_SL_RESTART}@8")

    rc.assert_awaited_once_with("@8")


async def test_restart_refused_while_claude_is_running() -> None:
    """The start command is typed into the pane: never into a live Claude."""
    upd, user, query = _cb()
    window = TmuxWindow(
        window_id="@8", window_name="proj", cwd="/t", pane_current_command="claude"
    )
    tmux = MagicMock()
    tmux.find_window_by_id = AsyncMock(return_value=window)
    with (
        patch(f"{_M}.session_manager", _sm()),
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.restart_claude", new_callable=AsyncMock) as rc,
    ):
        await _handle_restart(upd, _ctx(), user, query, f"{CB_SL_RESTART}@8")

    query.answer.assert_awaited_once_with("Claude is already running", show_alert=True)
    rc.assert_not_awaited()


async def test_restart_missing_window_alerts() -> None:
    upd, user, query = _cb()
    tmux = MagicMock()
    tmux.find_window_by_id = AsyncMock(return_value=None)
    with (
        patch(f"{_M}.session_manager", _sm()),
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.restart_claude", new_callable=AsyncMock) as rc,
    ):
        await _handle_restart(upd, _ctx(), user, query, f"{CB_SL_RESTART}@8")

    query.answer.assert_awaited_once_with("Window not found", show_alert=True)
    rc.assert_not_awaited()


async def test_kill_ask_shows_confirmation() -> None:
    upd, user, query = _cb()
    with (
        patch(f"{_M}.session_manager", _sm()),
        patch(f"{_M}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        await _handle_kill_ask(upd, _ctx(), user, query, f"{CB_SL_KILL_ASK}@8")

    reply.assert_awaited_once()
    assert reply.await_args.args[0] is query.message
    markup = reply.await_args.kwargs["reply_markup"]
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert f"{CB_SL_KILL_OK}@8" in data
    query.answer.assert_awaited_once()


@pytest.mark.parametrize("prefix", sorted(CB_SL_PREFIXES))
def test_router_resolves_every_lifecycle_prefix(prefix: str) -> None:
    assert _match_route(f"{prefix}@8") is not None


def test_router_resolves_cancel_exactly() -> None:
    assert _match_route(CB_SL_CANCEL) is _handle_lifecycle_cancel


def test_router_maps_kill_confirm_variants() -> None:
    assert _match_route(f"{CB_SL_KILL_OK}@8") is _handle_kill_confirm
    assert _match_route(f"{CB_SL_KILL_CLOSE}@8") is _handle_kill_confirm
    assert _match_route(f"{CB_SL_KILL_ASK}@8") is _handle_kill_ask
    assert _match_route(f"{CB_SL_RESTART}@8") is _handle_restart


# ── session panel: /new choice, restart here, new topic, screenshot, Esc ─


def _sm_cwd(bound: str | None = "@8", cwd: str = "/t") -> MagicMock:
    sm = _sm(bound)
    sm.get_window_state.return_value = MagicMock(cwd=cwd)
    return sm


def _panel_cb(thread_id: int = 42):
    upd, user, query = _cb(thread_id)
    query.answer = AsyncMock()
    query.message.reply_document = AsyncMock()
    return upd, user, query


async def test_new_shows_choice_keyboard_when_bound() -> None:
    upd, user, query = _panel_cb()
    with (
        patch(f"{_M}.session_manager", _sm_cwd()),
        patch(f"{_M}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        await _handle_new(upd, _ctx(), user, query, CB_SL_NEW)

    reply.assert_awaited_once()
    assert reply.await_args.args[0] is query.message
    assert reply.await_args.args[1] == NEW_SESSION_TEXT
    data = [
        b.callback_data
        for row in reply.await_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    assert f"{CB_SL_NEW_HERE}@8" in data
    assert CB_SL_NEW_TOPIC in data
    assert CB_SL_CANCEL in data
    query.answer.assert_awaited_once_with()


async def test_new_alerts_when_topic_unbound() -> None:
    upd, user, query = _panel_cb()
    with (
        patch(f"{_M}.session_manager", _sm(None)),
        patch(f"{_M}.safe_reply", new_callable=AsyncMock) as reply,
    ):
        await _handle_new(upd, _ctx(), user, query, CB_SL_NEW)

    reply.assert_not_awaited()
    query.answer.assert_awaited_once_with("No session in this topic", show_alert=True)


async def test_restart_here_orders_edit_end_create() -> None:
    upd, user, query = _panel_cb()
    ctx = _ctx()
    calls: list[str] = []
    edit = AsyncMock(side_effect=lambda *a, **k: calls.append("edit"))
    end = AsyncMock(side_effect=lambda *a, **k: calls.append("end") or True)
    create = AsyncMock(side_effect=lambda *a, **k: calls.append("create"))
    with (
        patch(f"{_M}.session_manager", _sm_cwd(cwd="/work")),
        patch(f"{_M}.safe_edit", edit),
        patch(f"{_M}.end_session", end),
        patch(f"{_M}.create_and_bind_window", create),
    ):
        await _handle_new_here(upd, ctx, user, query, f"{CB_SL_NEW_HERE}@8")

    assert calls == ["edit", "end", "create"]
    end.assert_awaited_once_with(ctx.bot, 1, 42, "@8", user_data=ctx.user_data)
    create.assert_awaited_once_with(query, ctx, user, "/work", 42)
    query.answer.assert_not_awaited()


async def test_restart_here_stale_button_alerts_only() -> None:
    upd, user, query = _panel_cb()
    with (
        patch(f"{_M}.session_manager", _sm_cwd(bound="@9")),
        patch(f"{_M}.end_session", new_callable=AsyncMock) as end,
        patch(f"{_M}.safe_edit", new_callable=AsyncMock) as edit,
        patch(f"{_M}.create_and_bind_window", new_callable=AsyncMock) as create,
    ):
        await _handle_new_here(upd, _ctx(), user, query, f"{CB_SL_NEW_HERE}@8")

    query.answer.assert_awaited_once_with(
        "Stale button (session changed)", show_alert=True
    )
    end.assert_not_awaited()
    edit.assert_not_awaited()
    create.assert_not_awaited()


async def test_restart_here_empty_cwd_alerts_and_does_not_end() -> None:
    upd, user, query = _panel_cb()
    tmux = MagicMock()
    tmux.find_window_by_id = AsyncMock(return_value=None)
    with (
        patch(f"{_M}.session_manager", _sm_cwd(cwd="")),
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.end_session", new_callable=AsyncMock) as end,
        patch(f"{_M}.create_and_bind_window", new_callable=AsyncMock) as create,
    ):
        await _handle_new_here(upd, _ctx(), user, query, f"{CB_SL_NEW_HERE}@8")

    query.answer.assert_awaited_once_with("Unknown working directory", show_alert=True)
    end.assert_not_awaited()
    create.assert_not_awaited()


async def test_restart_here_falls_back_to_live_window_cwd() -> None:
    upd, user, query = _panel_cb()
    ctx = _ctx()
    tmux = MagicMock()
    tmux.find_window_by_id = AsyncMock(return_value=MagicMock(cwd="/live"))
    with (
        patch(f"{_M}.session_manager", _sm_cwd(cwd="")),
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.safe_edit", new_callable=AsyncMock),
        patch(f"{_M}.end_session", new_callable=AsyncMock, return_value=True),
        patch(f"{_M}.create_and_bind_window", new_callable=AsyncMock) as create,
    ):
        await _handle_new_here(upd, ctx, user, query, f"{CB_SL_NEW_HERE}@8")

    create.assert_awaited_once_with(query, ctx, user, "/live", 42)


async def test_new_topic_starts_in_bound_session_directory() -> None:
    upd, user, query = _panel_cb()
    ctx = _ctx()
    with (
        patch(f"{_M}.session_manager", _sm_cwd(cwd="/proj")),
        patch(
            f"{_M}.start_new_topic_flow", new_callable=AsyncMock, return_value=99
        ) as flow,
        patch(f"{_M}.safe_edit", new_callable=AsyncMock) as edit,
    ):
        await _handle_new_topic(upd, ctx, user, query, CB_SL_NEW_TOPIC)

    flow.assert_awaited_once_with(ctx.bot, ctx, 1, -100, "/proj")
    assert edit.await_args.args[0] is query
    assert "Topic created" in edit.await_args.args[1]
    query.answer.assert_awaited_once_with()


async def test_new_topic_failure_edits_message() -> None:
    upd, user, query = _panel_cb()
    with (
        patch(f"{_M}.session_manager", _sm_cwd(bound=None)),
        patch(
            f"{_M}.start_new_topic_flow", new_callable=AsyncMock, return_value=None
        ) as flow,
        patch(f"{_M}.safe_edit", new_callable=AsyncMock) as edit,
    ):
        await _handle_new_topic(upd, _ctx(), user, query, CB_SL_NEW_TOPIC)

    flow.assert_awaited_once()
    assert flow.await_args.args[4] is None
    assert "Could not create a topic" in edit.await_args.args[1]


async def test_panel_screenshot_sends_document_with_keyboard() -> None:
    upd, user, query = _panel_cb()
    tmux = MagicMock()
    tmux.capture_pane = AsyncMock(return_value="$ hello")
    with (
        patch(f"{_M}.session_manager", _sm_cwd()),
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.text_to_image", new_callable=AsyncMock, return_value=b"png"),
    ):
        await _handle_panel_screenshot(
            upd, _ctx(), user, query, f"{CB_SL_SCREENSHOT}@8"
        )

    tmux.capture_pane.assert_awaited_once_with("@8", with_ansi=True)
    query.message.reply_document.assert_awaited_once()
    kwargs = query.message.reply_document.await_args.kwargs
    assert kwargs["filename"] == "screenshot.png"
    data = [
        b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row
    ]
    assert data  # screenshot keyboard attached
    query.answer.assert_awaited_once_with()


async def test_panel_screenshot_empty_capture_alerts() -> None:
    upd, user, query = _panel_cb()
    tmux = MagicMock()
    tmux.capture_pane = AsyncMock(return_value=None)
    with (
        patch(f"{_M}.session_manager", _sm_cwd()),
        patch(f"{_M}.tmux_manager", tmux),
        patch(f"{_M}.text_to_image", new_callable=AsyncMock) as render,
    ):
        await _handle_panel_screenshot(
            upd, _ctx(), user, query, f"{CB_SL_SCREENSHOT}@8"
        )

    query.answer.assert_awaited_once_with("Failed to capture pane", show_alert=True)
    render.assert_not_awaited()
    query.message.reply_document.assert_not_awaited()


async def test_panel_esc_sends_escape_and_answers() -> None:
    upd, user, query = _panel_cb()
    tmux = MagicMock()
    tmux.send_keys = AsyncMock(return_value=True)
    with (
        patch(f"{_M}.session_manager", _sm_cwd()),
        patch(f"{_M}.tmux_manager", tmux),
    ):
        await _handle_panel_esc(upd, _ctx(), user, query, f"{CB_SL_ESC}@8")

    tmux.send_keys.assert_awaited_once_with("@8", "\x1b", enter=False)
    query.answer.assert_awaited_once_with("⎋ Sent Escape")


def test_router_panel_routes() -> None:
    assert _match_route(CB_SL_NEW) is _handle_new
    assert _match_route(CB_SL_NEW_TOPIC) is _handle_new_topic
    assert _match_route(f"{CB_SL_NEW_HERE}@8") is _handle_new_here
    assert _match_route(f"{CB_SL_SCREENSHOT}@8") is _handle_panel_screenshot
    assert _match_route(f"{CB_SL_ESC}@8") is _handle_panel_esc


def test_router_exact_new_not_swallowed_by_prefix() -> None:
    # "sl:new" must not be captured by any prefix route; "sl:nt" must not match "sl:nh:".
    assert _match_route(CB_SL_NEW) is _handle_new
    assert _match_route(CB_SL_NEW_TOPIC) is not _handle_new_here
    assert _match_route(CB_SL_NEW_TOPIC) is _handle_new_topic
    assert _match_route(f"{CB_SL_NEW_HERE}@8") is not _handle_new_topic


def test_router_every_panel_prefix_is_in_lifecycle_prefixes() -> None:
    for prefix in (CB_SL_NEW_HERE, CB_SL_SCREENSHOT, CB_SL_ESC):
        assert prefix in CB_SL_PREFIXES
