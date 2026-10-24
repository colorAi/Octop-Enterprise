"""Bound synchronous reads without releasing running work on HTTP cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any


class ReadQueueFull(Exception):
    """The bounded read queue cannot accept another operation."""


class BoundedIO:
    def __init__(self, *, workers: int = 4, capacity: int = 32, wait: bool = False) -> None:
        self._slots = asyncio.Semaphore(workers)
        self._capacity = capacity
        self._admission = asyncio.Semaphore(capacity) if wait else None
        self._waiting = 0
        self._pending = 0
        self._idle = asyncio.Event()
        self._idle.set()

    async def run[T](self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        return await self._run(
            lambda: asyncio.get_running_loop().run_in_executor(None, partial(fn, *args, **kwargs))
        )

    async def run_async[T](self, fn: Callable[[], Coroutine[Any, Any, T]]) -> T:
        """Keep a composite async I/O operation bounded until all its work finishes."""
        return await self._run(lambda: asyncio.create_task(fn()))

    async def _run[T](self, start: Callable[[], asyncio.Future[T]]) -> T:
        if self._admission is not None:
            self._waiting += 1
            self._idle.clear()
            try:
                await self._admission.acquire()
            except BaseException:
                self._waiting -= 1
                if self._pending == self._waiting == 0:
                    self._idle.set()
                raise
            self._waiting -= 1
        elif self._pending >= self._capacity:
            raise ReadQueueFull
        self._pending += 1
        self._idle.clear()
        try:
            await self._slots.acquire()
        except BaseException:
            self._finished()
            raise
        try:
            future = start()
        except BaseException:
            self._slots.release()
            self._finished()
            raise

        def done(result: asyncio.Future[T]) -> None:
            # Observe late exceptions even when the requesting client disconnected.
            if not result.cancelled():
                result.exception()
            self._slots.release()
            self._finished()

        future.add_done_callback(done)
        return await asyncio.shield(future)

    def _finished(self) -> None:
        self._pending -= 1
        if self._admission is not None:
            self._admission.release()
        if self._pending == self._waiting == 0:
            self._idle.set()

    async def drain(self) -> None:
        """Wait for accepted reads before their database/runtime is torn down."""
        await self._idle.wait()
