"""Table-driven tests for startup migration / stale-id resolution (REF-004)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from ccbot.session_migration import (
    apply_startup_state_migration,
    cleanup_stale_session_map_entries,
    is_window_id,
    migrate_session_map_old_format,
    state_needs_legacy_migration,
)


@dataclass
class FakeWS:
    session_id: str = ""
    cwd: str = ""
    window_name: str = ""


class TestIsWindowId:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("@0", True),
            ("@12", True),
            ("@999", True),
            ("proj", False),
            ("@", False),
            ("@x", False),
            ("", False),
            ("@1a", False),
        ],
    )
    def test_is_window_id(self, key: str, expected: bool) -> None:
        assert is_window_id(key) is expected


@dataclass
class StateCase:
    name: str
    window_states: dict
    thread_bindings: dict
    offsets: dict
    display_names: dict
    live_by_name: dict
    live_ids: set[str]
    expect_ws_keys: set[str]
    expect_binding: dict | None
    expect_changed: bool
    expect_event_substr: str | None = None


STATE_CASES = [
    StateCase(
        name="stale_id_reresolved_by_display_name",
        window_states={"@1": FakeWS(session_id="s1", window_name="proj")},
        thread_bindings={1: {10: "@1"}},
        offsets={1: {"@1": 42}},
        display_names={"@1": "proj"},
        live_by_name={"proj": "@9"},
        live_ids={"@9"},
        expect_ws_keys={"@9"},
        expect_binding={1: {10: "@9"}},
        expect_changed=True,
        expect_event_substr="re-resolve",
    ),
    StateCase(
        name="stale_id_dropped_when_no_live_name",
        window_states={"@1": FakeWS(session_id="s1", window_name="gone")},
        thread_bindings={1: {10: "@1"}},
        offsets={},
        display_names={"@1": "gone"},
        live_by_name={},
        live_ids=set(),
        expect_ws_keys=set(),
        expect_binding={},
        expect_changed=True,
        expect_event_substr="drop stale",
    ),
    StateCase(
        name="legacy_window_name_key_migrated",
        window_states={"myproj": FakeWS(session_id="s2")},
        thread_bindings={2: {5: "myproj"}},
        offsets={2: {"myproj": 7}},
        display_names={},
        live_by_name={"myproj": "@4"},
        live_ids={"@4"},
        expect_ws_keys={"@4"},
        expect_binding={2: {5: "@4"}},
        expect_changed=True,
        expect_event_substr="migrate",
    ),
    StateCase(
        name="legacy_orphan_dropped",
        window_states={"ghost": FakeWS(session_id="x")},
        thread_bindings={1: {1: "ghost"}},
        offsets={},
        display_names={},
        live_by_name={"other": "@1"},
        live_ids={"@1"},
        expect_ws_keys=set(),
        expect_binding={},
        expect_changed=True,
        expect_event_substr="drop old-format",
    ),
    StateCase(
        name="live_id_unchanged",
        window_states={"@3": FakeWS(session_id="ok", window_name="w")},
        thread_bindings={1: {2: "@3"}},
        offsets={1: {"@3": 1}},
        display_names={"@3": "w"},
        live_by_name={"w": "@3"},
        live_ids={"@3"},
        expect_ws_keys={"@3"},
        expect_binding={1: {2: "@3"}},
        expect_changed=False,
        expect_event_substr=None,
    ),
    StateCase(
        name="mixed_stale_and_live",
        window_states={
            "@1": FakeWS(session_id="a", window_name="keep"),
            "@2": FakeWS(session_id="b", window_name="move"),
        },
        thread_bindings={1: {1: "@1", 2: "@2"}},
        offsets={},
        display_names={"@1": "keep", "@2": "move"},
        live_by_name={"keep": "@1", "move": "@8"},
        live_ids={"@1", "@8"},
        expect_ws_keys={"@1", "@8"},
        expect_binding={1: {1: "@1", 2: "@8"}},
        expect_changed=True,
        expect_event_substr="re-resolve",
    ),
]


@pytest.mark.parametrize("case", STATE_CASES, ids=lambda c: c.name)
def test_apply_startup_state_migration(case: StateCase) -> None:
    result = apply_startup_state_migration(
        window_states=dict(case.window_states),
        thread_bindings={u: dict(b) for u, b in case.thread_bindings.items()},
        user_window_offsets={u: dict(o) for u, o in case.offsets.items()},
        window_display_names=dict(case.display_names),
        live_by_name=case.live_by_name,
        live_ids=case.live_ids,
    )
    assert set(result.window_states.keys()) == case.expect_ws_keys
    if case.expect_binding is not None:
        assert result.thread_bindings == case.expect_binding
    assert result.changed is case.expect_changed
    if case.expect_event_substr:
        joined = " ".join(result.events)
        assert case.expect_event_substr in joined


def test_migrate_old_format_to_window_id() -> None:
    smap = {
        "ccbot:myproj": {"session_id": "sid", "cwd": "/tmp"},
    }
    changed = migrate_session_map_old_format(smap, {"myproj": "@4"}, "ccbot:")
    assert changed is True
    assert "ccbot:@4" in smap
    assert "ccbot:myproj" not in smap
    assert smap["ccbot:@4"]["window_name"] == "myproj"
    assert smap["ccbot:@4"]["session_id"] == "sid"


def test_migrate_old_format_orphan_dropped() -> None:
    smap = {"ccbot:ghost": {"session_id": "x"}}
    changed = migrate_session_map_old_format(smap, {"other": "@1"}, "ccbot:")
    assert changed is True
    assert smap == {}


def test_migrate_old_format_superseded_by_existing_id() -> None:
    smap = {
        "ccbot:name": {"session_id": "old"},
        "ccbot:@7": {"session_id": "new", "window_name": "name"},
    }
    changed = migrate_session_map_old_format(smap, {"name": "@7"}, "ccbot:")
    assert changed is True
    assert "ccbot:name" not in smap
    assert smap["ccbot:@7"]["session_id"] == "new"


def test_cleanup_stale_ids_only() -> None:
    smap = {
        "ccbot:@1": {"session_id": "a"},
        "ccbot:@99": {"session_id": "b"},
        "other:@1": {"session_id": "c"},
    }
    changed = cleanup_stale_session_map_entries(smap, {"@1"}, "ccbot:")
    assert changed is True
    assert set(smap.keys()) == {"ccbot:@1", "other:@1"}


def test_cleanup_ignores_old_format_keys() -> None:
    smap = {"ccbot:name": {"session_id": "a"}}
    changed = cleanup_stale_session_map_entries(smap, set(), "ccbot:")
    assert changed is False
    assert "ccbot:name" in smap


def test_detects_name_keys() -> None:
    assert state_needs_legacy_migration({"foo": FakeWS()}, {}) is True


def test_detects_name_bindings() -> None:
    assert state_needs_legacy_migration({"@1": FakeWS()}, {1: {1: "name"}}) is True


def test_clean_state() -> None:
    assert state_needs_legacy_migration({"@1": FakeWS()}, {1: {1: "@1"}}) is False


@pytest.mark.asyncio
async def test_session_manager_resolve_stale_ids_uses_migration() -> None:
    """Integration: SessionManager applies result from pure migrator."""
    from ccbot.session import SessionManager, WindowState
    from ccbot.tmux_manager import TmuxWindow

    with (
        patch.object(SessionManager, "_load_state", lambda self: None),
        patch.object(SessionManager, "_save_state", lambda self: None),
    ):
        mgr = SessionManager()
        mgr.window_states = {
            "@1": WindowState(session_id="s", cwd="/t", window_name="proj")
        }
        mgr.thread_bindings = {1: {42: "@1"}}
        mgr.window_display_names = {"@1": "proj"}
        mgr.user_window_offsets = {1: {"@1": 0}}

        live = [TmuxWindow(window_id="@9", window_name="proj", cwd="/t")]
        with (
            patch(
                "ccbot.session.tmux_manager.list_windows",
                new_callable=AsyncMock,
                return_value=live,
            ),
            patch.object(
                mgr, "_cleanup_stale_session_map_entries", new_callable=AsyncMock
            ),
            patch.object(
                mgr, "_migrate_old_format_session_map_keys", new_callable=AsyncMock
            ),
        ):
            await mgr.resolve_stale_ids()

        assert set(mgr.window_states.keys()) == {"@9"}
        assert mgr.thread_bindings[1][42] == "@9"
        assert mgr.window_display_names.get("@9") == "proj"
