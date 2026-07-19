"""Focused tests for message queue enqueue / flood / worker (REF-009)."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.handlers import message_queue as mq
from ccbot.handlers.message_queue import (
    MessageTask,
    enqueue_content_message,
    enqueue_status_update,
    get_message_queue,
    get_or_create_queue,
    shutdown_workers,
)


@pytest.fixture(autouse=True)
async def _isolate_queue_state():
    """Reset module queue state around each test (no leftover workers)."""
    await shutdown_workers()
    mq._message_queues.clear()
    mq._queue_workers.clear()
    mq._queue_locks.clear()
    mq._tool_msg_ids.clear()
    mq._status_msg_info.clear()
    mq._flood_until.clear()
    yield
    await shutdown_workers()
    mq._message_queues.clear()
    mq._queue_workers.clear()
    mq._queue_locks.clear()
    mq._tool_msg_ids.clear()
    mq._status_msg_info.clear()
    mq._flood_until.clear()


@pytest.mark.asyncio
async def test_enqueue_content_creates_queue_and_task() -> None:
    bot = MagicMock()
    with patch.object(mq, "_message_queue_worker", new_callable=AsyncMock):
        await enqueue_content_message(
            bot,
            user_id=1,
            window_id="@1",
            parts=["hello"],
            content_type="text",
            thread_id=7,
        )
        q = get_message_queue(1)
        assert q is not None
        assert q.qsize() == 1
        task = q.get_nowait()
        assert isinstance(task, MessageTask)
        assert task.task_type == "content"
        assert task.parts == ["hello"]
        assert task.window_id == "@1"
        assert task.thread_id == 7


@pytest.mark.asyncio
async def test_enqueue_status_skipped_during_flood() -> None:
    bot = MagicMock()
    mq._flood_until[1] = time.monotonic() + 99
    with patch.object(mq, "_message_queue_worker", new_callable=AsyncMock):
        await enqueue_status_update(
            bot,
            user_id=1,
            window_id="@1",
            status_text="working…",
            thread_id=3,
        )
        assert get_message_queue(1) is None


@pytest.mark.asyncio
async def test_enqueue_status_dedupes_same_text() -> None:
    bot = MagicMock()
    with patch.object(mq, "_message_queue_worker", new_callable=AsyncMock):
        mq._status_msg_info[(1, 5)] = (10, "@2", "same")
        await enqueue_status_update(
            bot,
            user_id=1,
            window_id="@2",
            status_text="same",
            thread_id=5,
        )
        assert get_message_queue(1) is None

        await enqueue_status_update(
            bot,
            user_id=1,
            window_id="@2",
            status_text="changed",
            thread_id=5,
        )
        q = get_message_queue(1)
        assert q is not None
        task = q.get_nowait()
        assert task.task_type == "status_update"
        assert task.text == "changed"
        assert task.thread_id == 5


@pytest.mark.asyncio
async def test_enqueue_status_clear() -> None:
    bot = MagicMock()
    with patch.object(mq, "_message_queue_worker", new_callable=AsyncMock):
        await enqueue_status_update(
            bot,
            user_id=1,
            window_id="@1",
            status_text=None,
            thread_id=2,
        )
        q = get_message_queue(1)
        assert q is not None
        task = q.get_nowait()
        assert task.task_type == "status_clear"
        assert task.thread_id == 2


@pytest.mark.asyncio
async def test_worker_processes_content_and_marks_done() -> None:
    bot = MagicMock()
    processed: list[MessageTask] = []

    async def fake_process(_bot, _user_id, task: MessageTask) -> None:
        processed.append(task)

    with patch.object(mq, "_process_content_with_retry", side_effect=fake_process):
        q: asyncio.Queue[MessageTask] = asyncio.Queue()
        mq._message_queues[3] = q
        mq._queue_locks[3] = asyncio.Lock()
        worker = asyncio.create_task(mq._message_queue_worker(bot, 3))
        mq._queue_workers[3] = worker
        await q.put(
            MessageTask(
                task_type="content",
                window_id="@9",
                parts=["hi"],
                thread_id=0,
            )
        )
        for _ in range(50):
            if processed:
                break
            await asyncio.sleep(0.01)
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        assert len(processed) == 1
        assert processed[0].parts == ["hi"]


@pytest.mark.asyncio
async def test_get_or_create_queue_starts_worker_once() -> None:
    bot = MagicMock()
    with patch.object(mq, "_message_queue_worker", new_callable=AsyncMock) as worker_fn:
        q1 = get_or_create_queue(bot, 42)
        q2 = get_or_create_queue(bot, 42)
        assert q1 is q2
        assert worker_fn.call_count == 1
