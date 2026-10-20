"""Long graph histories must not be repeatedly serialized into the WebView."""

import copy
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from octop.infra.company_runtime.ui_stream import project_ui_chunk


@pytest.mark.parametrize("kind", ["state_update", "state_snapshot"])
@pytest.mark.parametrize("wire_format", ["object", "dict", "wrapped"])
def test_large_history_projects_to_usage_without_mutating_graph(kind, wire_format):
    history = [HumanMessage(content="private history " * 100_000)]
    ai = AIMessage(
        content="data:image/png;base64," + "A" * 8_000_000,
        usage_metadata={"input_tokens": 24000, "output_tokens": 100, "total_tokens": 24100},
        additional_kwargs={
            "context_usage": {
                "max_tokens": 64000,
                "input_tokens": 24000,
                "used_tokens": 24000,
                "segments": {
                    "system_prompt": 1000,
                    "tool_definitions": 3000,
                    "conversation": 20000,
                },
            },
            "reasoning_content": "private reasoning" * 100_000,
        },
    )
    history += [ai, ToolMessage(content="result" * 100_000, tool_call_id="tool1")]
    if wire_format == "dict":
        history = [message.model_dump() for message in history]
    elif wire_format == "wrapped":
        history = [{"type": message.type, "data": message.model_dump()} for message in history]
    chunk = {
        "type": kind,
        "node": "model",
        "data": {"messages": history, "large_state": "X" * 100_000},
    }
    original = copy.deepcopy(chunk)
    projected = project_ui_chunk(chunk)
    assert projected is not None
    assert len(json.dumps(projected)) < 1024
    assert projected["data"]["messages"][0]["usage_metadata"]["input_tokens"] == 24000
    assert (
        projected["data"]["messages"][0]["additional_kwargs"]["context_usage"]["segments"][
            "tool_definitions"
        ]
        == 3000
    )
    assert chunk == original


@pytest.mark.parametrize(
    "kind",
    [
        "token",
        "reasoning",
        "tool_call_chunk",
        "tool_result",
        "attachment",
        "usage",
        "error",
        "done",
    ],
)
def test_actual_output_and_lifecycle_are_not_truncated(kind):
    chunk = {"type": kind, "content": "X" * 100_000, "messages": [{"content": "complete result"}]}
    assert project_ui_chunk(chunk) is chunk


def test_empty_or_non_message_state_needs_no_ui_event():
    assert project_ui_chunk({"type": "state_update", "data": {"todos": []}}) is None
    assert (
        project_ui_chunk(
            {"type": "state_snapshot", "data": {"messages": [HumanMessage(content="hello")]}}
        )
        is None
    )
