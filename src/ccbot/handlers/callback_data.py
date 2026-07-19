"""Callback data constants, typed payloads, and encode/parse helpers.

Defines all CB_* prefixes used for routing callback queries in the bot.
Each prefix identifies a specific action or navigation target.

Typed frozen dataclasses replace ad-hoc string splits. Wire formats are
unchanged for backward compatibility with existing Telegram keyboards:

  - History: ``hp|hn:<page>:<window_id>:<start>:<end>`` (legacy without range)
  - Dir select/page: ``db:sel:<index>``, ``db:page:<page>``
  - Window/session pickers: ``wb:sel:<index>``, ``rs:sel:<index>``
  - Screenshot: ``ss:ref:<window>``, keys ``kb:<key_id>:<window>``
  - Interactive UI: ``aq:<action>:<window>``

Always pass payloads through ``safe_callback_data`` when building
InlineKeyboardButton — never silent ``[:64]`` truncation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

# Telegram Bot API hard limit for callback_data (UTF-8 bytes).
MAX_CALLBACK_DATA_BYTES = 64
CALLBACK_DATA_MAX = MAX_CALLBACK_DATA_BYTES  # alias

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

# Known interactive-UI prefixes (for validation / docs)
CB_ASK_PREFIXES = frozenset(
    {
        CB_ASK_UP,
        CB_ASK_DOWN,
        CB_ASK_LEFT,
        CB_ASK_RIGHT,
        CB_ASK_ESC,
        CB_ASK_ENTER,
        CB_ASK_SPACE,
        CB_ASK_TAB,
        CB_ASK_REFRESH,
    }
)


# ── Typed callback dataclasses ───────────────────────────────────────────


@dataclass(frozen=True)
class HistoryCallback:
    """History pagination: ``hp|hn:<page>:<window_id>:<start>:<end>``."""

    older: bool  # True → CB_HISTORY_PREV, False → CB_HISTORY_NEXT
    page: int
    window_id: str
    start_byte: int = 0
    end_byte: int = 0

    def __post_init__(self) -> None:
        if self.page < 0:
            raise ValueError(f"page must be non-negative, got {self.page}")
        if not self.window_id:
            raise ValueError("window_id must be non-empty")
        if self.start_byte < 0 or self.end_byte < 0:
            raise ValueError("byte range must be non-negative")

    def to_old_string(self) -> str:
        """Serialize to on-wire format (no length check)."""
        prefix = CB_HISTORY_PREV if self.older else CB_HISTORY_NEXT
        return f"{prefix}{self.page}:{self.window_id}:{self.start_byte}:{self.end_byte}"


@dataclass(frozen=True)
class DirSelectCallback:
    """Directory browser entry: ``db:sel:<index>`` (index into cached list)."""

    index: int

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError(f"index must be non-negative, got {self.index}")

    def to_old_string(self) -> str:
        return f"{CB_DIR_SELECT}{self.index}"


@dataclass(frozen=True)
class DirPageCallback:
    """Directory browser page: ``db:page:<page>``."""

    page: int

    def __post_init__(self) -> None:
        if self.page < 0:
            raise ValueError(f"page must be non-negative, got {self.page}")

    def to_old_string(self) -> str:
        return f"{CB_DIR_PAGE}{self.page}"


@dataclass(frozen=True)
class WinBindCallback:
    """Window picker: ``wb:sel:<index>`` (index into unbound windows cache)."""

    index: int

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError(f"index must be non-negative, got {self.index}")

    def to_old_string(self) -> str:
        return f"{CB_WIN_BIND}{self.index}"


@dataclass(frozen=True)
class SessionSelectCallback:
    """Session picker: ``rs:sel:<index>`` (index into sessions cache)."""

    index: int

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError(f"index must be non-negative, got {self.index}")

    def to_old_string(self) -> str:
        return f"{CB_SESSION_SELECT}{self.index}"


@dataclass(frozen=True)
class AskCallback:
    """Interactive UI action: ``aq:<action>:<window_id>``."""

    prefix: str
    window_id: str

    def __post_init__(self) -> None:
        if self.prefix not in CB_ASK_PREFIXES:
            raise ValueError(f"unknown ask prefix: {self.prefix!r}")
        if not self.window_id:
            raise ValueError("window_id must be non-empty")

    def to_old_string(self) -> str:
        return f"{self.prefix}{self.window_id}"


@dataclass(frozen=True)
class ScreenshotRefreshCallback:
    """Screenshot refresh: ``ss:ref:<window_id>``."""

    window_id: str

    def __post_init__(self) -> None:
        if not self.window_id:
            raise ValueError("window_id must be non-empty")

    def to_old_string(self) -> str:
        return f"{CB_SCREENSHOT_REFRESH}{self.window_id}"


@dataclass(frozen=True)
class KeyCallback:
    """Screenshot control key: ``kb:<key_id>:<window_id>``."""

    key_id: str
    window_id: str

    def __post_init__(self) -> None:
        if not self.key_id:
            raise ValueError("key_id must be non-empty")
        if not self.window_id:
            raise ValueError("window_id must be non-empty")

    def to_old_string(self) -> str:
        return f"{CB_KEYS_PREFIX}{self.key_id}:{self.window_id}"


# Union of typed payloads (plus plain str for constants like CB_DIR_UP / "noop").
CallbackPayload = Union[
    str,
    HistoryCallback,
    DirSelectCallback,
    DirPageCallback,
    WinBindCallback,
    SessionSelectCallback,
    AskCallback,
    ScreenshotRefreshCallback,
    KeyCallback,
]


def safe_callback_data(cb: CallbackPayload) -> str:
    """Serialize callback_data with Telegram 64-byte UTF-8 limit check.

    Accepts a wire string or a typed payload with ``to_old_string()``.
    Raises ``ValueError`` on empty or oversized data — never truncates.
    """
    if isinstance(cb, str):
        data = cb
    else:
        data = cb.to_old_string()

    if not data:
        raise ValueError("callback_data must be non-empty")

    encoded_len = len(data.encode("utf-8"))
    if encoded_len > MAX_CALLBACK_DATA_BYTES:
        raise ValueError(
            f"callback_data too long ({encoded_len}B > {MAX_CALLBACK_DATA_BYTES}B): "
            f"{data!r}"
        )
    return data


# ── History ──────────────────────────────────────────────────────────────


def encode_history(cb: HistoryCallback) -> str:
    return safe_callback_data(cb)


def encode_history_page(
    *,
    older: bool,
    page: int,
    window_id: str,
    start_byte: int = 0,
    end_byte: int = 0,
) -> str:
    return safe_callback_data(
        HistoryCallback(
            older=older,
            page=page,
            window_id=window_id,
            start_byte=start_byte,
            end_byte=end_byte,
        )
    )


def parse_history(data: str) -> HistoryCallback | None:
    """Parse history callback_data; None if not history or invalid."""
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
        # Current: page:window_id:start:end
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


# ── Index-based pickers ──────────────────────────────────────────────────


def encode_dir_select(index: int) -> str:
    return safe_callback_data(DirSelectCallback(index=index))


def parse_dir_select(data: str) -> DirSelectCallback | None:
    if not data.startswith(CB_DIR_SELECT):
        return None
    try:
        return DirSelectCallback(index=int(data[len(CB_DIR_SELECT) :]))
    except ValueError:
        return None


def encode_dir_page(page: int) -> str:
    return safe_callback_data(DirPageCallback(page=page))


def parse_dir_page(data: str) -> DirPageCallback | None:
    if not data.startswith(CB_DIR_PAGE):
        return None
    try:
        return DirPageCallback(page=int(data[len(CB_DIR_PAGE) :]))
    except ValueError:
        return None


def encode_win_bind(index: int) -> str:
    return safe_callback_data(WinBindCallback(index=index))


def parse_win_bind(data: str) -> WinBindCallback | None:
    if not data.startswith(CB_WIN_BIND):
        return None
    try:
        return WinBindCallback(index=int(data[len(CB_WIN_BIND) :]))
    except ValueError:
        return None


def encode_session_select(index: int) -> str:
    return safe_callback_data(SessionSelectCallback(index=index))


def parse_session_select(data: str) -> SessionSelectCallback | None:
    if not data.startswith(CB_SESSION_SELECT):
        return None
    try:
        return SessionSelectCallback(index=int(data[len(CB_SESSION_SELECT) :]))
    except ValueError:
        return None


# ── Window-id suffix (screenshot + interactive UI) ───────────────────────


def encode_screenshot_refresh(window_id: str) -> str:
    return safe_callback_data(ScreenshotRefreshCallback(window_id=window_id))


def parse_screenshot_refresh(data: str) -> ScreenshotRefreshCallback | None:
    if not data.startswith(CB_SCREENSHOT_REFRESH):
        return None
    window_id = data[len(CB_SCREENSHOT_REFRESH) :]
    if not window_id:
        return None
    try:
        return ScreenshotRefreshCallback(window_id=window_id)
    except ValueError:
        return None


def encode_ask(prefix: str, window_id: str) -> str:
    return safe_callback_data(AskCallback(prefix=prefix, window_id=window_id))


def parse_ask(data: str, prefix: str) -> AskCallback | None:
    """Parse interactive-UI action for a known CB_ASK_* prefix."""
    if not data.startswith(prefix):
        return None
    window_id = data[len(prefix) :]
    if not window_id:
        return None
    try:
        return AskCallback(prefix=prefix, window_id=window_id)
    except ValueError:
        return None


# ── Screenshot control keys ──────────────────────────────────────────────


def encode_key(key_id: str, window_id: str) -> str:
    return safe_callback_data(KeyCallback(key_id=key_id, window_id=window_id))


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
    try:
        return KeyCallback(key_id=key_id, window_id=window_id)
    except ValueError:
        return None
