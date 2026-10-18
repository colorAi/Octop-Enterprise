"""Image bytes travel gateway -> company workspace -> native project -> receipt."""

import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path

import httpx
import pytest
from deepagents.backends import FilesystemBackend
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from octop_harness.backends.workspace import BackendWorkspace
from octop_harness.config import MediaGenerationConfig, MediaProviderConfig
from octop_harness.media.manager import MediaManager
from octop_harness.media.registry import create_media_provider
from PIL import Image

from octop.infra.agents.providers.media_openai import register_openai_media_provider
from octop.infra.company_runtime.runtime import EnterpriseRuntime
from tests.unit.agents.test_media_openai import multipart_fields

from .test_runtime import install_agent


class ImageModel:
    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        users = [
            index for index, message in enumerate(messages) if isinstance(message, HumanMessage)
        ]
        turn = len(users)
        current = messages[users[-1] + 1 :]
        if any(isinstance(message, ToolMessage) for message in current):
            yield AIMessageChunk(content="Image saved.", id=f"reply-{turn}")
        else:
            yield AIMessageChunk(
                content="",
                id=f"generate-{turn}",
                tool_call_chunks=[
                    {
                        "name": "company_generate_image",
                        "args": json.dumps({"prompt": "test", "filename": f"生成/封面{turn}.png"}),
                        "id": f"image{turn}",
                        "index": 0,
                    }
                ],
            )


@pytest.mark.asyncio
async def test_native_edit_sends_imported_original_and_retains_preview_metadata(
    company, tmp_path, monkeypatch
):
    from langchain_core.messages import messages_from_dict

    from octop.api.routers.chat.serialize import _serialize_history_message

    server, client, _, _ = company
    install_agent(server)
    register_openai_media_provider()
    agent = server.app_runtime.agent_registry.get_agent("expert")
    local_root = tmp_path / "local"
    project = local_root / "thread1"
    project.mkdir(parents=True)
    original = project / "原图.png"
    Image.new("RGB", (12, 8), "blue").save(original)
    data = original.read_bytes()
    receipt = {"path": str(original), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    output = io.BytesIO()
    Image.new("RGB", (12, 8), "red").save(output, "PNG")
    calls = []

    class EditModel(ImageModel):
        async def astream(self, messages):
            if any(isinstance(m, ToolMessage) for m in messages):
                yield AIMessageChunk(content="Edited original saved.", id="edit-answer")
            else:
                yield AIMessageChunk(
                    content="",
                    id="edit-call",
                    tool_call_chunks=[
                        {
                            "name": "company_generate_image",
                            "args": json.dumps(
                                {
                                    "prompt": "change blue to red",
                                    "filename": "edited.png",
                                    "reference_images": [str(original)],
                                    "size": "1024x1024",
                                }
                            ),
                            "id": "edit-image",
                            "index": 0,
                        }
                    ],
                )

    agent.model_factory.get_chat_model = lambda ref: EditModel()
    company_root = tmp_path / "company"
    company_root.mkdir()
    agent.workspace = BackendWorkspace(
        FilesystemBackend(root_dir=company_root, virtual_mode=True), company_root
    )
    config = MediaProviderConfig(
        id="image", provider="openai", api_key="test", base_url="http://gateway.test/v1"
    )
    agent.config.media_generation = MediaGenerationConfig(providers=[config])

    def gateway(request):
        calls.append(request)
        assert request.url.path == "/v1/images/edits"
        assert [value for key, value in multipart_fields(request) if key == "image[]"] == [data]
        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(output.getvalue()).decode()}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as gateway_client:
        manager = MediaManager(agent.workspace, output_dir="generated")
        manager.add_provider(
            "image", create_media_provider(config, client=gateway_client), video_enabled=False
        )
        monkeypatch.setattr(
            "octop.infra.company_runtime.media.create_media_manager", lambda *args: manager
        )
        events = []
        runtime = EnterpriseRuntime(events.append)
        runtime.client = client
        runtime.workspace = local_root
        runtime.state = tmp_path / "state"
        runtime.authorized = True
        runtime.installation_token = "i" * 32
        runtime.tools = {"ffmpeg": "test"}
        await runtime.start(
            {
                "task_id": "edit-task",
                "thread_id": "thread1",
                "agent_id": "expert",
                "text": f"Edit this original [file: {original}]",
                "local_attachments": [receipt],
            }
        )
        await asyncio.wait_for(runtime.work["thread1"], 30)
        assert events[-1]["status"] == "completed", events[-1]
        assert (project / "edited.png").read_bytes() == output.getvalue()
        assert original.read_bytes() == data
        assert len(calls) == 1
        with server.services.db.connect() as conn:
            wire = conn.execute(
                "SELECT message_json FROM thread_messages WHERE thread_id=? AND message_id=?",
                ("thread1", "edit-task_user"),
            ).fetchone()[0]
        restored = messages_from_dict([json.loads(wire)])[0]
        preview = _serialize_history_message(restored)["inbound_attachments"][0]
        assert preview["workspace_path"] == str(original.resolve())
        assert preview["sha256"] == receipt["sha256"]
        assert preview["execution_location"] == "desktop"
        assert preview["kind"] == "image"
        await runtime.close()


@pytest.mark.asyncio
async def test_real_image_materialization_and_native_artifact_receipt(
    company, tmp_path, monkeypatch
):
    server, client, _, _ = company
    install_agent(server)
    register_openai_media_provider()
    agent = server.app_runtime.agent_registry.get_agent("expert")
    agent.model_factory.get_chat_model = lambda ref: ImageModel()
    company_root = tmp_path / "company"
    company_root.mkdir()
    agent.workspace = BackendWorkspace(
        FilesystemBackend(root_dir=company_root, virtual_mode=True), company_root
    )
    config = MediaProviderConfig(id="image", provider="openai", api_key="test", video_enabled=False)
    agent.config.media_generation = MediaGenerationConfig(providers=[config])
    image = io.BytesIO()
    Image.new("RGB", (12, 8), "blue").save(image, "PNG")
    calls = []

    def gateway(request):
        calls.append(request)
        if request.method == "POST":
            assert json.loads(request.content)["response_format"] == "url"
            return httpx.Response(
                200, json={"data": [{"url": "https://images.test/generated.png"}]}
            )
        assert str(request.url) == "https://images.test/generated.png"
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=image.getvalue(), headers={"Content-Type": "image/png"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as gateway_client:
        manager = MediaManager(agent.workspace, output_dir="generated")
        manager.add_provider(
            "image", create_media_provider(config, client=gateway_client), video_enabled=False
        )
        monkeypatch.setattr(
            "octop.infra.company_runtime.media.create_media_manager", lambda *args: manager
        )
        events = []
        runtime = EnterpriseRuntime(events.append)
        runtime.client = client
        runtime.workspace = tmp_path / "local"
        runtime.workspace.mkdir()
        runtime.state = tmp_path / "state"
        runtime.authorized = True
        runtime.installation_token = "i" * 32
        runtime.tools = {"ffmpeg": "test"}
        await runtime.start(
            {
                "task_id": "image-task",
                "thread_id": "thread1",
                "agent_id": "expert",
                "text": "Generate an image",
            }
        )
        await asyncio.wait_for(runtime.work["thread1"], 30)
        done = events[-1]
        assert done["status"] == "completed", done
        assert len(done["artifacts"]) == 1
        target = Path(done["artifacts"][0]["path"])
        assert target.read_bytes() == image.getvalue()
        assert Image.open(target).size == (12, 8)
        assert [request.method for request in calls] == ["POST", "GET"]
        assert server.services.company_runtime_repo.artifacts("thread1") == done["artifacts"]
        first_receipt = done["artifacts"]
        await runtime.start(
            {
                "task_id": "image-task2",
                "thread_id": "thread1",
                "agent_id": "expert",
                "text": "Generate a second image",
            }
        )
        await asyncio.wait_for(runtime.work["thread1"], 30)
        second_done = events[-1]
        assert second_done["status"] == "completed", second_done
        assert len(second_done["artifacts"]) == 1
        assert second_done["artifacts"][0]["path"] != first_receipt[0]["path"]
        assert target.read_bytes() == image.getvalue()
        turns = server.services.company_runtime_repo.artifact_turns("thread1")
        assert [turn["files"] for turn in turns] == [first_receipt, second_done["artifacts"]]
        assert [turn["user_message_id"] for turn in turns] == [
            "image-task_user",
            "image-task2_user",
        ]
        assert all(turn["message_id"] for turn in turns)
        assert [request.method for request in calls] == ["POST", "GET", "POST", "GET"]
        await runtime.close()


@pytest.mark.asyncio
async def test_gateway_failure_success_failure_keeps_error_results_and_turn_order(
    company, tmp_path, monkeypatch, caplog
):
    server, client, _, _ = company
    install_agent(server)
    register_openai_media_provider()
    agent = server.app_runtime.agent_registry.get_agent("expert")

    class FailureAwareModel(ImageModel):
        async def astream(self, messages):
            users = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
            results = [m for m in messages[users[-1] + 1 :] if isinstance(m, ToolMessage)]
            if results and results[-1].status == "error":
                assert "Do not retry" in results[-1].content
                yield AIMessageChunk(content="Image service unavailable.", id=f"reply-{len(users)}")
            else:
                async for chunk in super().astream(messages):
                    yield chunk

    agent.model_factory.get_chat_model = lambda ref: FailureAwareModel()
    company_root = tmp_path / "company"
    company_root.mkdir()
    agent.workspace = BackendWorkspace(
        FilesystemBackend(root_dir=company_root, virtual_mode=True), company_root
    )
    config = MediaProviderConfig(id="image", provider="openai", api_key="private-test-key")
    agent.config.media_generation = MediaGenerationConfig(providers=[config])
    png = io.BytesIO()
    Image.new("RGB", (12, 8), "blue").save(png, "PNG")
    calls = []

    def gateway(request):
        calls.append(request)
        if len(calls) != 2:
            return httpx.Response(503, json={"error": {"code": "upstream_failed"}})
        import base64

        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(png.getvalue()).decode()}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as gateway_client:
        manager = MediaManager(agent.workspace, output_dir="generated")
        manager.add_provider(
            "image", create_media_provider(config, client=gateway_client), video_enabled=False
        )
        monkeypatch.setattr(
            "octop.infra.company_runtime.media.create_media_manager", lambda *args: manager
        )
        events = []
        runtime = EnterpriseRuntime(events.append)
        runtime.client = client
        runtime.workspace = tmp_path / "local"
        runtime.workspace.mkdir()
        runtime.state = tmp_path / "state"
        runtime.authorized = True
        runtime.installation_token = "i" * 32
        runtime.tools = {"ffmpeg": "test"}
        for turn in range(1, 4):
            await runtime.start(
                {
                    "task_id": f"image-task{turn}",
                    "thread_id": "thread1",
                    "agent_id": "expert",
                    "text": "Generate an image" if turn == 1 else "Try again",
                }
            )
            await asyncio.wait_for(runtime.work["thread1"], 30)
            # The conversation finishes with an explanation even when the image failed.
            assert events[-1]["status"] == "completed", events[-1]
            assert len(calls) == turn  # Never automatically replay a paid request.
        with server.services.db.connect() as conn:
            rows = conn.execute(
                "SELECT message_json FROM thread_messages WHERE thread_id=? ORDER BY seq",
                ("thread1",),
            ).fetchall()
        messages = [json.loads(row["message_json"]) for row in rows]
        assert [m["data"]["id"] for m in messages if m["type"] == "human"] == [
            f"image-task{turn}_user" for turn in range(1, 4)
        ]
        assert [m["data"]["status"] for m in messages if m["type"] == "tool"] == [
            "error",
            "success",
            "error",
        ]
        assert [m["data"]["id"] for m in messages if m["type"] in {"human", "ai"}] == [
            ident
            for turn in range(1, 4)
            for ident in (f"image-task{turn}_user", f"generate-{turn}", f"reply-{turn}")
        ]
        assert "http_status=503" in caplog.text and "provider_code=upstream_failed" in caplog.text
        assert "private-test-key" not in caplog.text
        await runtime.close()
