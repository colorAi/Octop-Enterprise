"""Company proxy must preserve model capacities and automatic overflow recovery."""

import json
from types import SimpleNamespace

import httpx
import pytest
from deepagents.backends import FilesystemBackend
from deepagents.middleware.summarization import create_summarization_middleware
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately

from octop.infra.company_runtime.client import (
    CompanyChatModel,
    CompanyModelError,
    RemoteCheckpointSaver,
)
from octop.infra.company_runtime.model_context import (
    calibrated_prompt_tokens,
    company_model_profile,
)
from octop.infra.company_runtime.runtime import CompanyModelFactory
from octop.infra.company_runtime.service import model_failure

from .test_resilience import run_turn, runtime_for
from .test_runtime import ToolModel, acquire, install_agent


def test_compaction_uses_model_capacity_expert_cap_and_output_reservation():
    profile = company_model_profile(
        {"context_window": 32768, "max_input_tokens": 32000, "max_output_tokens": 8192},
        {"max_input_length": 40000, "max_tokens": 4096},
    )
    assert profile["max_input_tokens"] == 28672
    assert company_model_profile({}, {"max_input_length": 16000})["max_input_tokens"] == 16000
    factory = CompanyModelFactory(
        None,
        "task1",
        ["local/small", "local/large"],
        model_profiles={
            "local/small": {"context_window": 16000},
            "local/large": {"context_window": 64000},
        },
    )
    assert factory.get_chat_model("local/small").profile["max_input_tokens"] == 16000
    assert factory.get_chat_model("local/large").profile["max_input_tokens"] == 64000
    middleware = create_summarization_middleware(
        factory.get_chat_model("local/small"), FilesystemBackend()
    )
    assert middleware._lc_helper.trigger == ("fraction", 0.85)


@pytest.mark.parametrize(
    "marker", ["context_length_exceeded", "maximum context length", "prompt is too long"]
)
def test_context_error_category_survives_sanitization(marker):
    error = httpx.HTTPStatusError(
        marker + " private provider payload",
        request=httpx.Request("POST", "http://provider.invalid"),
        response=httpx.Response(400),
    )
    assert model_failure(error) == {"error": "company_model_context_overflow", "retryable": False}
    assert (
        model_failure(ContextOverflowError("private details"))["error"]
        == "company_model_context_overflow"
    )


def test_unrelated_bad_request_does_not_trigger_compaction():
    error = httpx.HTTPStatusError(
        "unsupported tool parameter",
        request=httpx.Request("POST", "http://provider.invalid"),
        response=httpx.Response(400),
    )
    assert model_failure(error) == {"error": "company_model_http_400", "retryable": False}


def test_model_errors_do_not_expose_provider_credentials():
    sentinel = "sk-model-failure-regression-only"
    request = httpx.Request("POST", "http://provider.invalid", headers={"Authorization": sentinel})
    errors = [
        RuntimeError(sentinel),
        httpx.ReadTimeout(sentinel, request=request),
        httpx.HTTPStatusError(
            sentinel, request=request, response=httpx.Response(401, text=sentinel)
        ),
    ]
    for error in errors:
        assert sentinel not in json.dumps(model_failure(error))


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["pressure", "overflow", "measured_pressure"])
async def test_real_compactor_summarizes_via_company_proxy_and_continues(
    company, tmp_path, trigger
):
    server, client, _, _ = company
    install_agent(server)
    calls = []
    overflowed = False

    class SummarizingModel(ToolModel):
        profile = {
            "max_input_tokens": 12000 if trigger == "pressure" else 64000,
            "context_window": 64000,
        }

        async def astream(self, messages):
            nonlocal overflowed
            is_summary = any("<messages>" in str(message.content) for message in messages)
            calls.append((is_summary, messages))
            if trigger == "overflow" and not overflowed and not is_summary:
                overflowed = True
                raise ContextOverflowError("prompt is too long: private provider details")
            yield AIMessageChunk(
                content="Task checkpoint: report saved; continue validation."
                if is_summary
                else "Continued successfully."
            )

    provider_model = SummarizingModel()
    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: provider_model
    )
    acquired = await acquire(client)
    assert (
        acquired["expert"]["model_profiles"]["test/model"]["max_input_tokens"]
        == provider_model.profile["max_input_tokens"]
    )
    factory = CompanyModelFactory(
        client, "task1", ["test/model"], model_profiles=acquired["expert"]["model_profiles"]
    )
    model = factory.get_chat_model("test/model")
    messages = []
    for index in range(12):
        cls = HumanMessage if index % 2 == 0 else AIMessage
        messages.append(
            cls(content=f"Step {index}: " + "historical detail " * 400, id=f"message{index}")
        )
    if trigger == "measured_pressure":
        messages[-1].usage_metadata = {
            "input_tokens": 63500,
            "output_tokens": 20,
            "total_tokens": 63520,
        }
        messages[-1].additional_kwargs["context_usage"] = {
            "source": "model_request",
            "segments": {"conversation": count_tokens_approximately(messages)},
        }
    originals = [message.model_dump() for message in messages]
    middleware = create_summarization_middleware(
        model, FilesystemBackend(root_dir=tmp_path / "archive", virtual_mode=True)
    )

    async def handler(request):
        return ModelResponse(result=[await request.model.ainvoke(request.messages)])

    try:
        response = await middleware.awrap_model_call(
            ModelRequest(
                model=model,
                messages=messages,
                system_message=SystemMessage(content="Continue the assigned task."),
                state={"messages": messages},
                runtime=SimpleNamespace(context=None),
            ),
            handler,
        )
        assert response.model_response.result[0].content == "Continued successfully."
        summary_calls = [request for summary, request in calls if summary]
        assert len(summary_calls) == 1
        assert len(calls) == (3 if trigger == "overflow" else 2)
        if trigger == "measured_pressure":
            assert calls[0][0] is True  # Large prompt was compacted before a provider call.
        assert len(calls[-1][1]) < len(messages)
        event = response.command.update["_summarization_event"]
        assert event["cutoff_index"] > 0
        assert event["file_path"]
        assert list((tmp_path / "archive").rglob("*.md"))
        assert [message.model_dump() for message in messages] == originals
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_context_error_after_partial_output_is_terminal(company):
    server, client, _, _ = company
    install_agent(server)

    class PartialModel(ToolModel):
        async def astream(self, messages):
            yield AIMessageChunk(content="Partial answer")
            raise ContextOverflowError("late provider error")

    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: PartialModel()
    )
    await acquire(client)
    model = CompanyChatModel(client=client, task_id="task1", model_ref="test/model")
    try:
        with pytest.raises(CompanyModelError, match="company_model_context_overflow"):
            async for _ in model.astream([HumanMessage(content="task")]):
                pass
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["pressure", "overflow", "length", "persistent_length"])
async def test_long_tool_turn_auto_compacts_and_executes_write_once(company, tmp_path, trigger):
    server, client, _, _ = company
    install_agent(server)
    steps = 0
    summaries = 0
    overflowed = False

    class LongTaskModel(ToolModel):
        profile = {"max_input_tokens": 32768 if trigger == "pressure" else 64000}

        async def astream(self, messages):
            nonlocal steps, summaries, overflowed
            if any("<messages>" in str(message.content) for message in messages):
                summaries += 1
                yield AIMessageChunk(
                    content="Read steps completed. Continue the current task; write the final report once."
                )
                return
            if trigger == "overflow" and steps == 2 and not overflowed:
                overflowed = True
                raise ContextOverflowError("prompt is too long")
            if (
                trigger in {"length", "persistent_length"}
                and steps == 2
                and (not overflowed or trigger == "persistent_length")
            ):
                overflowed = True
                yield AIMessageChunk(
                    content="", additional_kwargs={"reasoning_content": "Working on the task"}
                )
                yield AIMessageChunk(
                    content="",
                    response_metadata={"finish_reason": "length"},
                    usage_metadata={
                        "input_tokens": 63500,
                        "output_tokens": 500,
                        "total_tokens": 64000,
                    },
                )
                return
            steps += 1
            if steps <= 4:
                name, arguments = "read_file", {"file_path": "/large.txt", "limit": 200}
            elif steps == 5:
                name, arguments = (
                    "write_file",
                    {"file_path": "/report.txt", "content": "completed after compaction"},
                )
            else:
                yield AIMessageChunk(content="Task completed after automatic compaction.")
                return
            yield AIMessageChunk(
                content="",
                id=f"model{steps}",
                tool_call_chunks=[
                    {"name": name, "args": json.dumps(arguments), "id": f"call{steps}", "index": 0}
                ],
            )

    provider_model = LongTaskModel()
    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: provider_model
    )
    runtime, events = runtime_for(client, tmp_path)
    project = runtime.workspace / "thread1"
    project.mkdir()
    (project / "large.txt").write_text(("historical detail " * 10 + "\n") * 200)
    try:
        await run_turn(runtime)
        if trigger == "persistent_length":
            assert events[-1]["status"] == "failed", events[-1]
            assert summaries == 1
            assert steps == 2  # Completed reads were not repeated during either model attempt.
            assert not (project / "report.txt").exists()
            return
        assert events[-1]["status"] == "completed", events[-1]
        assert summaries >= 1
        assert steps == 6
        result_ids = {
            message.tool_call_id
            for event in events
            if event.get("data", {}).get("type") == "tool_result"
            for message in event["data"]["messages"]
        }
        assert result_ids == {f"call{index}" for index in range(1, 6)}
        assert (project / "report.txt").read_text() == "completed after compaction"
        receipts = [
            json.loads(path.read_text())
            for path in (runtime.state / "threads/thread1/operations").glob("*.json")
        ]
        assert sum(receipt.get("tool") == "write_file" for receipt in receipts) == 1
        saved = await RemoteCheckpointSaver(client, "task1", "thread1").aget_tuple(
            {"configurable": {"thread_id": "thread1"}}
        )
        assert saved.checkpoint["channel_values"]["_summarization_event"]["cutoff_index"] > 0
        assert server.services.thread_message_repo.head("thread1") >= 12
        assert list((runtime.state / "threads/thread1/context-cache").rglob("*.md"))
    finally:
        await runtime.close()


def test_measured_image_usage_calibrates_pressure_and_shrinks_after_compaction():
    measured = AIMessage(
        content="previous step",
        usage_metadata={"input_tokens": 260554, "output_tokens": 20, "total_tokens": 260574},
        additional_kwargs={
            "context_usage": {"source": "model_request", "segments": {"conversation": 140000}}
        },
    )
    messages = [HumanMessage(content="history " * 80000), measured]
    assert count_tokens_approximately(messages) < 262000 * 0.85
    assert calibrated_prompt_tokens(messages, []) > 262000 * 0.85
    assert calibrated_prompt_tokens([HumanMessage(content="summary"), measured], []) < 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("content,input_tokens", [("", 100), ("partial answer", 63500)])
async def test_length_without_safe_context_recovery_is_not_success(company, content, input_tokens):
    server, client, _, _ = company
    install_agent(server)

    class TruncatedModel(ToolModel):
        async def astream(self, messages):
            yield AIMessageChunk(
                content=content,
                response_metadata={"finish_reason": "length"},
                usage_metadata={
                    "input_tokens": input_tokens,
                    "output_tokens": 100,
                    "total_tokens": input_tokens + 100,
                },
            )

    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: TruncatedModel()
    )
    await acquire(client)
    model = CompanyChatModel(client=client, task_id="task1", model_ref="test/model")
    try:
        with pytest.raises(CompanyModelError, match="company_model_output_truncated"):
            async for _ in model.astream([HumanMessage(content="task")]):
                pass
    finally:
        await client.close()
