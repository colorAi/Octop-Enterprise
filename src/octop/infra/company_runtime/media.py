"""Idempotent company generation and hash-checked company-origin downloads."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from octop_harness.media.errors import MediaGenerationError
from octop_harness.media.models import ImageGenerationRequest
from octop_harness.media.registry import create_media_manager

from octop.infra.company_runtime.service import CompanyRuntimeService, denied
from octop.infra.db.repos.company_runtime import encode
from octop.infra.utils.asyncio import offload

logger = logging.getLogger(__name__)


async def generate(
    svc: CompanyRuntimeService, task_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    task = await svc.task(task_id, active=True)
    agent = svc.agent(task["agent_id"])
    config = agent.config.media_generation
    if config is None or not config.has_api_key() or not config.image_enabled:
        raise denied("company_image_service_unavailable", 503)
    operation = body["operation_id"]
    references = body.get("reference_images") or []
    # Native callers may supply image bytes, never server file paths or URLs.
    # The workspace normalizer must not read arbitrary company-side files.
    if any(
        not isinstance(reference, str)
        or not reference.startswith(
            ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")
        )
        for reference in references
    ):
        raise denied("company_image_reference_invalid", 422)
    old = await offload(
        svc.repo.begin_operation,
        task_id,
        operation,
        hashlib.sha256(encode(body).encode()).hexdigest(),
    )
    if old:
        if old["status"] == "failed":
            raise denied(json.loads(old["result_json"])["reason"], 502)
        if old["status"] != "completed":
            raise denied("generation_result_unknown")
        return dict(json.loads(old["result_json"]))
    manager = create_media_manager(config, agent.workspace)
    started = time.monotonic()
    try:
        result = await manager.generate_image(
            ImageGenerationRequest(
                prompt=body["prompt"],
                count=1,
                size=body.get("size"),
                reference_images=tuple(references),
            )
        )
    except MediaGenerationError as exc:
        # Keep transport evidence without logging credentials, prompts or URLs.
        logger.warning(
            "Company image failed task=%s operation=%s code=%s http_status=%s "
            "provider_code=%s remediation=%s cause=%s elapsed_seconds=%.1f",
            task_id,
            operation,
            exc.code,
            exc.http_status,
            exc.provider_code,
            exc.remediation,
            type(exc.__cause__).__name__ if exc.__cause__ else None,
            time.monotonic() - started,
        )
        # Preserve the confirmed error and never issue another paid generation
        # for the same operation, including failures while downloading a result.
        reason = (
            "company_image_download_failed"
            if exc.remediation == "retry_download"
            else "company_image_" + exc.code
        )
        await offload(svc.repo.fail_operation, task_id, operation, reason)
        raise denied(reason, 502) from exc
    artifacts = []
    for item in result.artifacts:
        content = await agent.workspace.adownload_bytes(item.path)
        if content is None:
            raise denied("generated_file_unavailable", 503)
        if len(content) > 32 * 1024 * 1024:
            raise denied("generated_file_too_large", 413)
        artifacts.append(
            {
                "file_id": hashlib.sha256(item.path.encode()).hexdigest(),
                "server_path": item.path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "media_type": item.media_type,
            }
        )
    payload = {"status": "completed", "operation_id": operation, "artifacts": artifacts}
    await offload(svc.repo.finish_operation, task_id, operation, payload)
    return payload


async def download(
    svc: CompanyRuntimeService, task_id: str, operation: str, file_id: str
) -> tuple[bytes, str]:
    task = await svc.task(task_id)
    # Lookup only an indexed output of this scoped generation operation.
    record = await offload(svc.repo.operation, task_id, operation)
    if not record or record["status"] != "completed":
        raise denied("generation_result_unknown", 404)
    files = json.loads(record["result_json"]).get("artifacts", [])
    item = next((item for item in files if item["file_id"] == file_id), None)
    if item is None:
        raise denied("generated_file_not_owned", 404)
    content = await svc.agent(task["agent_id"]).workspace.adownload_bytes(item["server_path"])
    if content is None or hashlib.sha256(content).hexdigest() != item["sha256"]:
        raise denied("generated_file_changed", 409)
    return content, item["media_type"]
