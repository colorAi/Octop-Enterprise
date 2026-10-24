"""Company capabilities enforce grants and survive idle executor eviction."""

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deepagents.backends import FilesystemBackend
from langchain_core.messages import AIMessageChunk, ToolMessage
from octop_harness.backends.workspace import BackendWorkspace
from octop_harness.config import MediaGenerationConfig, MediaProviderConfig

from octop.infra.company_runtime.client import CompanyServiceError
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.capabilities import CAPABILITIES

from .test_resilience import runtime_for
from .test_runtime import acquire, install_agent


@pytest.mark.asyncio
async def test_each_company_endpoint_requires_its_own_grant_before_provider_work(
    company, monkeypatch
):
    server, client, _, _ = company
    install_agent(server)
    await acquire(client)
    resources = SimpleNamespace(has_capability=AsyncMock(return_value=False), authorize=AsyncMock())
    server.employee_resources = resources
    generation = AsyncMock()
    monkeypatch.setattr(
        "octop.infra.company_runtime.service.CompanyRuntimeService.media_agent", generation
    )
    requests = {
        "images": {"operation_id": "image", "prompt": "test"},
        "videos": {"operation_id": "video", "prompt": "test"},
        "speech": {"operation_id": "voice", "text": "test"},
        "transcribe": {
            "audio_base64": base64.b64encode(b"audio").decode(),
            "media_type": "audio/wav",
        },
        "search": {"query": "test"},
    }
    try:
        for endpoint, body in requests.items():
            with pytest.raises(CompanyServiceError, match="_not_assigned") as error:
                await client.request(f"tasks/task1/{endpoint}", body)
            assert error.value.status == 403
        assert {call.args[1] for call in resources.has_capability.await_args_list} == set(
            CAPABILITIES
        )
        generation.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_image_generate_and_download_restore_evicted_worker_once_without_duplicate_generation(
    company, tmp_path, monkeypatch
):
    server, client, _, _ = company
    install_agent(server)
    await acquire(client)
    registry = server.app_runtime.agent_registry
    agent = registry.get_agent("expert")
    root = tmp_path / "outputs"
    root.mkdir()
    agent.workspace = BackendWorkspace(FilesystemBackend(root_dir=root, virtual_mode=True), root)
    agent.config.media_generation = MediaGenerationConfig(
        providers=[MediaProviderConfig(id="test", provider="volcengine", api_key="test")]
    )
    loaded = False

    def get_agent(aid):
        if not loaded:
            raise OctopError(ErrorCode.AGENT_NOT_RUNNING, "evicted")
        return agent

    async def ensure(aid):
        nonlocal loaded
        loaded = True
        return agent

    server.role = "worker"
    registry.get_agent = get_agent
    registry.ensure_runtime = AsyncMock(side_effect=ensure)
    data = b"generated image bytes"

    async def generate(request):
        await agent.workspace.aupload_many([("generated/image.png", data)])
        return SimpleNamespace(
            artifacts=[SimpleNamespace(path="generated/image.png", media_type="image/png")]
        )

    manager = SimpleNamespace(generate_image=AsyncMock(side_effect=generate))
    monkeypatch.setattr(
        "octop.infra.company_runtime.media.create_media_manager", lambda *args: manager
    )
    try:
        body = {"operation_id": "one", "prompt": "test"}
        result = await client.request("tasks/task1/images", body)
        assert await client.request("tasks/task1/images", body) == result
        loaded = False
        file = result["artifacts"][0]
        response = await client.http.get(f"tasks/task1/images/one/{file['file_id']}")
        assert response.status_code == 200 and response.content == data
        assert registry.ensure_runtime.await_count == 3
        manager.generate_image.assert_awaited_once()
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["video", "speech", "transcribe", "search"])
async def test_native_company_capability_tools_and_artifacts(company, tmp_path, monkeypatch, kind):
    server, client, _, _ = company
    install_agent(server)
    agent = server.app_runtime.agent_registry.get_agent("expert")
    root = tmp_path / "company"
    root.mkdir()
    agent.workspace = BackendWorkspace(FilesystemBackend(root_dir=root, virtual_mode=True), root)
    agent.config.media_generation = MediaGenerationConfig(
        providers=[MediaProviderConfig(id="test", provider="volcengine", api_key="test")]
    )
    runtime, events = runtime_for(client, tmp_path)
    project = runtime.workspace / "thread1"
    project.mkdir()
    (project / "input.wav").write_bytes(b"RIFF-test-audio")
    calls = []
    data = b"generated bytes"

    async def video(request):
        calls.append(("video", request.prompt))
        await agent.workspace.aupload_many([("generated/video.mp4", data)])
        return SimpleNamespace(
            artifacts=[SimpleNamespace(path="generated/video.mp4", media_type="video/mp4")]
        )

    async def speech(text, **kwargs):
        calls.append(("speech", text))
        yield data

    async def transcribe(audio, **kwargs):
        calls.append(("transcribe", audio))
        return SimpleNamespace(text="recognized words", confidence=1)

    async def search(body):
        calls.append(("search", body["query"]))
        return "https://example.test source result"

    monkeypatch.setattr(
        "octop.infra.company_runtime.media.create_media_manager",
        lambda *args: SimpleNamespace(generate_video=video),
    )
    monkeypatch.setattr(
        "octop.infra.company_runtime.company_tools.voice",
        lambda svc: SimpleNamespace(
            synthesize=speech, media_type=lambda _: "audio/mpeg", transcribe=transcribe
        ),
    )
    monkeypatch.setattr(
        "octop_harness.builtin.tools.web_search.load_web_search_tools",
        lambda policy: [SimpleNamespace(name="test_search", ainvoke=search)],
    )
    names = {
        "video": "company_generate_video",
        "speech": "company_synthesize_speech",
        "transcribe": "company_transcribe_audio",
        "search": "company_web_search",
    }
    args = {
        "video": {"prompt": "test clip"},
        "speech": {"text": "test speech"},
        "transcribe": {"file_path": "input.wav"},
        "search": {"query": "test query"},
    }

    observed = {"tools": [], "prompts": [], "results": []}

    class Model:
        def bind_tools(self, tools):
            observed["tools"].append([t.get("function", t).get("name") for t in tools])
            return self

        async def astream(self, messages):
            prompt = str(messages[0].content)
            observed["prompts"].append(prompt)
            if any(isinstance(message, ToolMessage) for message in messages):
                observed["results"].extend(
                    message.content
                    for message in messages
                    if isinstance(message, ToolMessage) and message.status == "error"
                )
                yield AIMessageChunk(content="Done", id="answer")
            else:
                yield AIMessageChunk(
                    content="",
                    id="invoke",
                    tool_call_chunks=[
                        {
                            "name": names[kind],
                            "args": json.dumps(args[kind]),
                            "id": "tool1",
                            "index": 0,
                        }
                    ],
                )

    agent.model_factory.get_chat_model = lambda ref: Model()
    try:
        await runtime.start(
            {
                "task_id": "capability-task",
                "thread_id": "thread1",
                "agent_id": "expert",
                "text": "use company capability",
            }
        )
        await asyncio.wait_for(runtime.work["thread1"], 20)
        assert events[-1]["status"] == "completed", json.dumps(events[-1])
        assert not observed["results"], observed
        assert names[kind] in observed["tools"][0], observed
        assert (
            "Image and video generation tools are not configured" not in observed["prompts"][0]
        ), observed["prompts"][0][-1500:]
        assert names[kind] in observed["prompts"][0], observed["prompts"][0][-1500:]
        assert len(calls) == 1 and calls[0][0] == kind
        if kind in {"video", "speech"}:
            assert Path(events[-1]["artifacts"][0]["path"]).read_bytes() == data
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_native_snapshot_hides_unassigned_company_tools(company, tmp_path):
    server, client, _, _ = company
    install_agent(server)
    server.employee_resources = SimpleNamespace(
        authorize=AsyncMock(),
        snapshot=AsyncMock(return_value=None),
        prepare=AsyncMock(),
        granted_capabilities=AsyncMock(return_value=set()),
    )
    observed = []

    class Model:
        def bind_tools(self, tools):
            observed.extend(tool.get("function", tool).get("name") for tool in tools)
            return self

        async def astream(self, messages):
            yield AIMessageChunk(
                content="Ask the administrator to assign company capabilities.", id="answer"
            )

    server.app_runtime.agent_registry.get_agent("expert").model_factory.get_chat_model = (
        lambda ref: Model()
    )
    runtime, events = runtime_for(client, tmp_path)
    try:
        await runtime.start(
            {
                "task_id": "unassigned",
                "thread_id": "thread1",
                "agent_id": "expert",
                "text": "generate media",
            }
        )
        await asyncio.wait_for(runtime.work["thread1"], 15)
        assert events[-1]["status"] == "completed", events[-1]
        assert not {item[4] for item in CAPABILITIES.values()}.intersection(observed)
    finally:
        await runtime.close()
