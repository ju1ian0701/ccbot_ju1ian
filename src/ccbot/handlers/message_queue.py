"""Per-user message queue management for ordered message delivery.

Provides a queue-based message processing system that ensures:
  - Messages are sent in receive order (FIFO)
  - Status messages always follow content messages
  - Consecutive content messages can be merged for efficiency
  - Thread-aware sending: each MessageTask carries an optional thread_id
    for Telegram topic support

Rate limiting is handled globally by AIORateLimiter on the Application.

Key components:
  - MessageTask: Dataclass representing a queued message task (with thread_id)
  - StatusTracker: status + tool_use message id maps
  - MessageQueueManager: queues, workers, flood control, status tracker
  - queue_manager: process-wide singleton
  - Public wrappers (get_or_create_queue, enqueue_*, clear_*, shutdown_workers)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Literal

from telegram import Bot
from telegram.constants import ChatAction

from ..errors import (
    RetryAfter,
    TelegramError,
    log_exception,
    retry_after_seconds,
)
from ..session import session_manager
from ..terminal_parser import parse_status_line
from ..tmux_manager import tmux_manager
from .message_sender import (
    edit_with_fallback,
    send_photo,
    send_with_fallback,
)

logger = logging.getLogger(__name__)


# Merge limit for content messages
MERGE_MAX_LENGTH = 3800  # Leave room for markdown conversion overhead

# Max seconds to wait for flood control before dropping tasks
FLOOD_CONTROL_MAX_WAIT = 10


@dataclass
class MessageTask:
    """Message task for queue processing."""

    task_type: Literal["content", "status_update", "status_clear"]
    text: str | None = None
    window_id: str | None = None
    # content type fields
    parts: list[str] = field(default_factory=list)
    tool_use_id: str | None = None
    content_type: str = "text"
    thread_id: int | None = None  # Telegram topic thread_id for targeted send
    image_data: list[tuple[str, bytes]] | None = None  # From tool_result images


class StatusTracker:
    """Tracks Telegram status messages and tool_use message IDs."""

    def __init__(self) -> None:
        # (user_id, thread_id_or_0) -> (message_id, window_id, last_text)
        self._status: dict[tuple[int, int], tuple[int, str, str]] = {}
        # (tool_use_id, user_id, thread_id_or_0) -> telegram message_id
        self._tool_msg_ids: dict[tuple[str, int, int], int] = {}

    def set_status(
        self,
        user_id: int,
        thread_id_or_0: int,
        message_id: int,
        window_id: str,
        text: str,
    ) -> None:
        self._status[(user_id, thread_id_or_0)] = (message_id, window_id, text)

    def get_status(
        self, user_id: int, thread_id_or_0: int = 0
    ) -> tuple[int, str, str] | None:
        return self._status.get((user_id, thread_id_or_0))

    def pop_status(
        self, user_id: int, thread_id_or_0: int = 0
    ) -> tuple[int, str, str] | None:
        return self._status.pop((user_id, thread_id_or_0), None)

    def set_tool_msg_id(
        self,
        tool_use_id: str,
        user_id: int,
        thread_id_or_0: int,
        message_id: int,
    ) -> None:
        self._tool_msg_ids[(tool_use_id, user_id, thread_id_or_0)] = message_id

    def get_tool_msg_id(
        self, tool_use_id: str, user_id: int, thread_id_or_0: int
    ) -> int | None:
        return self._tool_msg_ids.get((tool_use_id, user_id, thread_id_or_0))

    def pop_tool_msg_id(
        self, tool_use_id: str, user_id: int, thread_id_or_0: int
    ) -> int | None:
        return self._tool_msg_ids.pop((tool_use_id, user_id, thread_id_or_0), None)

    def clear_tools_for_topic(self, user_id: int, thread_id: int | None = None) -> None:
        tid = thread_id or 0
        keys = [k for k in self._tool_msg_ids if k[1] == user_id and k[2] == tid]
        for key in keys:
            self._tool_msg_ids.pop(key, None)

    def clear_status_for_topic(
        self, user_id: int, thread_id: int | None = None
    ) -> None:
        self._status.pop((user_id, thread_id or 0), None)

    def clear_all(self) -> None:
        self._status.clear()
        self._tool_msg_ids.clear()


class MessageQueueManager:
    """Owns per-user queues, workers, flood control, and status tracking."""

    def __init__(self) -> None:
        self._queues: dict[int, asyncio.Queue[MessageTask]] = {}
        self._workers: dict[int, asyncio.Task[None]] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        # user_id -> monotonic time when flood ban expires
        self._flood_until: dict[int, float] = {}
        self.status = StatusTracker()

    def get_queue(self, user_id: int) -> asyncio.Queue[MessageTask] | None:
        """Get the message queue for a user (if exists)."""
        return self._queues.get(user_id)

    def get_or_create_queue(self, bot: Bot, user_id: int) -> asyncio.Queue[MessageTask]:
        """Get or create message queue and worker for a user."""
        if user_id not in self._queues:
            self._queues[user_id] = asyncio.Queue()
            self._locks[user_id] = asyncio.Lock()
            self._workers[user_id] = asyncio.create_task(
                _message_queue_worker(bot, user_id)
            )
        return self._queues[user_id]

    def set_flood(self, user_id: int, retry_secs: float) -> None:
        """Pause the user queue until now + retry_secs."""
        self._flood_until[user_id] = time.monotonic() + retry_secs

    def is_flooded(self, user_id: int) -> bool:
        return self._flood_until.get(user_id, 0) > time.monotonic()

    def flood_remaining(self, user_id: int) -> float:
        rem = self._flood_until.get(user_id, 0) - time.monotonic()
        return rem if rem > 0 else 0.0

    def clear_flood(self, user_id: int) -> None:
        self._flood_until.pop(user_id, None)

    def get_lock(self, user_id: int) -> asyncio.Lock:
        return self._locks[user_id]

    async def shutdown(self) -> None:
        """Stop all queue workers and clear queue state."""
        for _, worker in list(self._workers.items()):
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._workers.clear()
        self._queues.clear()
        self._locks.clear()
        self._flood_until.clear()
        self.status.clear_all()
        logger.info("Message queue workers stopped")


# Process-wide singleton
queue_manager = MessageQueueManager()


# --- Public wrappers (stable import surface) ---


def get_message_queue(user_id: int) -> asyncio.Queue[MessageTask] | None:
    """Get the message queue for a user (if exists)."""
    return queue_manager.get_queue(user_id)


def get_or_create_queue(bot: Bot, user_id: int) -> asyncio.Queue[MessageTask]:
    """Get or create message queue and worker for a user."""
    return queue_manager.get_or_create_queue(bot, user_id)


def clear_status_msg_info(user_id: int, thread_id: int | None = None) -> None:
    """Clear status message tracking for a user (and optionally a specific thread)."""
    queue_manager.status.clear_status_for_topic(user_id, thread_id)


def clear_tool_msg_ids_for_topic(user_id: int, thread_id: int | None = None) -> None:
    """Clear tool message ID tracking for a specific topic."""
    queue_manager.status.clear_tools_for_topic(user_id, thread_id)


async def shutdown_workers() -> None:
    """Stop all queue workers (called during bot shutdown)."""
    await queue_manager.shutdown()


def _inspect_queue(queue: asyncio.Queue[MessageTask]) -> list[MessageTask]:
    """Non-destructively inspect all items in queue.

    Drains the queue and returns all items. Caller must refill.
    """
    items: list[MessageTask] = []
    while not queue.empty():
        try:
            item = queue.get_nowait()
            items.append(item)
        except asyncio.QueueEmpty:
            break
    return items


def _can_merge_tasks(base: MessageTask, candidate: MessageTask) -> bool:
    """Check if two content tasks can be merged."""
    if base.window_id != candidate.window_id:
        return False
    if candidate.task_type != "content":
        return False
    # tool_use/tool_result break merge chain
    # - tool_use: will be edited later by tool_result
    # - tool_result: edits previous message, merging would cause order issues
    if base.content_type in ("tool_use", "tool_result"):
        return False
    if candidate.content_type in ("tool_use", "tool_result"):
        return False
    return True


async def _merge_content_tasks(
    queue: asyncio.Queue[MessageTask],
    first: MessageTask,
    lock: asyncio.Lock,
) -> tuple[MessageTask, int]:
    """Merge consecutive content tasks from queue.

    Returns: (merged_task, merge_count) where merge_count is the number of
    additional tasks merged (0 if no merging occurred).

    Note on queue counter management:
        When we put items back, we call task_done() to compensate for the
        internal counter increment caused by put_nowait(). This is necessary
        because the items were already counted when originally enqueued.
        Without this compensation, queue.join() would wait indefinitely.
    """
    merged_parts = list(first.parts)
    current_length = sum(len(p) for p in merged_parts)
    merge_count = 0

    async with lock:
        items = _inspect_queue(queue)
        remaining: list[MessageTask] = []

        for i, task in enumerate(items):
            if not _can_merge_tasks(first, task):
                # Can't merge, keep this and all remaining items
                remaining = items[i:]
                break

            # Check length before merging
            task_length = sum(len(p) for p in task.parts)
            if current_length + task_length > MERGE_MAX_LENGTH:
                # Too long, stop merging
                remaining = items[i:]
                break

            merged_parts.extend(task.parts)
            current_length += task_length
            merge_count += 1

        # Put remaining items back into the queue
        for item in remaining:
            queue.put_nowait(item)
            # Compensate: this item was already counted when first enqueued,
            # put_nowait adds a duplicate count that must be removed
            queue.task_done()

    if merge_count == 0:
        return first, 0

    return (
        MessageTask(
            task_type="content",
            window_id=first.window_id,
            parts=merged_parts,
            tool_use_id=first.tool_use_id,
            content_type=first.content_type,
            thread_id=first.thread_id,
        ),
        merge_count,
    )


async def _process_content_with_retry(
    bot: Bot, user_id: int, task: MessageTask, max_attempts: int = 3
) -> None:
    """Send a content task, retrying across RetryAfter (flood control).

    Content is actual Claude output — unlike ephemeral status updates it must
    not be dropped just because a 429 bubbled past AIORateLimiter's retries.
    Retrying re-runs the whole task, so parts already sent before the 429 may
    be duplicated; duplication is preferred over losing output.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            await _process_content_task(bot, user_id, task)
            return
        except RetryAfter as e:
            retry_secs = int(retry_after_seconds(e))
            if retry_secs > FLOOD_CONTROL_MAX_WAIT:
                # Long ban — also pause subsequent queued tasks
                queue_manager.set_flood(user_id, retry_secs)
            if attempt >= max_attempts:
                logger.error(
                    "Dropping content message for user %d after %d flood-control "
                    "retries (retry_after=%ds)",
                    user_id,
                    max_attempts,
                    retry_secs,
                )
                return
            logger.warning(
                "Flood control for user %d: retrying content in %ds (attempt %d/%d)",
                user_id,
                retry_secs,
                attempt,
                max_attempts,
            )
            await asyncio.sleep(retry_secs)


async def _message_queue_worker(bot: Bot, user_id: int) -> None:
    """Process message tasks for a user sequentially."""
    queue = queue_manager._queues[user_id]
    lock = queue_manager._locks[user_id]
    logger.info(f"Message queue worker started for user {user_id}")

    while True:
        try:
            task = await queue.get()
            try:
                # Flood control: drop status, wait for content
                remaining = queue_manager.flood_remaining(user_id)
                if remaining > 0:
                    if task.task_type != "content":
                        # Status is ephemeral — safe to drop
                        continue
                    # Content is actual Claude output — wait then send
                    logger.debug(
                        "Flood controlled: waiting %.0fs for content (user %d)",
                        remaining,
                        user_id,
                    )
                    await asyncio.sleep(remaining)
                    # Ban expired
                    queue_manager.clear_flood(user_id)
                    logger.info("Flood control lifted for user %d", user_id)

                if task.task_type == "content":
                    # Try to merge consecutive content tasks
                    merged_task, merge_count = await _merge_content_tasks(
                        queue, task, lock
                    )
                    if merge_count > 0:
                        logger.debug(f"Merged {merge_count} tasks for user {user_id}")
                        # Mark merged tasks as done
                        for _ in range(merge_count):
                            queue.task_done()
                    await _process_content_with_retry(bot, user_id, merged_task)
                elif task.task_type == "status_update":
                    await _process_status_update_task(bot, user_id, task)
                elif task.task_type == "status_clear":
                    await _do_clear_status_message(bot, user_id, task.thread_id or 0)
            except RetryAfter as e:
                retry_secs = int(retry_after_seconds(e))
                if retry_secs > FLOOD_CONTROL_MAX_WAIT:
                    queue_manager.set_flood(user_id, retry_secs)
                    logger.warning(
                        "Flood control for user %d: retry_after=%ds, "
                        "pausing queue until ban expires",
                        user_id,
                        retry_secs,
                    )
                else:
                    logger.warning(
                        "Flood control for user %d: waiting %ds",
                        user_id,
                        retry_secs,
                    )
                    await asyncio.sleep(retry_secs)
            except TelegramError as e:
                log_exception(
                    logger,
                    "Telegram error processing message task",
                    e,
                    level=logging.ERROR,
                    user_id=user_id,
                )
            except Exception as e:
                log_exception(
                    logger,
                    "Error processing message task",
                    e,
                    level=logging.ERROR,
                    user_id=user_id,
                )
            finally:
                queue.task_done()
        except asyncio.CancelledError:
            logger.info(f"Message queue worker cancelled for user {user_id}")
            break
        except Exception as e:
            log_exception(
                logger,
                "Unexpected error in queue worker",
                e,
                level=logging.ERROR,
                user_id=user_id,
            )


def _send_kwargs(thread_id: int | None) -> dict[str, int]:
    """Build message_thread_id kwargs for bot.send_message()."""
    if thread_id is not None:
        return {"message_thread_id": thread_id}
    return {}


async def _send_task_images(bot: Bot, chat_id: int, task: MessageTask) -> None:
    """Send images attached to a task, if any."""
    if not task.image_data:
        return
    logger.info(
        "Sending %d image(s) in thread %s",
        len(task.image_data),
        task.thread_id,
    )
    await send_photo(
        bot,
        chat_id,
        task.image_data,
        **_send_kwargs(task.thread_id),  # type: ignore[arg-type]
    )


async def _process_content_task(bot: Bot, user_id: int, task: MessageTask) -> None:
    """Process a content message task."""
    wid = task.window_id or ""
    tid = task.thread_id or 0
    chat_id = session_manager.resolve_chat_id(user_id, task.thread_id)
    st = queue_manager.status

    # 1. Handle tool_result editing (merged parts are edited together)
    if task.content_type == "tool_result" and task.tool_use_id:
        edit_msg_id = st.pop_tool_msg_id(task.tool_use_id, user_id, tid)
        if edit_msg_id is not None:
            # Clear status message first
            await _do_clear_status_message(bot, user_id, tid)
            # Join all parts for editing (merged content goes together)
            full_text = "\n\n".join(task.parts)
            edited = await edit_with_fallback(
                bot,
                chat_id,
                edit_msg_id,
                full_text,
            )
            if edited:
                await _send_task_images(bot, chat_id, task)
                await _check_and_send_status(bot, user_id, wid, task.thread_id)
                return
            logger.debug("Failed to edit tool msg %s, sending new", edit_msg_id)
            # Fall through to send as new message

    # 2. Send content messages, converting status message to first content part
    first_part = True
    last_msg_id: int | None = None
    for part in task.parts:
        sent = None

        # For first part, try to convert status message to content (edit instead of delete)
        if first_part:
            first_part = False
            converted_msg_id = await _convert_status_to_content(
                bot,
                user_id,
                tid,
                wid,
                part,
            )
            if converted_msg_id is not None:
                last_msg_id = converted_msg_id
                continue

        sent = await send_with_fallback(
            bot,
            chat_id,
            part,
            **_send_kwargs(task.thread_id),  # type: ignore[arg-type]
        )

        if sent:
            last_msg_id = sent.message_id

    # 3. Record tool_use message ID for later editing
    if last_msg_id and task.tool_use_id and task.content_type == "tool_use":
        st.set_tool_msg_id(task.tool_use_id, user_id, tid, last_msg_id)

    # 4. Send images if present (from tool_result with base64 image blocks)
    await _send_task_images(bot, chat_id, task)

    # 5. After content, check and send status
    await _check_and_send_status(bot, user_id, wid, task.thread_id)


async def _convert_status_to_content(
    bot: Bot,
    user_id: int,
    thread_id_or_0: int,
    window_id: str,
    content_text: str,
) -> int | None:
    """Convert status message to content message by editing it.

    Returns the message_id if converted successfully, None otherwise.
    """
    info = queue_manager.status.pop_status(user_id, thread_id_or_0)
    if not info:
        return None

    msg_id, stored_wid, _ = info
    chat_id = session_manager.resolve_chat_id(user_id, thread_id_or_0 or None)
    if stored_wid != window_id:
        # Different window, just delete the old status
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except TelegramError as e:
            log_exception(
                logger,
                "Failed to delete status on window change",
                e,
                message_id=msg_id,
            )
        return None

    # Edit status message to show content
    edited = await edit_with_fallback(bot, chat_id, msg_id, content_text)
    if edited:
        return msg_id
    # Message might be deleted or too old, caller will send new message
    return None


async def _process_status_update_task(
    bot: Bot, user_id: int, task: MessageTask
) -> None:
    """Process a status update task."""
    wid = task.window_id or ""
    tid = task.thread_id or 0
    chat_id = session_manager.resolve_chat_id(user_id, task.thread_id)
    status_text = task.text or ""
    st = queue_manager.status

    if not status_text:
        # No status text means clear status
        await _do_clear_status_message(bot, user_id, tid)
        return

    current_info = st.get_status(user_id, tid)

    if current_info:
        msg_id, stored_wid, last_text = current_info

        if stored_wid != wid:
            # Window changed - delete old and send new
            await _do_clear_status_message(bot, user_id, tid)
            await _do_send_status_message(bot, user_id, tid, wid, status_text)
        elif status_text == last_text:
            # Same content, skip edit
            return
        else:
            # Same window, text changed - edit in place
            # Send typing indicator when Claude is working
            if "esc to interrupt" in status_text.lower():
                try:
                    await bot.send_chat_action(
                        chat_id=chat_id, action=ChatAction.TYPING
                    )
                except RetryAfter:
                    raise
                except TelegramError as e:
                    log_exception(logger, "Failed to send typing action", e)
            edited = await edit_with_fallback(bot, chat_id, msg_id, status_text)
            if edited:
                st.set_status(user_id, tid, msg_id, wid, status_text)
            else:
                st.pop_status(user_id, tid)
                await _do_send_status_message(bot, user_id, tid, wid, status_text)
    else:
        # No existing status message, send new
        await _do_send_status_message(bot, user_id, tid, wid, status_text)


async def _do_send_status_message(
    bot: Bot,
    user_id: int,
    thread_id_or_0: int,
    window_id: str,
    text: str,
) -> None:
    """Send a new status message and track it (internal, called from worker)."""
    st = queue_manager.status
    thread_id: int | None = thread_id_or_0 if thread_id_or_0 != 0 else None
    chat_id = session_manager.resolve_chat_id(user_id, thread_id)
    # Safety net: delete any orphaned status message before sending a new one.
    # This catches edge cases where tracking was cleared without deleting the message.
    old = st.pop_status(user_id, thread_id_or_0)
    if old:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old[0])
        except TelegramError as e:
            log_exception(
                logger,
                "Failed to delete orphaned status message",
                e,
                message_id=old[0],
            )
    # Send typing indicator when Claude is working
    if "esc to interrupt" in text.lower():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except RetryAfter:
            raise
        except TelegramError as e:
            log_exception(logger, "Failed to send typing action", e)
    sent = await send_with_fallback(
        bot,
        chat_id,
        text,
        **_send_kwargs(thread_id),  # type: ignore[arg-type]
    )
    if sent:
        st.set_status(user_id, thread_id_or_0, sent.message_id, window_id, text)


async def _do_clear_status_message(
    bot: Bot,
    user_id: int,
    thread_id_or_0: int = 0,
) -> None:
    """Delete the status message for a user (internal, called from worker)."""
    info = queue_manager.status.pop_status(user_id, thread_id_or_0)
    if info:
        msg_id = info[0]
        chat_id = session_manager.resolve_chat_id(user_id, thread_id_or_0 or None)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except TelegramError as e:
            log_exception(
                logger,
                "Failed to delete status message",
                e,
                message_id=msg_id,
            )


async def _check_and_send_status(
    bot: Bot,
    user_id: int,
    window_id: str,
    thread_id: int | None = None,
) -> None:
    """Check terminal for status line and send status message if present."""
    # Skip if there are more messages pending in the queue
    queue = queue_manager.get_queue(user_id)
    if queue and not queue.empty():
        return
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        return

    pane_text = await tmux_manager.capture_pane(w.window_id)
    if not pane_text:
        return

    tid = thread_id or 0
    status_line = parse_status_line(pane_text)
    if status_line:
        await _do_send_status_message(bot, user_id, tid, window_id, status_line)


async def enqueue_content_message(
    bot: Bot,
    user_id: int,
    window_id: str,
    parts: list[str],
    tool_use_id: str | None = None,
    content_type: str = "text",
    text: str | None = None,
    thread_id: int | None = None,
    image_data: list[tuple[str, bytes]] | None = None,
) -> None:
    """Enqueue a content message task."""
    logger.debug(
        "Enqueue content: user=%d, window_id=%s, content_type=%s",
        user_id,
        window_id,
        content_type,
    )
    queue = get_or_create_queue(bot, user_id)

    task = MessageTask(
        task_type="content",
        text=text,
        window_id=window_id,
        parts=parts,
        tool_use_id=tool_use_id,
        content_type=content_type,
        thread_id=thread_id,
        image_data=image_data,
    )
    queue.put_nowait(task)


async def enqueue_status_update(
    bot: Bot,
    user_id: int,
    window_id: str,
    status_text: str | None,
    thread_id: int | None = None,
) -> None:
    """Enqueue status update. Skipped if text unchanged or during flood control."""
    # Don't enqueue during flood control — they'd just be dropped
    if queue_manager.is_flooded(user_id):
        return

    tid = thread_id or 0

    # Deduplicate: skip if text matches what's already displayed
    if status_text:
        info = queue_manager.status.get_status(user_id, tid)
        if info and info[1] == window_id and info[2] == status_text:
            return

    queue = get_or_create_queue(bot, user_id)

    if status_text:
        task = MessageTask(
            task_type="status_update",
            text=status_text,
            window_id=window_id,
            thread_id=thread_id,
        )
    else:
        task = MessageTask(task_type="status_clear", thread_id=thread_id)

    queue.put_nowait(task)
