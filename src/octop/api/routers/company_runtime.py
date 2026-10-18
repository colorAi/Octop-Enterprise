"""Scoped company runtime transport. Never authenticated by browser cookies."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import get_server
from octop.infra.company_runtime import PROTOCOL_VERSION
from octop.infra.company_runtime.service import CompanyRuntimeService, denied
from octop.infra.db.repos.company_runtime import RuntimeConflict
from octop.infra.utils.asyncio import offload

router = APIRouter(prefix="/company-runtime")
ID = r"^[A-Za-z0-9_-]{1,128}$"


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Capabilities(Document):
    protocol_version: int = PROTOCOL_VERSION
    checkpoint_format: str = "langgraph-jsonplus-v1"
    execution: str = "desktop"
    features: list[str] = ["model_stream", "remote_checkpoints", "memory", "knowledge", "events"]


async def service(request: Request, server: Any = Depends(get_server)) -> CompanyRuntimeService:
    if not server.services.config.company_runtime_enabled:
        raise denied("company_runtime_disabled", 404)
    if request.headers.get("origin"):
        raise denied("native_runtime_required", 403)
    raw = request.headers.get("x-octop-device-credential", "")
    if not raw.startswith("octop-device.") or len(raw) > 256:
        raise denied("device_credential_required", 401)
    identity = await offload(
        server.services.device_repo.authenticate,
        hashlib.sha256(raw.encode()).hexdigest(),
        int(time.time()),
    )
    if identity is None:
        raise denied("device_session_expired", 401)
    # Bound all JSON bodies, including chunked requests, before Pydantic consumes them.
    if request.method == "POST" and len(await request.body()) > 16 * 1024 * 1024:
        raise denied("request_too_large", 413)
    return CompanyRuntimeService(server, identity)


@router.get(
    "/capabilities",
    response_model=Capabilities,
    summary="Negotiate company desktop runtime capabilities",
)
async def capabilities(svc: CompanyRuntimeService = Depends(service)) -> Capabilities:
    del svc
    return Capabilities()


class StartTask(Document):
    task_id: str = Field(pattern=ID)
    thread_id: str = Field(pattern=ID)
    agent_id: str = Field(pattern=ID)
    text: str = Field(min_length=1, max_length=1000000)
    edit_message_id: str | None = Field(default=None, max_length=256)
    model: str | None = None
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=100)
    installation_token: str = Field(min_length=32, max_length=256)
    recovery_token: str = Field(min_length=32, max_length=256)


class TaskStarted(Document):
    task_id: str
    status: str
    expert: dict[str, Any]
    project_name: str


@router.post(
    "/tasks",
    response_model=TaskStarted,
    summary="Acquire a single desktop execution for an empty or local thread",
)
async def start(body: StartTask, svc: CompanyRuntimeService = Depends(service)) -> Any:
    return await svc.start(body.model_dump())


class TaskStatus(Document):
    task_id: str
    thread_id: str
    status: str
    updated_at: int


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatus,
    summary="Read authoritative cancellation and task status",
)
async def status(task_id: str, svc: CompanyRuntimeService = Depends(service)) -> Any:
    task = await svc.task(task_id)
    return {k: task[k] for k in TaskStatus.model_fields}


class Transition(Document):
    status: Literal["completed", "failed", "cancelled", "unknown", "cancel_requested"]


@router.post(
    "/tasks/{task_id}/status",
    response_model=Transition,
    summary="Finish or cancel a desktop execution without replay",
)
async def transition(
    task_id: str, body: Transition, svc: CompanyRuntimeService = Depends(service)
) -> Transition:
    await svc.task(task_id)
    try:
        await offload(svc.repo.transition, task_id, body.status)
    except RuntimeConflict as exc:
        raise denied(str(exc)) from exc
    return body


class Event(Document):
    event_id: str = Field(pattern=ID)
    seq: int = Field(ge=1)
    payload: dict[str, Any]


@router.post(
    "/tasks/{task_id}/events",
    response_model=dict[str, Any],
    summary="Append an ordered idempotent execution event and project messages",
)
async def event(task_id: str, body: Event, svc: CompanyRuntimeService = Depends(service)) -> Any:
    return await svc.event(task_id, body.model_dump())


@router.get(
    "/tasks/{task_id}/events",
    response_model=list[dict[str, Any]],
    summary="Reconcile acknowledged events after reconnect",
)
async def events(
    task_id: str, after: int = 0, svc: CompanyRuntimeService = Depends(service)
) -> Any:
    await svc.task(task_id)
    return await offload(svc.repo.events, task_id, after)


class CheckpointRequest(Document):
    namespace: str = Field(default="", max_length=512)
    checkpoint_id: str | None = Field(default=None, max_length=128)
    payload: dict[str, Any] | None = None
    task_key: str = Field(default="", max_length=256)
    writes: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)


@router.post(
    "/tasks/{task_id}/checkpoint/{action}",
    response_model=dict[str, Any] | None,
    summary="Read or persist an opaque scoped LangGraph checkpoint",
)
async def checkpoint(
    task_id: str,
    action: Literal["get", "put", "writes"],
    body: CheckpointRequest,
    svc: CompanyRuntimeService = Depends(service),
) -> Any:
    task = await svc.task(task_id, active=action != "get")
    args = (task["thread_id"], body.namespace, body.checkpoint_id)
    if action == "get":
        return await offload(svc.repo.checkpoint, *args)
    if not body.checkpoint_id:
        raise denied("checkpoint_id_required", 422)
    try:
        if action == "put":
            if body.payload is None:
                raise denied("checkpoint_payload_required", 422)
            await offload(svc.repo.put_checkpoint, *args, body.payload)
        else:
            if any(
                set(w) != {"idx", "value"} or not isinstance(w["idx"], int) for w in body.writes
            ):
                raise denied("invalid_checkpoint_writes", 422)
            await offload(svc.repo.put_writes, *args, body.task_key, body.writes)
    except RuntimeConflict as exc:
        raise denied(str(exc)) from exc
    return {"saved": True}


class ModelRequest(Document):
    model: str = Field(max_length=512)
    messages: list[dict[str, Any]] = Field(max_length=10000)
    tools: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


@router.post(
    "/tasks/{task_id}/model",
    response_class=StreamingResponse,
    summary="Stream company model output; supplier credentials remain server-side",
)
async def model(
    task_id: str, body: ModelRequest, svc: CompanyRuntimeService = Depends(service)
) -> StreamingResponse:
    await svc.task(task_id, active=True)

    async def stream() -> AsyncIterator[str]:
        try:
            async for chunk in svc.model(task_id, body.model_dump()):
                yield chunk
        except Exception as exc:
            reason = (
                exc.details.get("reason", "company_model_failed")
                if hasattr(exc, "details")
                else "company_model_failed"
            )
            yield json.dumps({"error": reason}) + "\n"

    return StreamingResponse(
        stream(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"}
    )


class MemoryRequest(Document):
    query: str = Field(default="", max_length=10000)
    max_results: int = Field(default=5, ge=1, le=20)
    path: str = Field(default="", max_length=512)
    assertion: str = Field(default="", max_length=10000)
    operation_id: str = Field(default="", max_length=128)


@router.post(
    "/tasks/{task_id}/memory/{action}",
    response_model=dict[str, Any],
    summary="Recall or update the employee expert's company memory",
)
async def memory(
    task_id: str,
    action: Literal["search", "get", "remember"],
    body: MemoryRequest,
    svc: CompanyRuntimeService = Depends(service),
) -> Any:
    if action == "remember" and (not body.operation_id or not body.assertion.strip()):
        raise denied("memory_operation_required", 422)
    return await svc.memory(task_id, action, body.model_dump())


class KnowledgeRequest(Document):
    query: str = Field(min_length=1, max_length=10000)
    k: int = Field(default=8, ge=1, le=20)


@router.post(
    "/tasks/{task_id}/knowledge",
    response_model=dict[str, str],
    summary="Search selected company knowledge bases under employee permissions",
)
async def knowledge(
    task_id: str, body: KnowledgeRequest, svc: CompanyRuntimeService = Depends(service)
) -> Any:
    return {"content": await svc.knowledge(task_id, body.model_dump())}


class ImageRequest(Document):
    operation_id: str = Field(pattern=ID)
    prompt: str = Field(min_length=1, max_length=20000)
    size: str | None = Field(default=None, max_length=32)
    reference_images: list[str] = Field(default_factory=list, max_length=4)


@router.post(
    "/tasks/{task_id}/images",
    response_model=dict[str, Any],
    summary="Generate an image once using company configuration",
)
async def generate_image(
    task_id: str, body: ImageRequest, svc: CompanyRuntimeService = Depends(service)
) -> Any:
    from octop.infra.company_runtime.media import generate

    return await generate(svc, task_id, body.model_dump())


@router.get(
    "/tasks/{task_id}/images/{operation}/{file_id}",
    summary="Download an owned generated output with a SHA-256 checksum",
)
async def image_file(
    task_id: str, operation: str, file_id: str, svc: CompanyRuntimeService = Depends(service)
) -> Any:
    from fastapi.responses import Response

    from octop.infra.company_runtime.media import download

    data, media_type = await download(svc, task_id, operation, file_id)
    return Response(
        data,
        media_type=media_type,
        headers={"X-Content-SHA256": hashlib.sha256(data).hexdigest(), "Cache-Control": "no-store"},
    )


class RecoveryRequest(Document):
    installation_token: str = Field(min_length=32, max_length=256)
    recovery_token: str = Field(min_length=32, max_length=256)


@router.post(
    "/tasks/{task_id}/recover",
    response_model=TaskStatus,
    summary="Reconcile owned receipts after login without resuming side effects",
)
async def recover(
    task_id: str, body: RecoveryRequest, svc: CompanyRuntimeService = Depends(service)
) -> Any:
    try:
        task = await offload(
            svc.repo.recover,
            task_id,
            owner=svc.owner,
            device_id=svc.identity["device_id"],
            installation_hash=hashlib.sha256(body.installation_token.encode()).hexdigest(),
            recovery_hash=hashlib.sha256(body.recovery_token.encode()).hexdigest(),
        )
    except RuntimeConflict as exc:
        raise denied(str(exc), 404 if str(exc) == "task_not_found" else 409) from exc
    return {key: task[key] for key in TaskStatus.model_fields}


@router.get(
    "/tasks/{task_id}/skills/{digest}",
    summary="Download one immutable asset from this task's granted skill version",
)
async def skill_asset(
    task_id: str, digest: str, svc: CompanyRuntimeService = Depends(service)
) -> Any:
    from fastapi.responses import Response

    task = await svc.task(task_id, active=True)
    assets = json.loads(task["expert_json"])["skills"]
    if not any(item["sha256"] == digest for item in assets):
        raise denied("skill_not_granted", 403)
    data = await offload((svc.services.paths.root / "company-runtime-skills" / digest).read_bytes)
    if hashlib.sha256(data).hexdigest() != digest:
        raise denied("skill_checksum_mismatch", 409)
    return Response(
        data,
        media_type="application/octet-stream",
        headers={"X-Content-SHA256": digest, "Cache-Control": "private, immutable"},
    )


class CreateThread(Document):
    agent_id: str = Field(pattern=ID)
    title: str = Field(default="", max_length=200)


@router.post(
    "/threads",
    response_model=dict[str, str],
    summary="Create an employee-owned conversation for local execution",
)
async def create_thread(body: CreateThread, svc: CompanyRuntimeService = Depends(service)) -> Any:
    from octop.infra.utils.ulid import new_short_id

    await svc.authorize(body.agent_id)
    thread_id = "thr_" + new_short_id()
    await offload(
        svc.services.thread_repo.insert,
        thread_id=thread_id,
        agent_id=body.agent_id,
        user_id=svc.owner,
        channel_type="dashboard",
        session_key=f"dashboard:{body.agent_id}:{svc.owner}",
        title=body.title or None,
        last_active=0,
    )
    return {"thread_id": thread_id}
