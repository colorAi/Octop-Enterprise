import asyncio
import threading

import pytest

from octop.infra.utils.bounded_io import BoundedIO, ReadQueueFull


async def test_cancelled_reader_keeps_running_work_in_capacity():
    io = BoundedIO(workers=1, capacity=1)
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def slow():
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return "finished"

    request = asyncio.create_task(io.run(slow))
    try:
        await asyncio.wait_for(started.wait(), 2)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        with pytest.raises(ReadQueueFull):
            await io.run(lambda: "must not start")
        draining = asyncio.create_task(io.drain())
        await asyncio.sleep(0)
        assert not draining.done()
    finally:
        release.set()
        await io.drain()
    assert await io.run(lambda: "available") == "available"


async def test_cancelling_waiter_and_failed_read_release_capacity():
    io = BoundedIO(workers=1, capacity=2)
    release = threading.Event()
    first = asyncio.create_task(io.run(release.wait, 5))
    second = asyncio.create_task(io.run(lambda: "queued"))
    try:
        await asyncio.sleep(0)
        with pytest.raises(ReadQueueFull):
            await io.run(lambda: None)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
    finally:
        release.set()
        await first
        await io.drain()

    def fail():
        raise ValueError("test failure")

    with pytest.raises(ValueError, match="test failure"):
        await io.run(fail)
    assert await io.run(lambda: 42) == 42


async def test_cancelled_async_catalog_stays_bounded_until_nested_io_finishes():
    io = BoundedIO(workers=1, capacity=1)
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_catalog():
        started.set()
        await release.wait()
        return {"skills": []}

    request = asyncio.create_task(io.run_async(slow_catalog))
    await started.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    with pytest.raises(ReadQueueFull):
        await io.run_async(slow_catalog)
    release.set()
    await io.drain()
    assert await io.run_async(slow_catalog) == {"skills": []}


async def test_waiting_writes_drain_and_cancel_without_exceeding_capacity():
    io = BoundedIO(workers=1, capacity=1, wait=True)
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    calls = []

    def slow():
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        calls.append("first")

    first = asyncio.create_task(io.run(slow))
    await started.wait()
    cancelled = asyncio.create_task(io.run(lambda: calls.append("cancelled")))
    remaining = [asyncio.create_task(io.run(lambda: calls.append("queued"))) for _ in range(50)]
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    draining = asyncio.create_task(io.drain())
    await asyncio.sleep(0)
    assert not draining.done()
    assert io._pending == 1
    release.set()
    await draining
    await asyncio.gather(first, *remaining)
    assert calls == ["first", *["queued"] * 50]
    assert io._pending == io._waiting == 0
