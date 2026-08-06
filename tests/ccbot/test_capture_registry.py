"""Tests for CaptureTaskRegistry (ISS-010)."""

import asyncio
import contextlib

from ccbot.handlers.capture_registry import CaptureTaskRegistry


async def _never() -> None:
    await asyncio.sleep(3600)


async def test_register_and_discard_does_not_cancel() -> None:
    reg = CaptureTaskRegistry()
    task = asyncio.create_task(_never())
    reg.register(1, 100, task)
    reg.discard(1, 100)
    # discard removes the entry without cancelling the task
    assert not task.done()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_cancel_running_task() -> None:
    reg = CaptureTaskRegistry()
    task = asyncio.create_task(_never())
    reg.register(1, 100, task)
    reg.cancel(1, 100)
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled()


async def test_cancel_missing_key_is_noop() -> None:
    reg = CaptureTaskRegistry()
    reg.cancel(999, 999)


async def test_cancel_done_task_does_not_raise() -> None:
    reg = CaptureTaskRegistry()

    async def done_worker() -> None:
        return None

    task = asyncio.create_task(done_worker())
    await task
    reg.register(1, 100, task)
    reg.cancel(1, 100)
    assert task.done()
