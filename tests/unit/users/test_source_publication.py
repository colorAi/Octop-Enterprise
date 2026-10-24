import json
from unittest.mock import AsyncMock

import pytest
from tests.unit.users.test_employee_resources import resources as resources
from tests.unit.users.test_managed_resource_sync import managed

from octop.infra.errors import OctopError
from octop.infra.users.source_publication import SourcePublication


async def test_publication_in_progress_keeps_catalog_and_employee_available(resources):
    import asyncio

    e = resources
    uid, aid, _ = await managed(e)
    publication = e.service.publication = SourcePublication(e.service)
    await publication.refresh()
    before = await e.service.revision(uid)
    started, finish = asyncio.Event(), asyncio.Event()

    async def save():
        async with publication.mutation():
            await e.agents["source"].workspace.aupload_many([("SOUL.md", b"new version")])
            started.set()
            await finish.wait()

    task = asyncio.create_task(save())
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert publication.repo.head(e.admin)["editing"]
        for _ in range(3):
            assert (await e.service.catalog())["experts"][0]["available"]
            assert await e.service.revision(uid) == before
            await e.service.authorize(uid, aid)
        # Live revocation is still immediate, even while readers retain the snapshot.
        with e.pool.transaction() as conn:
            conn.execute("DELETE FROM expert_assignments WHERE user_id=?", (uid,))
        with pytest.raises(OctopError, match="resource_not_assigned"):
            await e.service.authorize(uid, aid)
    finally:
        finish.set()
        await task
    assert not publication.repo.head(e.admin)["editing"]


async def test_runtime_status_and_noop_saves_do_not_republish_experts(resources):
    from octop.infra.db.migrate import run_migrations

    e = resources
    await managed(e)
    publication = e.service.publication = SourcePublication(e.service)
    await publication.refresh()
    before = publication.repo.head(e.admin)
    # Upgrade an existing v29 database with the old broad trigger, twice.
    with e.pool.transaction() as conn:
        conn.execute("DROP TRIGGER source_agent_update")
        conn.execute(
            "CREATE TRIGGER source_agent_update AFTER UPDATE ON agents BEGIN "
            "UPDATE resource_source_heads SET dirty_revision=dirty_revision+1; END"
        )
    run_migrations(e.pool)
    run_migrations(e.pool)
    with e.pool.transaction() as conn:
        conn.execute(
            "UPDATE agents SET last_state='running',last_error=NULL,updated_at=updated_at+1"
        )
        conn.execute("UPDATE agents SET name=name,config_json=config_json")
    assert publication.repo.head(e.admin) == before
    e.services.agent_repo.update_config("source", name="Updated expert")
    assert publication.repo.head(e.admin)["dirty_revision"] > before["dirty_revision"]


async def test_first_publication_still_reports_preparing(resources):
    publication = resources.service.publication = SourcePublication(resources.service)
    with pytest.raises(OctopError, match="resource_publication_pending"):
        await publication.current()


async def test_previous_publication_upgrades_display_data_without_a_manual_save(
    resources, monkeypatch
):
    e = resources
    await managed(e)
    publication = e.service.publication = SourcePublication(e.service)
    capture = publication.capture

    async def legacy(user):
        value = await capture(user)
        value["format"] = "octop.source-resources.v1"
        for definition in value["definitions"].values():
            definition.pop("presentation")
            definition.pop("avatar")
        return value

    monkeypatch.setattr(publication, "capture", legacy)
    await publication.refresh()
    assert (await publication.current())[1]["format"] == "octop.source-resources.v1"
    monkeypatch.setattr(publication, "capture", capture)
    await publication.refresh()
    assert (await publication.current())[1]["format"] == "octop.source-resources.v3"
    assert "presentation" in (await e.service.definition("source"))


async def test_failed_legacy_publication_exposes_capabilities_without_publishing_drafts(
    resources, monkeypatch
):
    from tests.unit.users.test_employee_resources import save, selection

    from octop.infra.users.capabilities import CAPABILITIES, IMAGE_CAPABILITY

    e = resources
    uid, aid, _ = await managed(e)
    publication = e.service.publication = SourcePublication(e.service)
    capture = publication.capture

    async def legacy(user):
        value = await capture(user)
        value["format"] = "octop.source-resources.v2"
        value["catalog"]["skills"] = [
            record for record in value["catalog"]["skills"] if record["id"] not in CAPABILITIES
        ]
        return value

    monkeypatch.setattr(publication, "capture", legacy)
    await publication.refresh()
    before = await e.service.snapshot(uid, aid)
    with pytest.raises(OSError):
        async with publication.mutation():
            await e.agents["source"].workspace.aupload_many([("SOUL.md", b"unpublished draft")])
            raise OSError("failed save")
    head = publication.repo.head(e.admin)
    assert head["failed"]
    monkeypatch.setattr(publication, "capture", capture)
    publication._cached = None
    await publication.refresh()
    catalog = await e.service.catalog()
    assert set(CAPABILITIES) <= {record["id"] for record in catalog["skills"]}
    pool = e.service.repo.pool("A")
    e.service.repo.save_pool(
        "A",
        pool["expert_ids"],
        [*pool["skill_ids"], IMAGE_CAPABILITY],
        pool["revision"],
        e.service.access.repo.source()["revision"],
        "administrator",
    )
    await save(e, [selection(e, uid, ["source"], [IMAGE_CAPABILITY])])
    snapshot = await e.service.snapshot(uid, aid)
    assert "skills/company-image-generation/SKILL.md" in snapshot["uploads"]
    assert snapshot["definition"] == before["definition"]
    assert publication.repo.head(e.admin) == head


@pytest.mark.parametrize("failed", [False, True])
async def test_http_save_publishes_background_writes_before_acknowledging(resources, failed):
    from types import SimpleNamespace

    import httpx
    from fastapi import BackgroundTasks, FastAPI, HTTPException

    from octop.api.middleware.source_publication import SourcePublicationMiddleware

    e = resources
    uid, aid, _ = await managed(e)
    publication = e.service.publication = SourcePublication(e.service)
    await publication.refresh()
    before = (await e.service.snapshot(uid, aid))["definition"]["files"].get("SOUL.md")
    app = FastAPI()

    @app.patch("/api/agents/source/workspace/file")
    async def save(background: BackgroundTasks):
        await e.agents["source"].workspace.aupload_many([("SOUL.md", b"draft")])
        if failed:
            raise HTTPException(409, "rejected")
        background.add_task(e.agents["source"].workspace.aupload_many, [("SOUL.md", b"finished")])
        return {"saved": True}

    app.add_middleware(
        SourcePublicationMiddleware, server=SimpleNamespace(employee_resources=e.service)
    )

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.octop_user = SimpleNamespace(id=e.admin, role="admin")
        return await call_next(request)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.patch("/api/agents/source/workspace/file")
        assert result.status_code == (409 if failed else 200)
    after = (await e.service.snapshot(uid, aid))["definition"]["files"].get("SOUL.md")
    assert after == (before if failed else "finished")


async def test_expired_publisher_cannot_commit_even_without_replacement(resources):
    from octop.infra.db.repos.source_publication import SourceConflict

    e = resources
    await managed(e)
    repo = e.services.execution_repo
    repo.register("source-host", "boot", "worker")
    repo.reserve_runtime_host("source-host", "boot")
    publication = e.service.publication = SourcePublication(
        e.service, executor=("source-host", "boot")
    )
    await publication.refresh()
    before = publication.repo.head(e.admin)
    with pytest.raises(SourceConflict, match="executor_fenced"):
        async with publication.mutation():
            await e.agents["source"].workspace.aupload_many([("SOUL.md", b"uncommitted")])
            with e.pool.transaction() as conn:
                conn.execute("UPDATE execution_nodes SET lease_until=0")
    assert publication.repo.head(e.admin)["manifest_sha256"] == before["manifest_sha256"]
    assert not publication.repo.head(e.admin)["editing"]


async def test_employee_plugin_write_does_not_publish_source_on_its_worker(resources, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from starlette.responses import Response

    from octop.api.middleware.source_publication import SourcePublicationMiddleware

    e = resources
    _, aid, _ = await managed(e)
    publication = e.service.publication = SourcePublication(e.service)
    mutation = Mock(side_effect=AssertionError("employee worker cannot publish the source"))
    monkeypatch.setattr(publication, "mutation", mutation)
    middleware = SourcePublicationMiddleware(
        Response(), server=SimpleNamespace(employee_resources=e.service)
    )
    await middleware(
        {
            "type": "http",
            "method": "PATCH",
            "path": f"/api/plugins/agents/{aid}/tools",
            "state": {"octop_user": SimpleNamespace(id=e.admin, role="admin")},
        },
        AsyncMock(),
        AsyncMock(),
    )
    mutation.assert_not_called()


async def test_failed_source_switch_does_not_leave_either_source_editing(resources, monkeypatch):
    e = resources
    publication = e.service.publication = SourcePublication(e.service)
    await publication.refresh()
    other = e.services.user_repo.create(username="other-source", role="admin")
    monkeypatch.setattr(publication, "_publish", AsyncMock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        async with publication.mutation():
            policy = e.service.access.repo.source()
            e.service.access.repo.set_source(other, policy["revision"], "admin")
    for user in (e.admin, other):
        head = publication.repo.head(user)
        assert head["failed"] and not head["editing"]


@pytest.mark.asyncio
async def test_saved_source_is_immutable_and_revision_reads_never_scan(resources, monkeypatch):
    e = resources
    uid, aid, _ = await managed(e)
    publication = SourcePublication(e.service)
    e.service.publication = publication
    await publication.refresh()
    initial = await e.service.snapshot(uid, aid)
    before = await e.service.revision(uid)
    # The mutable workspace is now a draft, outside the immutable publication.
    await e.agents["source"].workspace.aupload_many([("skills/pdf/script.py", b"draft")])
    assert await e.service.revision(uid) == before
    assert (await e.service.snapshot(uid, aid))["uploads"] == initial["uploads"]
    async with publication.mutation():
        await e.agents["source"].workspace.aupload_many([("SOUL.md", b"published")])
    assert (await e.service.snapshot(uid, aid))["uploads"]["skills/pdf/script.py"] == b"draft"
    assert await e.service.revision(uid) != before
    monkeypatch.setattr(e.service, "_catalog", AsyncMock(side_effect=AssertionError("scan")))
    monkeypatch.setattr(e.service, "_definition", AsyncMock(side_effect=AssertionError("scan")))
    monkeypatch.setattr(e.service, "_files", AsyncMock(side_effect=AssertionError("scan")))
    for _ in range(5):
        assert await e.service.revision(uid)
    with e.pool.transaction() as conn:
        conn.execute("DELETE FROM expert_assignments WHERE user_id=?", (uid,))
    with pytest.raises(OctopError, match="resource_not_assigned"):
        await e.service.authorize(uid, aid)


@pytest.mark.asyncio
async def test_failed_save_keeps_old_manifest_until_successful_save(resources):
    e = resources
    uid, aid, _ = await managed(e)
    publication = e.service.publication = SourcePublication(e.service)
    await publication.refresh()
    before = await e.service.snapshot(uid, aid)
    with pytest.raises(RuntimeError, match="failed"):
        async with publication.mutation():
            await e.agents["source"].workspace.aupload_many([("SOUL.md", b"partial draft")])
            raise RuntimeError("failed")
    await publication.refresh()
    assert (await e.service.snapshot(uid, aid))["manifest_sha256"] == before["manifest_sha256"]
    async with publication.mutation():
        await e.agents["source"].workspace.aupload_many([("SOUL.md", b"complete save")])
    assert (await e.service.snapshot(uid, aid))["definition"]["files"]["SOUL.md"] == "complete save"


@pytest.mark.asyncio
async def test_offline_definition_edit_keeps_committed_version_until_published(resources):
    e = resources
    uid, aid, _ = await managed(e)
    publication = e.service.publication = SourcePublication(e.service)
    await publication.refresh()
    before = await e.service.revision(uid)
    e.services.agent_repo.update_config("source", system_prompt="offline prompt")
    assert await e.service.revision(uid) == before
    await publication.refresh()
    result = await e.service.snapshot(uid, aid)
    assert result["definition"]["fields"]["system_prompt"] == "offline prompt"
    assert "offline prompt" in json.dumps(result["definition"])


@pytest.mark.asyncio
async def test_revoke_source_during_publication_never_advances_pointer(resources, monkeypatch):
    e = resources
    await managed(e)
    publication = e.service.publication = SourcePublication(e.service)
    await publication.refresh()
    before = publication.repo.head(e.admin)
    capture = publication.capture

    async def revoke(*args):
        result = await capture(*args)
        e.services.user_repo.set_disabled(e.admin, True)
        return result

    monkeypatch.setattr(publication, "capture", revoke)
    with pytest.raises(OctopError, match="resource_source_changed"):
        async with publication.mutation():
            pass
    assert publication.repo.head(e.admin)["manifest_sha256"] == before["manifest_sha256"]


async def test_many_cold_experts_publish_and_assign_with_one_source_read_slot(resources):
    from contextlib import asynccontextmanager

    e = resources
    uid, aid, _ = await managed(e)
    ids = ["source"]
    for index in range(39):
        expert_id = f"cold-expert-{index}"
        e.services.agent_repo.create(agent_id=expert_id, user_id=e.admin, name=expert_id)
        agent = e.add_agent(expert_id)
        await agent.workspace.aupload_many(
            [
                ("SOUL.md", expert_id.encode()),
                ("skills/example/SKILL.md", b"---\nname: example\n---\nInstructions"),
                ("skills/example/data.txt", expert_id.encode()),
            ]
        )
        ids.append(expert_id)
    registry = e.server.app_runtime.agent_registry
    opened = []
    live = 0

    @asynccontextmanager
    async def source_workspace(expert_id):
        nonlocal live
        assert live == 0, "publication must not retain one sandbox per expert"
        live += 1
        opened.append(expert_id)
        try:
            yield e.agents[expert_id].workspace
        finally:
            live -= 1

    async def summaries(expert_id, *, workspace):
        assert live == 1 and workspace is e.agents[expert_id].workspace
        return await e.agents[expert_id].list_skill_summaries()

    def unloaded(expert_id):
        raise AssertionError("source publication must not require a running expert")

    registry.source_workspace = source_workspace
    registry.list_skill_summaries = summaries
    registry.get_agent = unloaded
    publication = e.service.publication = SourcePublication(e.service)
    await publication.refresh()
    digest, current = await publication.current()
    assert set(current["definitions"]) == set(ids)
    assert all(expert["available"] for expert in current["catalog"]["experts"])
    assert set(opened) == set(ids) and live == 0
    assert current["definitions"][ids[-1]]["files"]["SOUL.md"] == ids[-1]
    before = len(opened)
    snapshot = await e.service.snapshot(uid, aid)
    assert snapshot["uploads"]["skills/pdf/script.py"] == b"print('PDF')"
    assert len(opened) == before, "employee reads use the complete immutable publication"
    async with publication.mutation():
        await e.agents[ids[-1]].workspace.aupload_many([("SOUL.md", b"updated")])
    assert (await publication.current())[0] != digest
    assert live == 0
