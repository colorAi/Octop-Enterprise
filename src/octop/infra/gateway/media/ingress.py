"""IM ingress media → agent workspace ``inbound/`` via :class:`BackendWorkspace`."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from octop_gateway.media import MediaBackend
from octop_harness.backends.workspace import BackendWorkspace

from octop.infra.gateway.media.constants import OUTBOUND_DIR
from octop.infra.gateway.media.inbound_store import inbound_rel_path, validate_inbound_size

if TYPE_CHECKING:
    from octop.infra.agents.manager import AgentManager


class AgentBackedMediaBackend(MediaBackend):
    """octop-gateway ``MediaBackend`` adapter backed by ``agent.workspace``."""

    def __init__(self, workspace: BackendWorkspace, *, max_bytes: int | None = None) -> None:
        self._workspace = workspace
        self._max_bytes = max_bytes

    async def save(self, data: bytes, key: str) -> None:
        validate_inbound_size(data, max_bytes=self._max_bytes)
        await self._workspace.aupload_bytes(inbound_rel_path(key), data)

    async def read(self, key: str) -> bytes:
        rel = inbound_rel_path(key)
        data = await self._workspace.adownload_bytes(rel)
        if data is None:
            raise FileNotFoundError(f"Backend download returned no data for {rel!r}")
        return data

    async def exists(self, key: str) -> bool:
        return await self._workspace.aexists(inbound_rel_path(key))

    def get_local_path(self, key: str) -> Path | None:
        return self._workspace.materialize_local(inbound_rel_path(key))

    @property
    def outbound_dir(self) -> str:
        return f"/{OUTBOUND_DIR}"

    @property
    def inbound_dir(self) -> str:
        return "/inbound"

    @staticmethod
    def _inbound_fragment(key: str) -> str:
        """Backward-compatible alias used in tests."""
        return inbound_rel_path(key)


class RuntimeMediaBackend(MediaBackend):
    """Long-lived IM channels resolve and pin a fresh workspace for each transfer."""

    def __init__(self, manager: AgentManager, agent_id: str) -> None:
        self._manager, self._agent_id = manager, agent_id

    @asynccontextmanager
    async def _backend(self) -> AsyncIterator[AgentBackedMediaBackend]:
        manager = self._manager
        row = await asyncio.to_thread(manager.get_row, self._agent_id)
        if row is None:
            raise FileNotFoundError(self._agent_id)
        shards = manager.runtime_shards
        assert shards is not None
        async with shards.activity(row.user_id, self._agent_id):
            await asyncio.to_thread(shards.assert_user, row.user_id)
            workspace = await asyncio.to_thread(manager.workspace_for_agent, self._agent_id)
            if workspace is None:
                raise FileNotFoundError(self._agent_id)
            yield AgentBackedMediaBackend(
                workspace, max_bytes=manager.octop_config.max_upload_bytes
            )

    async def save(self, data: bytes, key: str) -> None:
        async with self._backend() as backend:
            await backend.save(data, key)

    async def read(self, key: str) -> bytes:
        async with self._backend() as backend:
            return await backend.read(key)

    async def exists(self, key: str) -> bool:
        async with self._backend() as backend:
            return await backend.exists(key)
