"""Shared utility functions used across multiple CCBot modules.

Provides:
  - ccbot_dir(): resolve config directory from CCBOT_DIR env var.
  - atomic_write_json(): crash-safe JSON file writes via temp+rename.
  - read_cwd_from_jsonl(): extract the cwd field from the first JSONL entry.
  - flock()/LOCK_EX/LOCK_UN: advisory file locks (fcntl on POSIX; no-op on Windows).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import IO, Any

CCBOT_DIR_ENV = "CCBOT_DIR"

# fcntl is POSIX-only. On Windows, provide a no-op lock so imports/type-check
# succeed for unit tests; production bot + hook run on Linux with real locks.
if sys.platform != "win32":
    import fcntl as _fcntl

    LOCK_EX = _fcntl.LOCK_EX
    LOCK_UN = _fcntl.LOCK_UN

    def flock(fd: IO[Any] | int, operation: int) -> None:
        """Apply an advisory lock via fcntl.flock (POSIX)."""
        _fcntl.flock(fd, operation)
else:
    LOCK_EX = 2
    LOCK_UN = 8

    def flock(fd: IO[Any] | int, operation: int) -> None:
        """No-op on Windows (fcntl is unavailable)."""
        return None


def ccbot_dir() -> Path:
    """Resolve config directory from CCBOT_DIR env var or default ~/.ccbot."""
    raw = os.environ.get(CCBOT_DIR_ENV, "")
    return Path(raw) if raw else Path.home() / ".ccbot"


def atomic_write_json(path: Path, data: Any, indent: int = 2) -> None:
    """Write JSON data to a file atomically.

    Writes to a temporary file in the same directory, then renames it
    to the target path. This prevents data corruption if the process
    is interrupted mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=indent)

    # Write to temp file in same directory (same filesystem for atomic rename)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=f".{path.name}."
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_cwd_from_jsonl(file_path: str | Path) -> str:
    """Read the cwd field from the first JSONL entry that has one.

    Shared by session.py and session_monitor.py.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    cwd = data.get("cwd")
                    if cwd:
                        return cwd
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return ""
