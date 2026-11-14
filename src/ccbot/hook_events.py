"""Structured hook event channel between the ccbot hook and the bot.

The `ccbot hook` command also records Claude Code's Notification, Stop,
StopFailure and SessionEnd events as JSON lines in
<CCBOT_DIR>/hook_events.jsonl, so the bot can learn about session state
without parsing the terminal.

Core responsibilities:
  - build_record(): turn a hook stdin payload into a compact, size-capped record.
  - append_event(): append one record under an exclusive file lock.
  - HookEventReader: tail the events file by byte offset (complete lines only),
    keeping only events for one tmux session, with optional size-based truncation.

This module must NOT import config.py (hooks run without the bot's env).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import LOCK_EX, LOCK_UN, flock

logger = logging.getLogger(__name__)

EVENTS_FILE_NAME = "hook_events.jsonl"
EVENTS_LOCK_NAME = "hook_events.lock"
RECORDED_EVENTS = frozenset({"Notification", "Stop", "StopFailure", "SessionEnd"})
MAX_FIELD_CHARS = 500

# Event-specific payload fields copied into the record (when present).
_EXTRA_FIELDS: dict[str, tuple[str, ...]] = {
    "Notification": ("notification_type", "message", "title"),
    "StopFailure": ("error", "error_details", "last_assistant_message"),
    "SessionEnd": ("reason",),
    "Stop": (),
}

# Keys of a record that are not event-specific data.
_BASE_KEYS = frozenset({"ts", "event", "window_key", "session_id"})


def build_record(payload: dict, window_key: str, *, now: float | None = None) -> dict:
    """Build the JSON record appended to the events file for one hook payload."""
    event = payload["hook_event_name"]
    record: dict[str, Any] = {
        "ts": now if now is not None else time.time(),
        "event": event,
        "window_key": window_key,
        "session_id": payload["session_id"],
    }
    for field in _EXTRA_FIELDS.get(event, ()):
        if field not in payload:
            continue
        value = payload[field]
        if not isinstance(value, str):
            continue
        record[field] = value[:MAX_FIELD_CHARS]
    return record


def append_event(events_file: Path, record: dict) -> None:
    """Append one record as a JSON line, under an exclusive lock.

    Raises OSError if the file cannot be written.
    """
    events_file.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    lock_path = events_file.with_name(EVENTS_LOCK_NAME)
    with open(lock_path, "w") as lock_f:
        flock(lock_f, LOCK_EX)
        try:
            fd = os.open(events_file, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                while data:
                    written = os.write(fd, data)
                    data = data[written:]
            finally:
                os.close(fd)
        finally:
            flock(lock_f, LOCK_UN)


@dataclass(frozen=True)
class HookEvent:
    """One hook event for a window owned by this tmux session."""

    ts: float
    event: str
    window_id: str  # e.g. "@12"
    session_id: str
    data: dict  # event-specific fields


class HookEventReader:
    """Incrementally read new events for one tmux session from the events file."""

    def __init__(
        self,
        events_file: Path,
        tmux_session_name: str,
        *,
        start_at_end: bool = True,
    ) -> None:
        self._file = events_file
        self._prefix = f"{tmux_session_name}:"
        self._offset = 0
        if start_at_end:
            try:
                self._offset = events_file.stat().st_size
            except OSError:
                self._offset = 0

    def read_new(self) -> list[HookEvent]:
        """Return events appended since the last call (complete lines only)."""
        try:
            size = self._file.stat().st_size
        except FileNotFoundError:
            return []
        except OSError as e:
            logger.error("Failed to stat %s: %s", self._file, e)
            return []

        if size < self._offset:
            logger.info("Hook events file truncated, re-reading from start")
            self._offset = 0
        if size == self._offset:
            return []

        try:
            with open(self._file, "rb") as f:
                f.seek(self._offset)
                chunk = f.read()
        except OSError as e:
            logger.error("Failed to read %s: %s", self._file, e)
            return []

        last_newline = chunk.rfind(b"\n")
        if last_newline < 0:
            return []  # only a partial line so far
        complete = chunk[: last_newline + 1]
        self._offset += len(complete)

        events: list[HookEvent] = []
        for raw in complete.split(b"\n"):
            if not raw.strip():
                continue
            event = self._parse_line(raw)
            if event is not None:
                events.append(event)
        return events

    def _parse_line(self, raw: bytes) -> HookEvent | None:
        try:
            record = json.loads(raw)
        except ValueError as e:
            logger.debug("Skipping malformed hook event line: %s", e)
            return None
        if not isinstance(record, dict):
            logger.debug("Skipping non-object hook event line")
            return None
        if not all(k in record for k in ("event", "window_key", "session_id")):
            logger.debug("Skipping hook event line with missing keys")
            return None

        window_key = record["window_key"]
        if not isinstance(window_key, str) or not window_key.startswith(self._prefix):
            return None

        ts = record.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            ts = 0.0
        data = {k: v for k, v in record.items() if k not in _BASE_KEYS}
        return HookEvent(
            ts=float(ts),
            event=str(record["event"]),
            window_id=window_key[len(self._prefix) :],
            session_id=str(record["session_id"]),
            data=data,
        )

    def maybe_truncate(self, max_bytes: int = 1_000_000) -> bool:
        """Truncate the events file if it is over *max_bytes* and fully consumed.

        Returns True if the file was truncated.
        """
        try:
            size = self._file.stat().st_size
        except OSError:
            return False
        if size <= max_bytes or size != self._offset:
            return False

        lock_path = self._file.with_name(EVENTS_LOCK_NAME)
        try:
            with open(lock_path, "w") as lock_f:
                flock(lock_f, LOCK_EX)
                try:
                    # Re-check under the lock: a writer may have appended.
                    if self._file.stat().st_size != self._offset:
                        return False
                    os.truncate(self._file, 0)
                    self._offset = 0
                    logger.info("Truncated hook events file %s", self._file)
                    return True
                finally:
                    flock(lock_f, LOCK_UN)
        except OSError as e:
            logger.error("Failed to truncate %s: %s", self._file, e)
            return False
