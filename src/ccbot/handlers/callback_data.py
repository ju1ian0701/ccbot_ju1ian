"""Callback data constants, typed payloads, and encode/parse helpers.

Defines all CB_* prefixes used for routing callback queries in the bot.
Each prefix identifies a specific action or navigation target.

Provides frozen dataclasses plus encode_*/parse_* so producers and the
callback router avoid ad-hoc string splits while preserving on-wire formats
and Telegram's 64-byte callback_data limit.

Constants:
  - CB_HISTORY_*: History pagination
  - CB_DIR_*: Directory browser navigation
  - CB_WIN_*: Window picker (bind existing unbound window)
  - CB_SCREENSHOT_*: Screenshot refresh
  - CB_ASK_*: Interactive UI navigation (arrows, enter, esc)
  - CB_KEYS_PREFIX: Screenshot control keys (kb:<key_id>:<window>)
"""

from __future__ import annotations

from dataclasses import dataclass

# Telegram Bot API hard limit for callback_data (bytes; we clip by char count
# to match existing call sites that used ``[:64]``).
CALLBACK_DATA_MAX = 64

# History pagination
CB_HISTORY_PREV = "hp:"  # history page older
CB_HISTORY_NEXT = "hn:"  # history page newer

# Directory browser
CB_DIR_SELECT = "db:sel:"
CB_DIR_UP = "db:up"
CB_DIR_CONFIRM = "db:confirm"
CB_DIR_CANCEL = "db:cancel"
CB_DIR_PAGE = "db:page:"

# Window picker (bind existing unbound window)
CB_WIN_BIND = "wb:sel:"  # wb:sel:<index>
CB_WIN_NEW = "wb:new"  # proceed to directory browser
CB_WIN_CANCEL = "wb:cancel"

# Screenshot
CB_SCREENSHOT_REFRESH = "ss:ref:"

# Interactive UI (aq: prefix kept for backward compatibility)
CB_ASK_UP = "aq:up:"  # aq:up:<window>
CB_ASK_DOWN = "aq:down:"  # aq:down:<window>
CB_ASK_LEFT = "aq:left:"  # aq:left:<window>
CB_ASK_RIGHT = "aq:right:"  # aq:right:<window>
CB_ASK_ESC = "aq:esc:"  # aq:esc:<window>
CB_ASK_ENTER = "aq:enter:"  # aq:enter:<window>
CB_ASK_SPACE = "aq:spc:"  # aq:spc:<window>
CB_ASK_TAB = "aq:tab:"  # aq:tab:<window>
CB_ASK_REFRESH = "aq:ref:"  # aq:ref:<window>

# Session picker (resume existing session)
CB_SESSION_SELECT = "rs:sel:"  # rs:sel:<index>
CB_SESSION_NEW = "rs:new"  # start a new session
CB_SESSION_CANCEL = "rs:cancel"  # cancel

# Screenshot control keys
CB_KEYS_PREFIX = "kb:"  # kb:<key_id>:<window>


def clip_callback_data(data: str) -> str:
    """Truncate callback_data to Telegram's size limit."""
    return data[:CALLBACK_DATA_MAX]


# ── Typed payloads ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class HistoryCallback:
    """History pagination payload.

    Wire formats:
      - ``hp|hn:<page>:<window_id>:<start>:<end>`` (current)
      - ``hp|hn:<page>:<window_id>`` (legacy, start/end default to 0)
    """

    older: bool  # True → CB_HISTORY_PREV, False → CB_HISTORY_NEXT
    page: int
    window_id: str
    start_byte: int = 0
    end_byte: int = 0


@dataclass(frozen=True)
class IndexCallback:
    """Integer index payload (dir/window/session pickers)."""

    index: int


@dataclass(frozen=True)
class WindowCallback:
    """Callback that carries only a tmux window id after a prefix."""

    window_id: str


@dataclass(frozen=True)
class KeyCallback:
    """Screenshot control key: ``kb:<key_id>:<window_id>``."""

    key_id: str
    window_id: str


# ── History ──────────────────────────────────────────────────────────────


def encode_history(cb: HistoryCallback) -> str:
    """Encode history pagination callback_data (always new format)."""
    prefix = CB_HISTORY_PREV if cb.older else CB_HISTORY_NEXT
    return clip_callback_data(
        f"{prefix}{cb.page}:{cb.window_id}:{cb.start_byte}:{cb.end_byte}"
    )


def encode_history_page(
    *,
    older: bool,
    page: int,
    window_id: str,
    start_byte: int = 0,
    end_byte: int = 0,
) -> str:
    """Convenience builder for history prev/next buttons."""
    return encode_history(
        HistoryCallback(
            older=older,
            page=page,
            window_id=window_id,
            start_byte=start_byte,
            end_byte=end_byte,
        )
    )


def parse_history(data: str) -> HistoryCallback | None:
    """Parse history pagination callback_data; None if not history or invalid."""
    if data.startswith(CB_HISTORY_PREV):
        older = True
        rest = data[len(CB_HISTORY_PREV) :]
    elif data.startswith(CB_HISTORY_NEXT):
        older = False
        rest = data[len(CB_HISTORY_NEXT) :]
    else:
        return None

    try:
        parts = rest.split(":")
        if len(parts) < 4:
            # Legacy: page:window_id (window_id may contain colons)
            page_str, window_id = rest.split(":", 1)
            return HistoryCallback(
                older=older,
                page=int(page_str),
                window_id=window_id,
                start_byte=0,
                end_byte=0,
            )
        # Current: page:window_id:start:end (window_id may contain colons)
        page = int(parts[0])
        start_byte = int(parts[-2])
        end_byte = int(parts[-1])
        window_id = ":".join(parts[1:-2])
        return HistoryCallback(
            older=older,
            page=page,
            window_id=window_id,
            start_byte=start_byte,
            end_byte=end_byte,
        )
    except (ValueError, IndexError):
        return None


# ── Index-based (dir / window / session pickers) ─────────────────────────


def encode_dir_select(index: int) -> str:
    return clip_callback_data(f"{CB_DIR_SELECT}{index}")


def parse_dir_select(data: str) -> IndexCallback | None:
    return _parse_index(data, CB_DIR_SELECT)


def encode_dir_page(page: int) -> str:
    return clip_callback_data(f"{CB_DIR_PAGE}{page}")


def parse_dir_page(data: str) -> IndexCallback | None:
    return _parse_index(data, CB_DIR_PAGE)


def encode_win_bind(index: int) -> str:
    return clip_callback_data(f"{CB_WIN_BIND}{index}")


def parse_win_bind(data: str) -> IndexCallback | None:
    return _parse_index(data, CB_WIN_BIND)


def encode_session_select(index: int) -> str:
    return clip_callback_data(f"{CB_SESSION_SELECT}{index}")


def parse_session_select(data: str) -> IndexCallback | None:
    return _parse_index(data, CB_SESSION_SELECT)


def _parse_index(data: str, prefix: str) -> IndexCallback | None:
    if not data.startswith(prefix):
        return None
    try:
        return IndexCallback(index=int(data[len(prefix) :]))
    except ValueError:
        return None


# ── Window-id suffix (screenshot + interactive UI) ───────────────────────


def encode_screenshot_refresh(window_id: str) -> str:
    return clip_callback_data(f"{CB_SCREENSHOT_REFRESH}{window_id}")


def parse_screenshot_refresh(data: str) -> WindowCallback | None:
    return _parse_window_suffix(data, CB_SCREENSHOT_REFRESH)


def encode_ask(prefix: str, window_id: str) -> str:
    """Encode an interactive-UI action: ``<prefix><window_id>``."""
    return clip_callback_data(f"{prefix}{window_id}")


def parse_ask(data: str, prefix: str) -> WindowCallback | None:
    """Parse interactive-UI action for a known prefix."""
    return _parse_window_suffix(data, prefix)


def _parse_window_suffix(data: str, prefix: str) -> WindowCallback | None:
    if not data.startswith(prefix):
        return None
    window_id = data[len(prefix) :]
    if not window_id:
        return None
    return WindowCallback(window_id=window_id)


# ── Screenshot control keys ──────────────────────────────────────────────


def encode_key(key_id: str, window_id: str) -> str:
    return clip_callback_data(f"{CB_KEYS_PREFIX}{key_id}:{window_id}")


def parse_key(data: str) -> KeyCallback | None:
    """Parse ``kb:<key_id>:<window_id>``; window_id may contain colons."""
    if not data.startswith(CB_KEYS_PREFIX):
        return None
    rest = data[len(CB_KEYS_PREFIX) :]
    colon_idx = rest.find(":")
    if colon_idx < 0:
        return None
    key_id = rest[:colon_idx]
    window_id = rest[colon_idx + 1 :]
    if not key_id or not window_id:
        return None
    return KeyCallback(key_id=key_id, window_id=window_id)
