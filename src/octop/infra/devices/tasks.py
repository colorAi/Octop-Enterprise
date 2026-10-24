"""Per-turn device authority. These tokens never come from model tool arguments."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from octop.infra.devices.protocol import DeviceSelection

if TYPE_CHECKING:
    from octop.infra.devices.manager import Connection

CONFIG_KEY = "octop_device_task"
# Auto mode currently permits company knowledge retrieval only. External connectors,
# script execution and delegation cannot inherit access to local document contents.
AUTO_SERVER_TOOLS = frozenset({"search_knowledge"})
READ_TOOLS = frozenset({"local_list_directory", "local_read_file", "local_operation_status"})


@dataclass(frozen=True)
class DeviceTask:
    token: str
    owner: int
    agent_id: str
    thread_id: str
    session_key: str
    selection: DeviceSelection
    expires_at: int
    request_id: str | None = None

    def valid_grant(self, conn: Connection) -> bool:
        return (
            self.expires_at > time.time()
            and conn.row["owner_user_id"] == self.owner
            and any(
                g.id == self.selection.grant_id
                and g.version == self.selection.grant_version
                and g.allow_company
                for g in conn.grants
            )
        )


def stamp_device_task(request: dict[str, Any], meta: dict[str, Any] | None) -> None:
    """Explicitly clear old authority when a subsequent turn selects server tools."""
    configurable = dict(request.get("configurable") or {})
    configurable[CONFIG_KEY] = (meta or {}).get(CONFIG_KEY)
    request["configurable"] = configurable
    if configurable[CONFIG_KEY]:
        # No lazy MCP discovery or skills may restore arbitrary server tools.
        request["mcp_servers"] = []
        request["skills"] = []
        configurable["mcp_servers"] = []
        configurable["skills"] = []
        configurable.pop("mcp_use_default", None)


# Harness HITL resume currently only supplies thread_id. Carry the verified
# original turn through a ContextVar, never through a shared agent configuration.

_RESUMED: ContextVar[dict[str, Any] | None] = ContextVar("octop_resumed_device_task", default=None)


@contextmanager
def resumed_device_task(config: dict[str, Any] | None) -> Iterator[None]:
    token = _RESUMED.set(config)
    try:
        yield
    finally:
        _RESUMED.reset(token)


def effective_device_config(config: dict[str, Any]) -> dict[str, Any]:
    original = _RESUMED.get()
    if original and original.get("thread_id") == config.get("thread_id"):
        return {**config, **original}
    return config
