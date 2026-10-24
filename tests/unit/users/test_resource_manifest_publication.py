import copy
import json
from unittest.mock import AsyncMock

import pytest
from tests.unit.users.test_employee_resources import resources as resources
from tests.unit.users.test_managed_resource_sync import configure_desktop_model, managed

from octop.infra.agents.middleware.expert_access import ExpertAccessMiddleware
from octop.infra.errors import OctopError
from octop.infra.users import employee_resources
from octop.infra.users.resource_manifest import read_manifest
from octop.infra.utils.immutable_files import read_object


@pytest.mark.asyncio
async def test_definition_and_assets_are_pinned_with_one_durable_change(resources):
    e = resources
    uid, aid, _ = await managed(e)
    await e.agents["source"].workspace.aupload_many(
        [("SOUL.md", b"original definition"), ("USER.md", b"private administrator data")]
    )
    first = await e.service.snapshot(uid, aid)
    cache = e.services.paths.root / "company-runtime-skills"
    manifest = read_manifest(cache, first["manifest_sha256"])
    assert manifest["definition"] == first["definition"]
    assert manifest["validation"] == "legacy_content"
    assert "private administrator data" not in json.dumps(manifest)
    assert "USER.md" not in manifest["definition"]["files"]
    for item in manifest["assets"]:
        assert read_object(cache, item["sha256"]) == first["uploads"][item["path"]]
        assert "content" not in item
    same = await e.service.snapshot(uid, aid)
    assert same["manifest_sha256"] == first["manifest_sha256"]
    assert same["generation"] == 1 and len(e.service.manifests.events()) == 1
    await e.agents["source"].workspace.aupload_many(
        [("SOUL.md", b"new definition"), ("skills/pdf/script.py", b"new script")]
    )
    newer = await e.service.snapshot(uid, aid)
    assert newer["generation"] == 2
    assert newer["manifest_sha256"] != first["manifest_sha256"]
    assert newer["definition"]["files"]["SOUL.md"] == "new definition"
    assert read_manifest(cache, first["manifest_sha256"]) == manifest
    assert len(e.service.manifests.events()) == 2


@pytest.mark.asyncio
async def test_transient_definition_cannot_be_published_under_another_content_hash(
    resources, monkeypatch
):
    e = resources
    uid, aid, _ = await managed(e)
    original = e.service.definition
    calls = 0

    async def transient(*args):
        nonlocal calls
        calls += 1
        definition = copy.deepcopy(await original(*args))
        if calls == 1:
            definition["files"]["SOUL.md"] = "transient intermediate edit"
        return definition

    monkeypatch.setattr(e.service, "definition", transient)
    with pytest.raises(OctopError, match="employee_resources_changed"):
        await e.service.snapshot(uid, aid)
    assert e.service.manifests.head(aid) is None
    assert e.service.manifests.events() == []


@pytest.mark.asyncio
async def test_preparation_uses_the_definition_already_verified_with_its_skills(
    resources, monkeypatch
):
    e = resources
    uid, aid, _ = await managed(e)
    await e.agents["source"].workspace.aupload_many([("SOUL.md", b"captured definition")])
    original = e.service.snapshot

    async def change_after_capture(*args):
        captured = await original(*args)
        await e.agents["source"].workspace.aupload_many([("SOUL.md", b"later definition")])
        return captured

    monkeypatch.setattr(e.service, "snapshot", change_after_capture)
    await e.service.prepare(aid)
    assert (e.workspaces[aid] / "SOUL.md").read_text() == "captured definition"
    handler = AsyncMock()
    with pytest.raises(OctopError, match="employee_resources_changed"):
        await ExpertAccessMiddleware(e.services, aid, lambda: e.service).awrap_model_call(
            object(), handler
        )
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_storage_failure_keeps_previous_pointer_and_employee_files(resources, monkeypatch):
    e = resources
    uid, aid, _ = await managed(e)
    await e.agents["source"].workspace.aupload_many([("SOUL.md", b"old definition")])
    await e.service.prepare(aid)
    previous = e.service.manifests.head(aid)
    await e.agents["source"].workspace.aupload_many([("SOUL.md", b"changed definition")])

    def fail(*args):
        raise OSError("injected manifest write failure")

    monkeypatch.setattr(employee_resources, "store_manifest", fail)
    with pytest.raises(OctopError, match="resource_snapshot_unavailable") as error:
        await e.service.prepare(aid)
    assert error.value.status == 503
    assert e.service.manifests.head(aid) == previous
    assert len(e.service.manifests.events()) == 1
    assert (e.workspaces[aid] / "SOUL.md").read_text() == "old definition"


@pytest.mark.asyncio
async def test_revocation_during_manifest_write_cannot_commit_a_pointer(resources, monkeypatch):
    e = resources
    uid, aid, _ = await managed(e)
    store = employee_resources.store_manifest

    def revoke_before_commit(*args):
        digest = store(*args)
        with e.pool.transaction() as conn:
            conn.execute("DELETE FROM expert_assignments WHERE user_id=?", (uid,))
        return digest

    monkeypatch.setattr(employee_resources, "store_manifest", revoke_before_commit)
    with pytest.raises(OctopError, match="resource_not_assigned"):
        await e.service.snapshot(uid, aid)
    assert e.service.manifests.head(aid) is None
    assert e.service.manifests.events() == []
    assert not (e.workspaces[aid] / "skills/pdf/SKILL.md").exists()


@pytest.mark.asyncio
async def test_desktop_export_reads_source_definitions_from_the_verified_manifest(
    resources, monkeypatch
):
    from octop.infra.company_runtime.service import CompanyRuntimeService

    e = resources
    uid, aid, _ = await managed(e)
    await e.agents["source"].workspace.aupload_many([("SOUL.md", b"verified company definition")])
    await e.agents[aid].workspace.aupload_many([("USER.md", b"employee profile")])
    agent = e.agents[aid]
    configure_desktop_model(e, uid, aid)
    prepare = e.service.prepare

    async def change_private_copy(*args):
        await prepare(*args)
        await agent.workspace.aupload_many([("SOUL.md", b"unverified private copy")])
        e.services.agent_repo.update_config(aid, system_prompt="unverified private prompt")

    monkeypatch.setattr(e.service, "prepare", change_private_copy)
    snapshot = await CompanyRuntimeService(
        e.server, {"owner_user_id": uid, "device_id": "test-device"}
    ).expert(aid)
    assert "verified company definition" in snapshot["instructions"]
    assert "employee profile" in snapshot["instructions"]
    assert "unverified private" not in snapshot["instructions"]
    assert snapshot["resource_manifest_sha256"] == e.service.manifests.head(aid)["manifest_sha256"]


@pytest.mark.asyncio
async def test_backup_roundtrip_keeps_manifest_heads_events_and_asset_bytes(resources, tmp_path):
    from octop.config import DatabaseConfig
    from octop.infra.backup.system_archive import create_system_backup, restore_system_backup
    from octop.infra.db.migrate import run_migrations
    from octop.infra.db.pool import SqlitePool
    from octop.infra.db.repos.resource_manifests import ResourceManifestRepo
    from octop.infra.utils.paths import PathLayout

    e = resources
    uid, aid, _ = await managed(e)
    snapshot = await e.service.snapshot(uid, aid)
    cache = e.services.paths.root / "company-runtime-skills"
    (cache / ".pending-backup-test").write_bytes(b"incomplete draft")
    archive = tmp_path / "manifest-backup.tar.gz"
    create_system_backup(
        paths=e.services.paths,
        pool=e.pool,
        db_config=DatabaseConfig(),
        agent_rows=[],
        dest=archive,
        include_workspaces=False,
        include_chats=False,
    )
    restored_paths = PathLayout(tmp_path / "restored")
    restored_pool = SqlitePool(restored_paths.db)
    try:
        run_migrations(restored_pool)
        restore_system_backup(
            archive,
            paths=restored_paths,
            pool=restored_pool,
            db_config=DatabaseConfig(),
            restore_config=False,
        )
        restored = ResourceManifestRepo(restored_pool)
        assert restored.head(aid) == e.service.manifests.head(aid)
        assert restored.events() == e.service.manifests.events()
        restored_cache = restored_paths.root / "company-runtime-skills"
        manifest = read_manifest(restored_cache, snapshot["manifest_sha256"])
        assert manifest == read_manifest(cache, snapshot["manifest_sha256"])
        for asset in manifest["assets"]:
            assert (
                read_object(restored_cache, asset["sha256"]) == snapshot["uploads"][asset["path"]]
            )
        assert not list(restored_cache.glob(".pending-*"))
    finally:
        restored_pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("assigned", [True, False])
async def test_skill_manifest_keeps_complete_bytes_and_detects_tail_changes(resources, assigned):
    from octop.infra.company_runtime.skills import snapshot_skills

    e = resources
    uid, aid, _ = await managed(e)
    data = b"---\nname: pdf\ndescription: Full manifest\n---\n" + b"x" * 270000 + b"end"
    manifest_path = e.workspaces["source"] / "skills/pdf/SKILL.md"
    manifest_path.write_bytes(data)
    cache = e.services.paths.root / "company-runtime-skills"
    if assigned:
        first = await e.service.snapshot(uid, aid)
        assets = first["assets"]
    else:
        assets = await snapshot_skills(e.agents["source"], cache)
    item = next(asset for asset in assets if asset["path"] == "skills/pdf/SKILL.md")
    assert read_object(cache, item["sha256"]) == data
    assert item["size"] == len(data)
    if assigned:
        manifest_path.write_bytes(data[:-3] + b"new")
        assert await e.service.revision(uid) != first["revision"]


@pytest.mark.asyncio
async def test_definition_limit_rejects_an_incomplete_snapshot(resources):
    e = resources
    uid, aid, _ = await managed(e)
    (e.workspaces["source"] / "SOUL.md").write_text("x" * 262145)
    with pytest.raises(OctopError, match="resource_definition_too_large"):
        await e.service.snapshot(uid, aid)
    assert e.service.manifests.head(aid) is None
