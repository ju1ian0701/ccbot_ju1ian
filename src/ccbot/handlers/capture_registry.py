"""Registry for active bash capture background tasks.

Owns the process-wide mapping ``(user_id, thread_id) -> asyncio.Task`` for
``!`` bash output capture watchers. Replaces the former module-level mutable
``bash_capture_tasks`` dict in ``message_handlers.py`` (ISS-010).

``cleanup.clear_topic_state`` cancels captures through this registry so topic
teardown never leaves a running watcher behind.
"""

from __future__ import annotations

import asyncio


class CaptureTaskRegistry:
    """Process-wide owner of active bash capture tasks."""

    def __init__(self) -> None:
        self._tasks: dict[tuple[int, int], asyncio.Task[None]] = {}

    def register(self, user_id: int, thread_id: int, task: asyncio.Task[None]) -> None:
        """Store the capture task for a topic (overwrites any previous entry)."""
        self._tasks[(user_id, thread_id)] = task

    def cancel(self, user_id: int, thread_id: int) -> None:
        """Cancel the running capture task for a topic, if any."""
        task = self._tasks.pop((user_id, thread_id), None)
        if task and not task.done():
            task.cancel()

    def discard(self, user_id: int, thread_id: int) -> None:
        """Remove the registry entry without cancelling (task self-cleanup)."""
        self._tasks.pop((user_id, thread_id), None)


# Process-wide singleton
capture_tasks = CaptureTaskRegistry()
