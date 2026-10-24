"""Best-effort PostgreSQL wakeups; durable state is always polled after a wake."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

logger = logging.getLogger(__name__)


class ExecutionNotifications:
    def __init__(self, conninfo: str | None) -> None:
        self.conninfo = conninfo
        self.changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.conninfo:
            self._task = asyncio.create_task(self._listen(), name="execution-notifications")

    async def _listen(self) -> None:
        from psycopg import AsyncConnection

        assert self.conninfo is not None
        while True:
            try:
                async with await AsyncConnection.connect(
                    self.conninfo, autocommit=True, connect_timeout=5
                ) as conn:
                    await conn.execute("LISTEN octop_execution")
                    self.changed.set()  # Reconnected: catch up even if no new NOTIFY arrives.
                    async for _ in conn.notifies():
                        self.changed.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("execution notification disconnected; using durable polling")
                await asyncio.sleep(2)

    async def wait(self, timeout: float = 1.0) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(self.changed.wait(), timeout)
        self.changed.clear()

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
