"""Window state and display name storage (ISS-005 phase 4b).

Owns the two window-related dicts previously held by ``SessionManager``:

  window_states: window_id -> WindowState (session_id, cwd, window_name)
  window_display_names: window_id -> window_name (for display)

plus their (de)serialization in the byte-for-byte ``state.json`` format.
Persist (atomic write) and logging stay with the ``SessionManager``
facade; all methods here are pure in-memory operations.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WindowState:
    """Persistent state for a tmux window.

    Attributes:
        session_id: Associated Claude session ID (empty if not yet detected)
        cwd: Working directory for direct file path construction
        window_name: Display name of the window
    """

    session_id: str = ""
    cwd: str = ""
    window_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "session_id": self.session_id,
            "cwd": self.cwd,
        }
        if self.window_name:
            d["window_name"] = self.window_name
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WindowState":
        return cls(
            session_id=data.get("session_id", ""),
            cwd=data.get("cwd", ""),
            window_name=data.get("window_name", ""),
        )


@dataclass
class WindowStateStore:
    """Owner of window_states and window_display_names (ISS-005 4b).

    Mutating methods do not persist: the caller (SessionManager facade)
    is responsible for saving state after mutations.
    """

    window_states: dict[str, WindowState] = field(default_factory=dict)
    # window_id -> display name (window_name)
    window_display_names: dict[str, str] = field(default_factory=dict)

    # --- state.json (de)serialization (byte-for-byte format) ---

    def to_state(self) -> dict[str, Any]:
        """Serialize owned dicts to state.json sections."""
        return {
            "window_states": {k: v.to_dict() for k, v in self.window_states.items()},
            "window_display_names": self.window_display_names,
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Load owned dicts from a parsed state.json dict."""
        self.window_states = {
            k: WindowState.from_dict(v)
            for k, v in state.get("window_states", {}).items()
        }
        self.window_display_names = state.get("window_display_names", {})

    def reset(self) -> None:
        """Clear both dicts (corrupt-state fallback)."""
        self.window_states = {}
        self.window_display_names = {}

    # --- Window state operations ---

    def get_window_state(self, window_id: str) -> WindowState:
        """Get or create window state."""
        if window_id not in self.window_states:
            self.window_states[window_id] = WindowState()
        return self.window_states[window_id]

    def set_window_session(
        self, window_id: str, session_id: str, cwd: str = "", window_name: str = ""
    ) -> None:
        """Set session association; non-empty cwd/window_name update fields."""
        state = self.get_window_state(window_id)
        state.session_id = session_id
        if cwd:
            state.cwd = cwd
        if window_name:
            state.window_name = window_name

    def clear_window_session(self, window_id: str) -> None:
        """Clear session association for a window (e.g., after /clear command)."""
        state = self.get_window_state(window_id)
        state.session_id = ""

    # --- Display name operations ---

    def get_display_name(self, window_id: str) -> str:
        """Get display name for a window_id, fallback to window_id itself."""
        return self.window_display_names.get(window_id, window_id)

    def update_display_name(self, window_id: str, new_name: str) -> None:
        """Update display name for a window (and WindowState.window_name)."""
        self.window_display_names[window_id] = new_name
        # Also update WindowState.window_name if it exists
        if window_id in self.window_states:
            self.window_states[window_id].window_name = new_name
