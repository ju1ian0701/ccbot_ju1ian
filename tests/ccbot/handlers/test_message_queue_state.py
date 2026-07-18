"""Unit tests for MessageQueueManager / StatusTracker (REF-005)."""

from __future__ import annotations

import asyncio

import pytest

from ccbot.handlers.message_queue import (
    MERGE_MAX_LENGTH,
    MessageQueueManager,
    MessageTask,
    StatusTracker,
    _can_merge_tasks,
    _merge_content_tasks,
    queue_manager,
)


class TestStatusTracker:
    def test_status_roundtrip(self) -> None:
        st = StatusTracker()
        st.set_status(1, 0, 10, "@1", "hello")
        assert st.get_status(1, 0) == (10, "@1", "hello")
        assert st.pop_status(1, 0) == (10, "@1", "hello")
        assert st.get_status(1, 0) is None

    def test_tool_ids_and_topic_clear(self) -> None:
        st = StatusTracker()
        st.set_tool_msg_id("tu1", 1, 5, 100)
        st.set_tool_msg_id("tu2", 1, 5, 101)
        st.set_tool_msg_id("tu3", 1, 9, 102)
        st.clear_tools_for_topic(1, 5)
        assert st.get_tool_msg_id("tu1", 1, 5) is None
        assert st.get_tool_msg_id("tu2", 1, 5) is None
        assert st.get_tool_msg_id("tu3", 1, 9) == 102

    def test_clear_all(self) -> None:
        st = StatusTracker()
        st.set_status(1, 0, 1, "@1", "x")
        st.set_tool_msg_id("t", 1, 0, 2)
        st.clear_all()
        assert st.get_status(1, 0) is None
        assert st.get_tool_msg_id("t", 1, 0) is None


class TestMessageQueueManager:
    def test_flood_lifecycle(self) -> None:
        mgr = MessageQueueManager()
        assert mgr.is_flooded(1) is False
        mgr.set_flood(1, 30.0)
        assert mgr.is_flooded(1) is True
        assert mgr.flood_remaining(1) > 0
        mgr.clear_flood(1)
        assert mgr.is_flooded(1) is False
        assert mgr.flood_remaining(1) == 0.0

    def test_get_queue_none_until_created(self) -> None:
        mgr = MessageQueueManager()
        assert mgr.get_queue(99) is None

    @pytest.mark.asyncio
    async def test_shutdown_clears_state(self) -> None:
        mgr = MessageQueueManager()
        q: asyncio.Queue[MessageTask] = asyncio.Queue()
        mgr._queues[1] = q
        mgr._locks[1] = asyncio.Lock()

        async def _noop() -> None:
            await asyncio.Event().wait()

        mgr._workers[1] = asyncio.create_task(_noop())
        mgr.status.set_status(1, 0, 1, "@1", "x")
        mgr.set_flood(1, 10.0)
        await mgr.shutdown()
        assert mgr.get_queue(1) is None
        assert mgr.status.get_status(1) is None
        assert mgr.is_flooded(1) is False
        assert mgr._workers == {}
        assert mgr._locks == {}


def test_can_merge_same_window_text() -> None:
    a = MessageTask(task_type="content", window_id="@1", parts=["a"])
    b = MessageTask(task_type="content", window_id="@1", parts=["b"])
    assert _can_merge_tasks(a, b) is True


def test_cannot_merge_tool_use() -> None:
    a = MessageTask(
        task_type="content", window_id="@1", parts=["a"], content_type="tool_use"
    )
    b = MessageTask(task_type="content", window_id="@1", parts=["b"])
    assert _can_merge_tasks(a, b) is False


@pytest.mark.asyncio
async def test_merge_content_tasks_respects_limit() -> None:
    q: asyncio.Queue[MessageTask] = asyncio.Queue()
    first = MessageTask(task_type="content", window_id="@1", parts=["x" * 100])
    # next chunk would exceed limit
    big = MessageTask(
        task_type="content",
        window_id="@1",
        parts=["y" * (MERGE_MAX_LENGTH)],
    )
    small = MessageTask(task_type="content", window_id="@1", parts=["z"])
    q.put_nowait(big)
    q.put_nowait(small)
    lock = asyncio.Lock()
    merged, count = await _merge_content_tasks(q, first, lock)
    assert count == 0
    assert merged is first
    assert q.qsize() == 2


def test_module_singleton_exists() -> None:
    assert isinstance(queue_manager, MessageQueueManager)
    assert isinstance(queue_manager.status, StatusTracker)


N_PARALLEL = 50


async def _concurrent_status_increment(
    st: StatusTracker,
    *,
    lock: asyncio.Lock | None,
    user_id: int = 1,
    thread_id: int = 0,
) -> None:
    """Read-modify-write status message_id with optional critical section.

    ``await asyncio.sleep(0)`` yields so 50 concurrent tasks interleave between
    read and write — classic lost-update race without a lock.
    """

    async def _body() -> None:
        info = st.get_status(user_id, thread_id)
        current = info[0] if info else 0
        await asyncio.sleep(0)
        st.set_status(user_id, thread_id, current + 1, "@1", "inc")

    if lock is None:
        await _body()
    else:
        async with lock:
            await _body()


@pytest.mark.asyncio
async def test_gather_50_status_updates_lose_without_lock() -> None:
    """Without asyncio.Lock, parallel RMW loses updates (counter < 50)."""
    st = StatusTracker()
    await asyncio.gather(
        *[_concurrent_status_increment(st, lock=None) for _ in range(N_PARALLEL)]
    )
    final = st.get_status(1, 0)
    assert final is not None
    assert final[0] < N_PARALLEL, (
        f"expected lost updates without lock, got message_id={final[0]}"
    )


@pytest.mark.asyncio
async def test_gather_50_status_updates_safe_with_lock() -> None:
    """With asyncio.Lock, all 50 parallel RMW complete (counter == 50)."""
    st = StatusTracker()
    lock = asyncio.Lock()
    await asyncio.gather(
        *[_concurrent_status_increment(st, lock=lock) for _ in range(N_PARALLEL)]
    )
    final = st.get_status(1, 0)
    assert final is not None
    assert final[0] == N_PARALLEL
