"""Startup migration / stale window-id re-resolution (REF-004).

## Authoritative state model (topic-only)

| Store | File | Authority | Notes |
|-------|------|-----------|-------|
| **thread_bindings** | ``state.json`` | Topic → window @id | Routing key for inbound/outbound Telegram |
| **window_states** | ``state.json`` | window @id → Claude session + cwd | Filled/updated from session_map |
| **window_display_names** | ``state.json`` | window @id → display name | UI + re-resolve after tmux restart |
| **user_window_offsets** | ``state.json`` | read cursor per user/window | Non-authoritative for routing |
| **group_chat_ids** | ``state.json`` | user:thread → supergroup chat_id | Outbound send target |
| **session_map** | ``session_map.json`` | Hook-written window@id → session | Ephemeral; monitor + load_session_map |
| **monitor_state** | ``monitor_state.json`` | JSONL byte offsets | Independent of bindings |

**Canonical key form:** tmux window id ``@N`` (never window_name for routing).

Legacy keys (window_name without ``@``) are migrated once at startup via
:func:`apply_startup_state_migration` / :func:`migrate_session_map_old_format`.
After a successful migrate, new writes always use ``@id``. Runtime code must
not introduce window_name as a routing key.

This module is intentionally **pure** (no I/O, no tmux) so migrations are
unit-testable. ``SessionManager.resolve_stale_ids`` orchestrates live data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def is_window_id(key: str) -> bool:
    """Return True if *key* looks like a tmux window ID (``@0``, ``@12``)."""
    return key.startswith("@") and len(key) > 1 and key[1:].isdigit()


@dataclass
class StartupMigrationResult:
    """Outcome of applying in-memory state migration."""

    window_states: dict[str, Any]
    thread_bindings: dict[int, dict[int, str]]
    user_window_offsets: dict[int, dict[str, int]]
    window_display_names: dict[str, str]
    changed: bool
    events: list[str] = field(default_factory=list)


def build_old_names_snapshot(
    window_states: dict[str, Any],
    window_display_names: dict[str, str],
) -> dict[str, str]:
    """Snapshot old_id → display_name before mutation.

    ``resolve_stale_ids`` rewrites ``window_display_names`` as it goes;
    thread_bindings / offsets must still resolve stale IDs against the old view.
    """
    old_names: dict[str, str] = dict(window_display_names)
    for key, ws in window_states.items():
        name = ""
        if hasattr(ws, "window_name"):
            name = ws.window_name or ""
        elif isinstance(ws, dict):
            name = ws.get("window_name", "") or ""
        if name and key not in old_names:
            old_names[key] = name
    return old_names


def apply_startup_state_migration(
    window_states: dict[str, Any],
    thread_bindings: dict[int, dict[int, str]],
    user_window_offsets: dict[int, dict[str, int]],
    window_display_names: dict[str, str],
    live_by_name: dict[str, str],
    live_ids: set[str],
) -> StartupMigrationResult:
    """Migrate in-memory state to live ``@window_id`` keys.

    Handles:
      1. Old-format keys (window_name) → window_id
      2. Stale IDs (window gone) re-resolved by display name, or dropped

    Does not touch disk. Caller persists when ``result.changed``.
    """
    old_names = build_old_names_snapshot(window_states, window_display_names)
    display_names: dict[str, str] = dict(window_display_names)
    events: list[str] = []
    changed = False

    # --- window_states ---
    new_window_states: dict[str, Any] = {}
    for key, ws in window_states.items():
        if is_window_id(key):
            if key in live_ids:
                new_window_states[key] = ws
            else:
                display = old_names.get(key, key)
                new_id = live_by_name.get(display)
                if new_id:
                    events.append(
                        f"re-resolve window_state {key} -> {new_id} ({display})"
                    )
                    new_window_states[new_id] = ws
                    if hasattr(ws, "window_name"):
                        ws.window_name = display
                    display_names[new_id] = display
                    display_names.pop(key, None)
                    changed = True
                else:
                    events.append(f"drop stale window_state {key} ({display})")
                    display_names.pop(key, None)
                    changed = True
        else:
            # Legacy: key is window_name — migrate once then never re-key by name
            new_id = live_by_name.get(key)
            if new_id:
                events.append(f"migrate window_state key {key} -> {new_id}")
                if hasattr(ws, "window_name"):
                    ws.window_name = key
                new_window_states[new_id] = ws
                display_names[new_id] = key
                changed = True
            else:
                events.append(f"drop old-format window_state {key}")
                changed = True

    # --- thread_bindings ---
    new_thread_bindings: dict[int, dict[int, str]] = {}
    for uid, bindings in thread_bindings.items():
        new_bindings: dict[int, str] = {}
        for thread_id, val in bindings.items():
            if is_window_id(val):
                if val in live_ids:
                    new_bindings[thread_id] = val
                else:
                    display = old_names.get(val, val)
                    new_id = live_by_name.get(display)
                    if new_id:
                        events.append(
                            f"re-resolve binding user={uid} thread={thread_id} "
                            f"{val} -> {new_id}"
                        )
                        new_bindings[thread_id] = new_id
                        display_names[new_id] = display
                        changed = True
                    else:
                        events.append(
                            f"drop stale binding user={uid} thread={thread_id} "
                            f"window_id={val}"
                        )
                        changed = True
            else:
                new_id = live_by_name.get(val)
                if new_id:
                    events.append(
                        f"migrate binding user={uid} thread={thread_id} "
                        f"{val} -> {new_id}"
                    )
                    new_bindings[thread_id] = new_id
                    display_names[new_id] = val
                    changed = True
                else:
                    events.append(
                        f"drop old-format binding user={uid} thread={thread_id} "
                        f"name={val}"
                    )
                    changed = True
        if new_bindings:
            new_thread_bindings[uid] = new_bindings

    # --- user_window_offsets ---
    new_offsets_all: dict[int, dict[str, int]] = {}
    for uid, offsets in user_window_offsets.items():
        new_offsets: dict[str, int] = {}
        for key, offset in offsets.items():
            if is_window_id(key):
                if key in live_ids:
                    new_offsets[key] = offset
                else:
                    display = old_names.get(key, key)
                    new_id = live_by_name.get(display)
                    if new_id:
                        new_offsets[new_id] = offset
                        changed = True
                    else:
                        changed = True
            else:
                new_id = live_by_name.get(key)
                if new_id:
                    new_offsets[new_id] = offset
                    changed = True
                else:
                    changed = True
        new_offsets_all[uid] = new_offsets

    for ev in events:
        logger.info("%s", ev)

    return StartupMigrationResult(
        window_states=new_window_states,
        thread_bindings=new_thread_bindings,
        user_window_offsets=new_offsets_all,
        window_display_names=display_names,
        changed=changed,
        events=events,
    )


def migrate_session_map_old_format(
    session_map: dict[str, dict],
    live_by_name: dict[str, str],
    session_prefix: str,
) -> bool:
    """Migrate old-format session_map keys (window_name) to @window_id in place.

    Keys look like ``{tmux_session}:name`` vs ``{tmux_session}:@N``.
    Returns True if *session_map* was mutated.
    """
    prefix = session_prefix if session_prefix.endswith(":") else f"{session_prefix}:"
    old_keys = [
        key
        for key in session_map
        if key.startswith(prefix) and not is_window_id(key[len(prefix) :])
    ]
    changed = False
    for key in old_keys:
        window_name = key[len(prefix) :]
        info = session_map.pop(key)
        changed = True
        new_id = live_by_name.get(window_name)
        if not new_id:
            logger.info("Dropping orphan old-format session_map key: %s", key)
            continue
        new_key = f"{prefix}{new_id}"
        if new_key in session_map:
            logger.info(
                "Discarding old-format session_map key %s (superseded by %s)",
                key,
                new_key,
            )
            continue
        info.setdefault("window_name", window_name)
        session_map[new_key] = info
        logger.info("Migrated old-format session_map key %s -> %s", key, new_key)
    return changed


def cleanup_stale_session_map_entries(
    session_map: dict[str, dict],
    live_ids: set[str],
    session_prefix: str,
) -> bool:
    """Remove session_map entries whose window @id is not live.

    Only touches keys that already use @window_id form.
    Returns True if anything was removed.
    """
    prefix = session_prefix if session_prefix.endswith(":") else f"{session_prefix}:"
    stale_keys = [
        key
        for key in session_map
        if key.startswith(prefix)
        and is_window_id(key[len(prefix) :])
        and key[len(prefix) :] not in live_ids
    ]
    for key in stale_keys:
        del session_map[key]
        logger.info("Removed stale session_map entry: %s", key)
    return bool(stale_keys)


def state_needs_legacy_migration(
    window_states: dict[str, Any],
    thread_bindings: dict[int, dict[int, str]],
) -> bool:
    """Detect pre-@id keys still present in persisted state."""
    for k in window_states:
        if not is_window_id(k):
            return True
    for bindings in thread_bindings.values():
        for window_id in bindings.values():
            if not is_window_id(window_id):
                return True
    return False
