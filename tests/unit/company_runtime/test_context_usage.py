"""Native context composition must reach the UI before final company synchronization."""

import json

import pytest

from .test_resilience import run_turn, runtime_for
from .test_runtime import ToolModel, install_agent


@pytest.mark.asyncio
async def test_native_stream_carries_tool_context_breakdown_and_persists_it(company, tmp_path):
    server, client, _, _ = company
    install_agent(server)
    runtime, events = runtime_for(client, tmp_path)
    try:
        await run_turn(runtime)
        snapshots = []
        # Apply the same serializer as the stdio entry point.
        wire_events = json.loads(json.dumps(events, default=lambda obj: obj.model_dump()))
        for event in wire_events:
            chunk = event.get("data", {})
            if chunk.get("type") not in {"state_update", "state_snapshot"}:
                continue
            for message in (chunk.get("data") or {}).get("messages", []):
                extra = message.get("additional_kwargs", {})
                usage = extra.get("context_usage")
                if usage:
                    snapshots.append(usage)
        assert snapshots, "the native stream must include the harness context stamp"
        assert snapshots[-1]["segments"]["tool_definitions"] > 0
        assert snapshots[-1]["segments"]["system_prompt"] > 0
        persisted = server.services.thread_message_repo.last_context_usage("thread1")
        assert persisted["segments"]["tool_definitions"] > 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_stream_preserves_model_limit_and_output_reservation(company, tmp_path):
    server, client, _, _ = company
    install_agent(server)
    server.services.agent_repo.update_config(
        "expert", config_json=json.dumps({"max_input_length": 128000, "max_tokens": 8192})
    )

    class SmallContextModel(ToolModel):
        profile = {"max_input_tokens": 32768, "context_window": 32768}

        def bind(self, **kwargs):
            return self

    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: SmallContextModel()
    )
    runtime, events = runtime_for(client, tmp_path)
    try:
        await run_turn(runtime)
        assert events[-1]["status"] == "completed", events[-1]
        wire = json.loads(json.dumps(events, default=lambda obj: obj.model_dump()))
        limits = [
            message["additional_kwargs"]["context_usage"]["max_tokens"]
            for event in wire
            for message in event.get("data", {}).get("data", {}).get("messages", [])
            if message.get("additional_kwargs", {}).get("context_usage")
        ]
        assert limits and set(limits) == {24576}
    finally:
        await runtime.close()
