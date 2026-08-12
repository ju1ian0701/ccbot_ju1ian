"""Claude Code session management — the core state hub.

Manages the key mappings:
  Window→Session (window_states): which Claude session_id a window holds (keyed by window_id).
  User→Thread→Window (thread_bindings): topic-to-window bindings (1 topic = 1 window_id).

Responsibilities:
  - Persist/load state to ~/.ccbot/state.json.
  - Sync window↔session bindings from session_map.json (written by hook).
  - Resolve window IDs to ClaudeSession objects (JSONL file reading).
  - Track per-user read offsets for unread-message detection.
  - Manage thread↔window bindings for Telegram topic routing.
  - Send keystrokes to tmux windows and retrieve message history.
  - Maintain window_id→display name mapping for UI display.
  - Re-resolve stale window IDs on startup (tmux server restart recovery).

Key class: SessionManager (singleton instantiated as `session_manager`).
Key methods for thread binding access:
  - resolve_window_for_thread: Get window_id for a user's thread
  - iter_thread_bindings: Generator for iterating all (user_id, thread_id, window_id)
  - find_users_for_session: Find all users bound to a session_id
"""

import asyncio
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from .binding_store import BindingStore
from .config import config
from .session_map_repository import SessionMapRepository
from .session_migration import (
    apply_startup_state_migration,
    cleanup_stale_session_map_entries,
    is_window_id,
    migrate_session_map_old_format,
    state_needs_legacy_migration,
)
from .tmux_manager import tmux_manager
from .transcript_reader import ClaudeSession, TranscriptReader
from .utils import atomic_write_json
from .window_state_store import WindowState, WindowStateStore

logger = logging.getLogger(__name__)


@dataclass
class SessionManager:
    """Manages session state for Claude Code.

    All internal keys use window_id (e.g. '@0', '@12') for uniqueness.
    Display names (window_name) are stored separately for UI presentation.

    window_states: window_id -> WindowState (session_id, cwd, window_name)
    user_window_offsets: user_id -> {window_id -> byte_offset}
    thread_bindings: user_id -> {thread_id -> window_id}
    window_display_names: window_id -> window_name (for display)
    group_chat_ids: "user_id:thread_id" -> group chat_id (for supergroup routing)
    """

    user_window_offsets: dict[int, dict[str, int]] = field(default_factory=dict)
    # thread_bindings and group_chat_ids are owned by BindingStore (ISS-005 4a),
    # window_states and window_display_names by WindowStateStore (ISS-005 4b),
    # session_map.json file access by SessionMapRepository (ISS-005 4c),
    # transcript JSONL reads by TranscriptReader (ISS-005 4d);
    # facade property delegates below keep backward-compatible access.
    _bindings: BindingStore = field(default_factory=BindingStore, repr=False)
    _window_store: WindowStateStore = field(
        default_factory=WindowStateStore, repr=False
    )
    _session_map: SessionMapRepository = field(
        default_factory=SessionMapRepository, repr=False
    )
    _transcripts: TranscriptReader = field(default_factory=TranscriptReader, repr=False)

    def __post_init__(self) -> None:
        self._load_state()

    # --- BindingStore delegates (ISS-005 4a) ---

    @property
    def thread_bindings(self) -> dict[int, dict[int, str]]:
        """Thread bindings (user_id -> {thread_id -> window_id}), owned by BindingStore."""
        return self._bindings.thread_bindings

    @thread_bindings.setter
    def thread_bindings(self, value: dict[int, dict[int, str]]) -> None:
        self._bindings.thread_bindings = value

    @property
    def group_chat_ids(self) -> dict[str, int]:
        """Group chat IDs ("user_id:thread_id" -> chat_id), owned by BindingStore.

        IMPORTANT: This mapping is essential for supergroup/forum topic support.
        Telegram Bot API requires group chat_id (negative number like -100xxx)
        as the chat_id parameter when sending messages to forum topics.
        Using user_id as chat_id will fail with "Message thread not found".
        See: https://core.telegram.org/bots/api#sendmessage
        History: originally added in 5afc111, erroneously removed in 26cb81f,
        restored in PR #23.
        """
        return self._bindings.group_chat_ids

    @group_chat_ids.setter
    def group_chat_ids(self, value: dict[str, int]) -> None:
        self._bindings.group_chat_ids = value

    # --- WindowStateStore delegates (ISS-005 4b) ---

    @property
    def window_states(self) -> dict[str, WindowState]:
        """Window states (window_id -> WindowState), owned by WindowStateStore."""
        return self._window_store.window_states

    @window_states.setter
    def window_states(self, value: dict[str, WindowState]) -> None:
        self._window_store.window_states = value

    @property
    def window_display_names(self) -> dict[str, str]:
        """Display names (window_id -> window_name), owned by WindowStateStore."""
        return self._window_store.window_display_names

    @window_display_names.setter
    def window_display_names(self, value: dict[str, str]) -> None:
        self._window_store.window_display_names = value

    def _save_state(self) -> None:
        bindings_state = self._bindings.to_state()
        window_state = self._window_store.to_state()
        state: dict[str, Any] = {
            "window_states": window_state["window_states"],
            "user_window_offsets": {
                str(uid): offsets for uid, offsets in self.user_window_offsets.items()
            },
            "thread_bindings": bindings_state["thread_bindings"],
            "window_display_names": window_state["window_display_names"],
            "group_chat_ids": bindings_state["group_chat_ids"],
        }
        atomic_write_json(config.state_file, state)
        logger.debug("State saved to %s", config.state_file)

    def _is_window_id(self, key: str) -> bool:
        """Check if a key looks like a tmux window ID (e.g. '@0', '@12')."""
        return is_window_id(key)

    def _load_state(self) -> None:
        """Load state synchronously during initialization.

        Detects old-format state (window_name keys without '@' prefix) and
        logs that startup re-resolution will migrate them once to @id keys.
        """
        if config.state_file.exists():
            try:
                state = json.loads(config.state_file.read_text())
                self._window_store.load_state(state)
                self.user_window_offsets = {
                    int(uid): offsets
                    for uid, offsets in state.get("user_window_offsets", {}).items()
                }
                self._bindings.load_state(state)

                if state_needs_legacy_migration(
                    self.window_states, self.thread_bindings
                ):
                    logger.info(
                        "Detected old-format state (window_name keys), "
                        "will re-resolve on startup"
                    )

            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("Failed to load state: %s", e)
                self._window_store.reset()
                self.user_window_offsets = {}
                self._bindings.reset()

    async def resolve_stale_ids(self) -> None:
        """Re-resolve persisted window IDs against live tmux windows.

        Thin orchestrator: lists live windows, applies pure
        :func:`apply_startup_state_migration`, persists, then cleans
        session_map.json. Canonical routing keys are always ``@N``.
        """
        windows = await tmux_manager.list_windows()
        live_by_name: dict[str, str] = {}
        live_ids: set[str] = set()
        for w in windows:
            live_by_name[w.window_name] = w.window_id
            live_ids.add(w.window_id)

        result = apply_startup_state_migration(
            window_states=self.window_states,
            thread_bindings=self.thread_bindings,
            user_window_offsets=self.user_window_offsets,
            window_display_names=self.window_display_names,
            live_by_name=live_by_name,
            live_ids=live_ids,
        )
        self.window_states = result.window_states  # type: ignore[assignment]
        self.thread_bindings = result.thread_bindings
        self.user_window_offsets = result.user_window_offsets
        self.window_display_names = result.window_display_names

        if result.changed:
            self._save_state()
            logger.info("Startup re-resolution complete")

        await self._cleanup_stale_session_map_entries(live_ids)
        await self._migrate_old_format_session_map_keys(live_by_name)

    def _migrate_old_format_map(
        self, session_map: dict[str, dict], live_by_name: dict[str, str]
    ) -> bool:
        """Migrate old-format session_map keys to @window_id (delegates to pure helper)."""
        return migrate_session_map_old_format(
            session_map,
            live_by_name,
            f"{config.tmux_session_name}:",
        )

    def _mutate_session_map_locked(
        self, mutate: Callable[[dict[str, dict]], bool]
    ) -> bool:
        """Read-modify-write session_map.json under the hook's flock (ISS-005 4c)."""
        return self._session_map.mutate_locked(mutate)

    async def override_session_map_entry(
        self, window_id: str, session_id: str, cwd: str = "", window_name: str = ""
    ) -> None:
        """Force a window's session_map entry to a specific session_id.

        Used after `--resume`: session_map drives both the monitor's watch
        list and load_session_map()'s sync into window_states, so overriding
        window_state alone would be reverted on the next poll cycle. Creates
        the entry if missing (hook timed out); no-op if already consistent.
        """
        key = f"{config.tmux_session_name}:{window_id}"

        def mutate(session_map: dict[str, dict]) -> bool:
            info = session_map.get(key)
            if info is None:
                session_map[key] = {
                    "session_id": session_id,
                    "cwd": cwd,
                    "window_name": window_name,
                }
                return True
            if info.get("session_id") == session_id:
                return False
            info["session_id"] = session_id
            return True

        if await asyncio.to_thread(self._mutate_session_map_locked, mutate):
            logger.info("session_map override: %s -> session_id=%s", key, session_id)

    async def _migrate_old_format_session_map_keys(
        self, live_by_name: dict[str, str]
    ) -> None:
        """Migrate old-format keys in session_map.json to @window_id form (startup)."""
        if not config.session_map_file.exists():
            return
        changed = await asyncio.to_thread(
            self._mutate_session_map_locked,
            lambda session_map: self._migrate_old_format_map(session_map, live_by_name),
        )
        if changed:
            logger.info("Migrated old-format session_map keys to @window_id form")

    async def _cleanup_stale_session_map_entries(self, live_ids: set[str]) -> None:
        """Remove entries for tmux windows that no longer exist.

        When windows are closed externally (outside ccbot), session_map.json
        retains orphan references. This cleanup removes entries whose window_id
        is not in the current set of live tmux windows.
        """
        if not config.session_map_file.exists():
            return

        prefix = f"{config.tmux_session_name}:"

        def mutate(session_map: dict[str, dict]) -> bool:
            return cleanup_stale_session_map_entries(session_map, live_ids, prefix)

        if await asyncio.to_thread(self._mutate_session_map_locked, mutate):
            logger.info(
                "Cleaned up stale session_map entries (windows no longer in tmux)"
            )

    # --- Display name management ---

    def get_display_name(self, window_id: str) -> str:
        """Get display name for a window_id, fallback to window_id itself."""
        return self._window_store.get_display_name(window_id)

    def update_display_name(self, window_id: str, new_name: str) -> None:
        """Update the display name for a window and persist state."""
        self._window_store.update_display_name(window_id, new_name)
        self._save_state()
        logger.info("Updated display name: window_id %s -> '%s'", window_id, new_name)

    # --- Group chat ID management (supergroup forum topic routing) ---

    def set_group_chat_id(
        self, user_id: int, thread_id: int | None, chat_id: int
    ) -> None:
        """Store the group chat_id for a user+thread combination.

        In supergroups with forum topics, messages must be sent to the group's
        chat_id (negative number like -100xxx) rather than the user's personal ID.
        Telegram's Bot API rejects message_thread_id when chat_id is a private
        user ID — the thread only exists within the group context.

        DO NOT REMOVE this method or the group_chat_ids mapping.
        Without it, all outbound messages in forum topics fail with
        "Message thread not found". See commit history: 5afc111 → 26cb81f → PR #23.
        """
        if self._bindings.set_group_chat_id(user_id, thread_id, chat_id):
            self._save_state()
            logger.debug(
                "Stored group chat_id: user=%d, thread=%s, chat_id=%d",
                user_id,
                thread_id,
                chat_id,
            )

    def resolve_chat_id(self, user_id: int, thread_id: int | None = None) -> int:
        """Resolve the correct chat_id for sending messages.

        Returns the stored group chat_id when a thread_id is present and a
        mapping exists, otherwise falls back to user_id (for private chats).

        Every outbound Telegram API call (send_message, edit_message_text,
        delete_message, send_chat_action, edit_forum_topic, etc.) MUST use
        this method instead of raw user_id. Using user_id directly breaks
        supergroup forum topic routing.
        """
        return self._bindings.resolve_chat_id(user_id, thread_id)

    async def wait_for_session_map_entry(
        self, window_id: str, timeout: float = 5.0, interval: float = 0.5
    ) -> bool:
        """Poll session_map.json until an entry for window_id appears.

        Returns True if the entry was found within timeout, False otherwise.
        """
        logger.debug(
            "Waiting for session_map entry: window_id=%s, timeout=%.1f",
            window_id,
            timeout,
        )
        key = f"{config.tmux_session_name}:{window_id}"
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            session_map = await self._session_map.read()
            if session_map is not None:
                info = session_map.get(key, {})
                if info.get("session_id"):
                    # Found — load into window_states immediately
                    logger.debug("session_map entry found for window_id %s", window_id)
                    await self.load_session_map()
                    return True
            await asyncio.sleep(interval)
        logger.warning(
            "Timed out waiting for session_map entry: window_id=%s", window_id
        )
        return False

    async def load_session_map(self) -> None:
        """Read session_map.json and update window_states with new session associations.

        Keys in session_map are formatted as "tmux_session:window_id" (e.g. "ccbot:@12").
        Only entries matching our tmux_session_name are processed.
        Also cleans up window_states entries not in current session_map.
        Updates window_display_names from the "window_name" field in values.
        """
        session_map = await self._session_map.read()
        if session_map is None:
            return

        prefix = f"{config.tmux_session_name}:"

        # Self-heal old-format keys (session:window_name) that an outdated hook
        # may write at runtime: resolve them against live windows and rewrite to
        # @window_id in place, so the delivery loop below can see them. Only
        # lists tmux windows when such keys are actually present (zero cost in
        # steady state). Mirrors the tolerance already in _load_current_session_map.
        if any(
            k.startswith(prefix) and not self._is_window_id(k[len(prefix) :])
            for k in session_map
        ):
            windows = await tmux_manager.list_windows()
            live_by_name = {w.window_name: w.window_id for w in windows}
            if self._migrate_old_format_map(session_map, live_by_name):
                self._session_map.write(session_map)
                logger.info("Migrated old-format session_map keys during load")

        valid_wids: set[str] = set()
        changed = False

        for key, info in session_map.items():
            # Only process entries for our tmux session
            if not key.startswith(prefix):
                continue
            window_id = key[len(prefix) :]
            if not self._is_window_id(window_id):
                continue
            valid_wids.add(window_id)
            new_sid = info.get("session_id", "")
            new_cwd = info.get("cwd", "")
            new_wname = info.get("window_name", "")
            if not new_sid:
                continue
            state = self.get_window_state(window_id)
            if state.session_id != new_sid or state.cwd != new_cwd:
                logger.info(
                    "Session map: window_id %s updated sid=%s, cwd=%s",
                    window_id,
                    new_sid,
                    new_cwd,
                )
                state.session_id = new_sid
                state.cwd = new_cwd
                changed = True
            # Update display name
            if new_wname:
                state.window_name = new_wname
                if self.window_display_names.get(window_id) != new_wname:
                    self.window_display_names[window_id] = new_wname
                    changed = True

        # Clean up window_states entries not in current session_map.
        stale_wids = [w for w in self.window_states if w and w not in valid_wids]
        for window_id in stale_wids:
            logger.info("Removing stale window_state: %s", window_id)
            del self.window_states[window_id]
            changed = True

        if changed:
            self._save_state()

    # --- Window state management ---

    def get_window_state(self, window_id: str) -> WindowState:
        """Get or create window state."""
        return self._window_store.get_window_state(window_id)

    def clear_window_session(self, window_id: str) -> None:
        """Clear session association for a window (e.g., after /clear command)."""
        self._window_store.clear_window_session(window_id)
        self._save_state()
        logger.info("Cleared session for window_id %s", window_id)

    def set_window_session(
        self, window_id: str, session_id: str, cwd: str = "", window_name: str = ""
    ) -> None:
        """Set a window's session association and persist state (ISS-009).

        Public write API for the window→session mapping; non-empty
        ``cwd``/``window_name`` update the corresponding fields.
        ``_save_state`` stays private.
        """
        self._window_store.set_window_session(window_id, session_id, cwd, window_name)
        self._save_state()

    # --- Directory session listing (ISS-005 4d delegate) ---

    async def list_sessions_for_directory(self, cwd: str) -> list[ClaudeSession]:
        """List existing Claude sessions for a directory.

        Encodes the cwd path to find the project directory under
        ~/.claude/projects/{encoded_cwd}/, globs *.jsonl files, and
        extracts summary info from each.

        Returns a list sorted by mtime (most recent first), capped at 10.
        """
        return await self._transcripts.list_sessions_for_directory(cwd)

    # --- Window → Session resolution ---

    async def resolve_session_for_window(self, window_id: str) -> ClaudeSession | None:
        """Resolve a tmux window to the best matching Claude session.

        Uses persisted session_id + cwd to construct file path directly.
        Returns None if no session is associated with this window.
        """
        state = self.get_window_state(window_id)

        if not state.session_id or not state.cwd:
            return None

        session = await self._transcripts.get_session_direct(
            state.session_id, state.cwd
        )
        if session:
            return session

        # File no longer exists, clear state
        logger.warning(
            "Session file no longer exists for window_id %s (sid=%s, cwd=%s)",
            window_id,
            state.session_id,
            state.cwd,
        )
        state.session_id = ""
        state.cwd = ""
        self._save_state()
        return None

    # --- User window offset management ---

    def update_user_window_offset(
        self, user_id: int, window_id: str, offset: int
    ) -> None:
        """Update the user's last read offset for a window."""
        if user_id not in self.user_window_offsets:
            self.user_window_offsets[user_id] = {}
        self.user_window_offsets[user_id][window_id] = offset
        self._save_state()

    # --- Thread binding management ---

    def bind_thread(
        self, user_id: int, thread_id: int, window_id: str, window_name: str = ""
    ) -> None:
        """Bind a Telegram topic thread to a tmux window.

        Args:
            user_id: Telegram user ID
            thread_id: Telegram topic thread ID
            window_id: Tmux window ID (e.g. '@0')
            window_name: Display name for the window (optional)
        """
        self._bindings.bind_thread(user_id, thread_id, window_id)
        if window_name:
            self.window_display_names[window_id] = window_name
        self._save_state()
        display = window_name or self.get_display_name(window_id)
        logger.info(
            "Bound thread %d -> window_id %s (%s) for user %d",
            thread_id,
            window_id,
            display,
            user_id,
        )

    def unbind_thread(self, user_id: int, thread_id: int) -> str | None:
        """Remove a thread binding. Returns the previously bound window_id, or None."""
        window_id = self._bindings.unbind_thread(user_id, thread_id)
        if window_id is None:
            return None
        self._save_state()
        logger.info(
            "Unbound thread %d (was %s) for user %d",
            thread_id,
            window_id,
            user_id,
        )
        return window_id

    def get_window_for_thread(self, user_id: int, thread_id: int) -> str | None:
        """Look up the window_id bound to a thread."""
        return self._bindings.get_window_for_thread(user_id, thread_id)

    def resolve_window_for_thread(
        self,
        user_id: int,
        thread_id: int | None,
    ) -> str | None:
        """Resolve the tmux window_id for a user's thread.

        Returns None if thread_id is None or the thread is not bound.
        """
        if thread_id is None:
            return None
        return self.get_window_for_thread(user_id, thread_id)

    def iter_thread_bindings(self) -> Iterator[tuple[int, int, str]]:
        """Iterate all thread bindings as (user_id, thread_id, window_id).

        Provides encapsulated access to thread_bindings without exposing
        the internal data structure directly.
        """
        yield from self._bindings.iter_thread_bindings()

    async def find_users_for_session(
        self,
        session_id: str,
    ) -> list[tuple[int, str, int]]:
        """Find all users whose thread-bound window maps to the given session_id.

        Returns list of (user_id, window_id, thread_id) tuples.
        """
        result: list[tuple[int, str, int]] = []
        for user_id, thread_id, window_id in self.iter_thread_bindings():
            # In-memory lookup only: window_states carries the authoritative
            # window→session mapping (synced from session_map each poll cycle).
            # Reading the JSONL here (resolve_session_for_window) would be
            # O(bindings × file size) on every incoming message.
            state = self.window_states.get(window_id)
            if state and state.session_id == session_id:
                result.append((user_id, window_id, thread_id))
        return result

    # --- Tmux helpers ---

    async def send_to_window(self, window_id: str, text: str) -> tuple[bool, str]:
        """Send text to a tmux window by ID."""
        display = self.get_display_name(window_id)
        logger.debug(
            "send_to_window: window_id=%s (%s), text_len=%d",
            window_id,
            display,
            len(text),
        )
        window = await tmux_manager.find_window_by_id(window_id)
        if not window:
            return False, "Window not found (may have been closed)"
        success = await tmux_manager.send_keys(window.window_id, text)
        if success:
            return True, f"Sent to {display}"
        return False, "Failed to send keys"

    # --- Message history ---

    async def get_recent_messages(
        self,
        window_id: str,
        *,
        start_byte: int = 0,
        end_byte: int | None = None,
    ) -> tuple[list[dict], int]:
        """Get user/assistant messages for a window's session.

        Resolves window → session, then reads the JSONL.
        Supports byte range filtering via start_byte/end_byte.
        Returns (messages, total_count).
        """
        session = await self.resolve_session_for_window(window_id)
        if not session or not session.file_path:
            return [], 0

        return await self._transcripts.read_recent_messages(
            session.file_path, start_byte=start_byte, end_byte=end_byte
        )


session_manager = SessionManager()
