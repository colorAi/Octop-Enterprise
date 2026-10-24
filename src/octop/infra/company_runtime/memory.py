"""Short-lived company memory access, independent of tool executors."""

import json
from collections.abc import Callable
from typing import Any

from octop.infra.agents.memory.backend import open_memory_kwargs, preserve_memory_layout
from octop.infra.backend.isolation import isolated_config


def with_company_memory[T](server: Any, agent_id: str, operation: Callable[[Any], T]) -> T:
    from octop_memory import Memory

    registry = server.app_runtime.agent_registry
    cfg = isolated_config(registry.get_config(agent_id))
    row = server.services.agent_repo.get(agent_id)
    cfg = preserve_memory_layout(cfg, json.loads(row.config_json or "{}") if row else {})
    if isinstance(cfg.get("memory"), dict) and cfg["memory"].get("memory_enabled") is False:
        return operation(None)
    namespace, backend, options = open_memory_kwargs(
        agent_id=agent_id,
        cfg=cfg,
        octop_config=server.services.config,
        workspace_dir=registry.resolve_workspace_dir(agent_id),
    )
    memory = Memory(namespace=namespace, backend=backend, backend_config=options)
    try:
        return operation(memory)
    finally:
        try:
            checkpoint_connection = getattr(memory._checkpointer, "conn", None)
            if checkpoint_connection is not None:
                checkpoint_connection.close()
        finally:
            memory._backend.close()
