from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from tests.unit.users.test_employee_resources import resources as resources
from tests.unit.users.test_employee_resources import save, selection
from tests.unit.users.test_managed_resource_sync import managed

from octop.infra.agents.middleware.expert_access import ExpertAccessMiddleware
from octop.infra.errors import OctopError
from octop.infra.users.capabilities import CAPABILITIES, TOOL_CAPABILITIES


@pytest.mark.asyncio
async def test_capabilities_are_independent_grants_and_revocation_blocks_server_tools(resources):
    e = resources
    uid, aid, _ = await managed(e)
    catalog = await e.service.catalog()
    records = {s["id"]: s for s in catalog["skills"]}
    assert set(CAPABILITIES) <= records.keys()
    assert all(records[cap]["expert_ids"] == [] for cap in CAPABILITIES)
    assert not await e.service.granted_capabilities(uid)
    assert not (set(CAPABILITIES) & set(catalog["defaults"]))
    pool = e.service.repo.pool("A")
    e.service.repo.save_pool(
        "A",
        pool["expert_ids"],
        [*pool["skill_ids"], *CAPABILITIES],
        pool["revision"],
        e.service.access.repo.source()["revision"],
        "administrator",
    )
    guard = ExpertAccessMiddleware(e.services, aid, lambda: e.service)
    for capability, item in CAPABILITIES.items():
        await save(e, [selection(e, uid, ["source"], [capability])])
        await e.service.prepare(aid)
        assert await e.service.granted_capabilities(uid) == {capability}
        assert not await e.service.granted_capabilities(e.employees[1])
        snapshot = await e.service.snapshot(uid, aid)
        assert f"skills/{item[1]}/SKILL.md" in snapshot["uploads"]
        for tool_name, required in TOOL_CAPABILITIES.items():
            handler = AsyncMock()
            request = SimpleNamespace(tool_call={"name": tool_name, "args": {}})
            if required == capability:
                await guard.awrap_tool_call(request, handler)
                handler.assert_awaited_once()
            else:
                with pytest.raises(OctopError, match="company_capability_not_assigned"):
                    await guard.awrap_tool_call(request, handler)
                handler.assert_not_awaited()
    await save(e, [selection(e, uid, ["source"], [])])
    await e.service.prepare(aid)
    assert not await e.service.granted_capabilities(uid)
    assert await e.service.granted_capabilities(e.admin) == set(CAPABILITIES)


@pytest.mark.asyncio
async def test_voice_controls_enforce_separate_stt_and_tts_grants(resources, monkeypatch):
    import httpx

    from octop.api.routers import voice
    from octop.infra.users.capabilities import STT_CAPABILITY, TTS_CAPABILITY

    e = resources
    uid, _, app = await managed(e)
    e.server.employee_resources = e.service
    app.include_router(voice.router, prefix="/api/voice")
    transcribe = AsyncMock(return_value=SimpleNamespace(text="recognized", confidence=1))

    async def synthesize(*args, **kwargs):
        yield b"speech"

    monkeypatch.setattr(
        voice,
        "_voice_manager",
        lambda server: SimpleNamespace(
            get_active=lambda: {"stt": "configured", "tts": "configured"},
            transcribe=transcribe,
            synthesize=synthesize,
            media_type=lambda provider: "audio/mpeg",
        ),
    )
    pool = e.service.repo.pool("A")
    e.service.repo.save_pool(
        "A",
        pool["expert_ids"],
        [STT_CAPABILITY, TTS_CAPABILITY],
        pool["revision"],
        e.service.access.repo.source()["revision"],
        "administrator",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        active = (await client.get("/api/voice/active")).json()
        assert active["stt_allowed"] is False and active["tts_allowed"] is False
        assert (
            await client.post(
                "/api/voice/stt", files={"audio": ("audio.wav", b"audio", "audio/wav")}
            )
        ).status_code == 403
        assert (await client.post("/api/voice/tts", json={"text": "hello"})).status_code == 403
        transcribe.assert_not_awaited()
        await save(e, [selection(e, uid, ["source"], [STT_CAPABILITY])])
        assert (
            await client.post(
                "/api/voice/stt", files={"audio": ("audio.wav", b"audio", "audio/wav")}
            )
        ).status_code == 200
        assert (await client.post("/api/voice/tts", json={"text": "hello"})).status_code == 403
        await save(e, [selection(e, uid, ["source"], [TTS_CAPABILITY])])
        assert (
            await client.post(
                "/api/voice/stt", files={"audio": ("audio.wav", b"audio", "audio/wav")}
            )
        ).status_code == 403
        response = await client.post("/api/voice/tts", json={"text": "hello"})
        assert response.status_code == 200 and response.content == b"speech"
