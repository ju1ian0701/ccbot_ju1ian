"""Thread binding and group chat ID storage (ISS-005 phase 4a).

Owns the two routing dicts previously held by ``SessionManager``:

  thread_bindings: user_id -> {thread_id -> window_id}
  group_chat_ids: "user_id:thread_id" -> group chat_id (supergroup routing)

plus their (de)serialization in the byte-for-byte ``state.json`` format.
Persist (atomic write) and logging stay with the ``SessionManager``
facade; all methods here are pure in-memory operations.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BindingStore:
    """Owner of thread_bindings and group_chat_ids (ISS-005 4a).

    thread_bindings: user_id -> {thread_id -> window_id}
    group_chat_ids: "user_id:thread_id" -> group chat_id

    IMPORTANT: group_chat_ids is essential for supergroup/forum topic
    support. Telegram Bot API requires the group chat_id (negative number
    like -100xxx) as the chat_id parameter when sending messages to forum
    topics. Using user_id as chat_id will fail with "Message thread not
    found". See: https://core.telegram.org/bots/api#sendmessage
    History: originally added in 5afc111, erroneously removed in 26cb81f,
    restored in PR #23.

    Mutating methods do not persist: the caller (SessionManager facade)
    is responsible for saving state after mutations.
    """

    thread_bindings: dict[int, dict[int, str]] = field(default_factory=dict)
    group_chat_ids: dict[str, int] = field(default_factory=dict)

    # --- state.json (de)serialization (byte-for-byte format) ---

    def to_state(self) -> dict[str, Any]:
        """Serialize owned dicts to state.json sections."""
        return {
            "thread_bindings": {
                str(uid): {str(tid): window_id for tid, window_id in bindings.items()}
                for uid, bindings in self.thread_bindings.items()
            },
            "group_chat_ids": self.group_chat_ids,
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Load owned dicts from a parsed state.json dict."""
        self.thread_bindings = {
            int(uid): {int(tid): window_id for tid, window_id in bindings.items()}
            for uid, bindings in state.get("thread_bindings", {}).items()
        }
        self.group_chat_ids = {
            k: int(v) for k, v in state.get("group_chat_ids", {}).items()
        }

    def reset(self) -> None:
        """Clear both dicts (corrupt-state fallback)."""
        self.thread_bindings = {}
        self.group_chat_ids = {}

    # --- Thread binding operations ---

    def bind_thread(self, user_id: int, thread_id: int, window_id: str) -> None:
        """Bind a Telegram topic thread to a tmux window."""
        if user_id not in self.thread_bindings:
            self.thread_bindings[user_id] = {}
        self.thread_bindings[user_id][thread_id] = window_id

    def unbind_thread(self, user_id: int, thread_id: int) -> str | None:
        """Remove a thread binding. Returns the previously bound window_id, or None."""
        bindings = self.thread_bindings.get(user_id)
        if not bindings or thread_id not in bindings:
            return None
        window_id = bindings.pop(thread_id)
        if not bindings:
            del self.thread_bindings[user_id]
        return window_id

    def get_window_for_thread(self, user_id: int, thread_id: int) -> str | None:
        """Look up the window_id bound to a thread."""
        bindings = self.thread_bindings.get(user_id)
        if not bindings:
            return None
        return bindings.get(thread_id)

    def iter_thread_bindings(self) -> Iterator[tuple[int, int, str]]:
        """Iterate all thread bindings as (user_id, thread_id, window_id)."""
        for user_id, bindings in self.thread_bindings.items():
            for thread_id, window_id in bindings.items():
                yield user_id, thread_id, window_id

    # --- Group chat ID operations (supergroup forum topic routing) ---

    def set_group_chat_id(
        self, user_id: int, thread_id: int | None, chat_id: int
    ) -> bool:
        """Store the group chat_id for a user+thread combination.

        Returns True if the mapping was changed (caller should persist).
        """
        tid = thread_id or 0
        key = f"{user_id}:{tid}"
        if self.group_chat_ids.get(key) != chat_id:
            self.group_chat_ids[key] = chat_id
            return True
        return False

    def resolve_chat_id(self, user_id: int, thread_id: int | None = None) -> int:
        """Resolve the correct chat_id for sending messages.

        Returns the stored group chat_id when a thread_id is present and a
        mapping exists, otherwise falls back to user_id (for private chats).
        """
        if thread_id is not None:
            key = f"{user_id}:{thread_id}"
            group_id = self.group_chat_ids.get(key)
            if group_id is not None:
                return group_id
        return user_id
