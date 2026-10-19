"""Task-local graph state with durable, idempotent company checkpoint delivery."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import aiosqlite
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import WRITES_IDX_MAP, CheckpointTuple
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from octop.infra.company_runtime.client import CompanyClient, RemoteCheckpointSaver
from octop.infra.company_runtime.local import atomic_json
from octop.infra.utils.asyncio import offload


async def flush_checkpoints(client: CompanyClient, task_id: str, directory: Path) -> None:
    """Upload saved graph mutations in bounded batches; retain unacknowledged files."""
    paths = sorted((directory / "checkpoints").glob("*.json"))
    while paths:
        batch: list[dict[str, Any]] = []
        selected: list[Path] = []
        size = 0
        for path in paths[:64]:
            raw = await offload(path.read_bytes)
            if batch and size + len(raw) > 2 * 1024 * 1024:
                break
            batch.append(json.loads(raw))
            selected.append(path)
            size += len(raw)
        await client.request(f"tasks/{task_id}/checkpoint-batch", {"operations": batch})
        for path in selected:
            await offload(path.unlink)
        paths = paths[len(selected) :]


class LocalCheckpointSaver(RemoteCheckpointSaver):
    """Seed each namespace once, then persist every graph mutation on this computer.

    Each task has its own seed and outbox, so edited company histories are read
    before execution and an older task cannot replace a later task's state.
    Delivery never dispatches a tool or resumes an interrupted graph.
    """

    def __init__(
        self, client: CompanyClient, task_id: str, thread_id: str, directory: Path
    ) -> None:
        super().__init__(client, task_id, thread_id)
        self.directory = directory
        self.store: AsyncSqliteSaver | None = None
        self.loaded: set[str] = set()
        self.lock = asyncio.Lock()
        self.sequence = 0

    async def _store(self) -> AsyncSqliteSaver:
        if self.store is None:
            await offload(self.directory.mkdir, parents=True, exist_ok=True, mode=0o700)
            connection = await aiosqlite.connect(str(self.directory / "graph.sqlite"))
            self.store = AsyncSqliteSaver(connection, serde=self.serde)
        return self.store

    async def close(self) -> None:
        if self.store is not None:
            await self.store.conn.close()

    async def _seed(self, config: Any) -> None:
        namespace = self.scope(config)["namespace"]
        async with self.lock:
            if namespace in self.loaded:
                return
            await self._store()
            current = await super().aget_tuple(
                {"configurable": {"thread_id": self.thread_id, "checkpoint_ns": namespace}}
            )
            if current is not None:
                await self._cache(current)
            self.loaded.add(namespace)

    async def _cache(self, current: CheckpointTuple) -> None:
        parent: RunnableConfig = current.parent_config or {
            "configurable": {
                "thread_id": self.thread_id,
                "checkpoint_ns": current.config["configurable"].get("checkpoint_ns", ""),
            }
        }
        store = await self._store()
        await store.aput(
            parent, current.checkpoint, current.metadata, current.checkpoint["channel_versions"]
        )
        writes: dict[str, list[tuple[str, Any]]] = defaultdict(list)
        for task_id, channel, value in current.pending_writes or []:
            writes[task_id].append((channel, value))
        for task_id, values in writes.items():
            await store.aput_writes(current.config, values, task_id)

    async def aget_tuple(self, config: Any) -> Any:
        await self._seed(config)
        current = await (await self._store()).aget_tuple(config)
        if current is None and self.scope(config)["checkpoint_id"]:
            current = await super().aget_tuple(config)
            if current is not None:
                async with self.lock:
                    await self._cache(current)
        return current

    async def _queue(self, operation: dict[str, Any]) -> None:
        self.sequence += 1
        await offload(
            atomic_json,
            self.directory / "checkpoints" / f"{self.sequence:012}.json",
            operation,
        )

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        scope = self.scope(config)
        result = {
            "configurable": {
                "thread_id": self.thread_id,
                "checkpoint_ns": scope["namespace"],
                "checkpoint_id": checkpoint["id"],
            }
        }
        payload = {
            "config": result,
            "checkpoint": self.pack(checkpoint),
            "metadata": self.pack(metadata),
            "parent_config": {
                "configurable": {
                    "thread_id": self.thread_id,
                    "checkpoint_ns": scope["namespace"],
                    "checkpoint_id": scope["checkpoint_id"],
                }
            }
            if scope["checkpoint_id"]
            else None,
        }
        async with self.lock:
            await self._queue(
                {
                    "action": "put",
                    "namespace": scope["namespace"],
                    "checkpoint_id": checkpoint["id"],
                    "payload": payload,
                }
            )
            return await (await self._store()).aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self, config: Any, writes: Any, task_id: str, task_path: str = ""
    ) -> None:
        async with self.lock:
            await self._queue(
                {
                    "action": "writes",
                    **self.scope(config),
                    "task_key": task_id,
                    "writes": [
                        {
                            "idx": WRITES_IDX_MAP.get(channel, i),
                            "value": self.pack([task_id, channel, value]),
                        }
                        for i, (channel, value) in enumerate(writes)
                    ],
                }
            )
            await (await self._store()).aput_writes(config, writes, task_id, task_path)
