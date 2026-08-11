"""session_map.json repository (ISS-005 phase 4c).

Owns file access to ``session_map.json`` (written by the SessionStart
hook): async reads, atomic writes, and the flock read-modify-write
discipline shared with the hook. Applying map data to window state
stores stays with the ``SessionManager`` facade. Config paths are
resolved dynamically so test fixtures that patch them keep working.
"""

import json
import logging
from collections.abc import Callable
from pathlib import Path

import aiofiles

from .config import config
from .utils import LOCK_EX, LOCK_UN, atomic_write_json, flock

logger = logging.getLogger(__name__)


class SessionMapRepository:
    """File access to session_map.json with the hook's flock discipline (ISS-005 4c)."""

    @property
    def map_file(self) -> Path:
        """session_map.json path (read dynamically: tests patch config)."""
        return config.session_map_file

    @property
    def prefix(self) -> str:
        """Entry key prefix: "tmux_session:"."""
        return f"{config.tmux_session_name}:"

    def exists(self) -> bool:
        """Whether session_map.json exists."""
        return self.map_file.exists()

    async def read(self) -> dict[str, dict] | None:
        """Read session_map.json; None if missing or unreadable."""
        if not self.map_file.exists():
            return None
        try:
            async with aiofiles.open(self.map_file, "r") as f:
                content = await f.read()
            return json.loads(content)
        except (json.JSONDecodeError, OSError):
            return None

    def write(self, session_map: dict[str, dict]) -> None:
        """Atomically rewrite session_map.json."""
        atomic_write_json(self.map_file, session_map)

    def mutate_locked(self, mutate: Callable[[dict[str, dict]], bool]) -> bool:
        """Read-modify-write session_map.json under the same flock the hook uses.

        The SessionStart hook serializes its writes via session_map.lock;
        any bot-side read-modify-write MUST take the same lock or it can
        overwrite a concurrent hook write (lost update). Synchronous —
        call via asyncio.to_thread from async code.

        Returns True if `mutate` reported changes and the file was rewritten.
        """
        map_file = self.map_file
        lock_path = map_file.with_suffix(".lock")
        try:
            with open(lock_path, "w") as lock_f:
                flock(lock_f, LOCK_EX)
                try:
                    session_map: dict[str, dict] = {}
                    if map_file.exists():
                        try:
                            session_map = json.loads(map_file.read_text())
                        except (json.JSONDecodeError, OSError):
                            logger.warning(
                                "Unreadable session_map.json, skipping mutation"
                            )
                            return False
                    if not mutate(session_map):
                        return False
                    atomic_write_json(map_file, session_map)
                    return True
                finally:
                    flock(lock_f, LOCK_UN)
        except OSError as e:
            logger.error("Failed to update session_map.json: %s", e)
            return False
