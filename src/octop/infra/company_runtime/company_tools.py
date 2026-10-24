"""Authorized company search and voice services for local tasks."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
from typing import Any

from octop.infra.company_runtime.service import CompanyRuntimeService, denied
from octop.infra.db.repos.company_runtime import encode
from octop.infra.users.capabilities import SEARCH_CAPABILITY, STT_CAPABILITY, TTS_CAPABILITY
from octop.infra.utils.asyncio import offload
from octop.infra.voice.manager import VoiceManager


def voice(svc: CompanyRuntimeService) -> VoiceManager:
    return VoiceManager(
        settings_repo=svc.services.settings_repo,
        voice_provider_repo=svc.services.voice_provider_repo,
    )


async def search(svc: CompanyRuntimeService, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
    await svc.require_capability(task_id, SEARCH_CAPABILITY)
    from octop_harness.builtin.tools.web_search import load_web_search_tools

    available = await offload(load_web_search_tools, "auto")
    if not available:
        raise denied("company_web_search_service_unavailable", 503)
    # Registry order prefers configured company providers over the public fallback.
    try:
        result = await asyncio.wait_for(available[0].ainvoke({"query": body["query"]}), 90)
    except Exception as exc:
        raise denied("company_web_search_failed", 502) from exc
    return {"content": str(result)[:40000], "provider": available[0].name}


async def transcribe(
    svc: CompanyRuntimeService, task_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    await svc.require_capability(task_id, STT_CAPABILITY)
    try:
        data = base64.b64decode(body["audio_base64"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise denied("company_speech_input_invalid", 422) from exc
    if not data or len(data) > 25 * 1024 * 1024:
        raise denied("company_speech_input_invalid", 422)
    result = await voice(svc).transcribe(
        data, mime=body["media_type"], language=body.get("language", "zh-CN")
    )
    return {"text": result.text, "confidence": result.confidence}


async def synthesize(
    svc: CompanyRuntimeService, task_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    task = await svc.require_capability(task_id, TTS_CAPABILITY)
    agent = await svc.media_agent(task["agent_id"])
    operation = body["operation_id"]
    old = await offload(
        svc.repo.begin_operation,
        task_id,
        operation,
        hashlib.sha256(encode({**body, "kind": "speech"}).encode()).hexdigest(),
    )
    if old:
        if old["status"] == "failed":
            raise denied(json.loads(old["result_json"])["reason"], 502)
        if old["status"] != "completed":
            raise denied("generation_result_unknown")
        return dict(json.loads(old["result_json"]))
    manager = voice(svc)
    try:
        media_type = manager.media_type(None)
        data = bytearray()
        async for chunk in manager.synthesize(
            body["text"], voice_id=body.get("voice_id"), speed=body.get("speed", 1)
        ):
            data.extend(chunk)
            if len(data) > 32 * 1024 * 1024:
                raise denied("generated_file_too_large", 413)
        if not data:
            raise denied("company_text_to_speech_failed", 502)
        extension = ".wav" if media_type == "audio/wav" else ".mp3"
        path = f"generated/speech-{hashlib.sha256((task_id + operation).encode()).hexdigest()}{extension}"
        await agent.workspace.aupload_many([(path, bytes(data))])
        result = {
            "status": "completed",
            "operation_id": operation,
            "artifacts": [
                {
                    "file_id": hashlib.sha256(path.encode()).hexdigest(),
                    "server_path": path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                    "media_type": media_type,
                }
            ],
        }
        await offload(svc.repo.finish_operation, task_id, operation, result)
        return result
    except Exception as exc:
        await offload(svc.repo.fail_operation, task_id, operation, "company_text_to_speech_failed")
        raise denied("company_text_to_speech_failed", 502) from exc
