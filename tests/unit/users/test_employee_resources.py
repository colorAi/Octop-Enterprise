import copy
import json
from types import SimpleNamespace

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from octop_harness.backends.workspace import BackendWorkspace

from octop.config import OctopConfig
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.errors import OctopError
from octop.infra.skills.skill_package_store import SkillPackageStore
from octop.infra.users.employee_resources import EmployeeResources
from octop.infra.users.expert_access import ExpertAccess
from octop.infra.utils.bounded_io import BoundedIO
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def resources(tmp_path):
    pool = SqlitePool(tmp_path / "main.db")
    run_migrations(pool)
    services = build_shared_services(
        paths=PathLayout(tmp_path / "data"), config=OctopConfig(), db=pool
    )
    admin = services.user_repo.create(username="administrator", role="admin")
    employees = [services.user_repo.create(username=f"employee-{i}", role="user") for i in range(2)]
    services.agent_repo.create(agent_id="source", user_id=admin, name="Document expert")
    access = ExpertAccess(services)
    access.configure_source(admin, admin, 0)
    memberships = {uid: {"department_id": "A", "revision": 0, "enabled": True} for uid in employees}

    class Bridge:
        enabled = True

        def mapped_id(self, uid):
            return f"native:{uid}"

        async def resource_membership(self, uid):
            return copy.deepcopy(memberships[uid])

        async def organization_request(self, actor, session, method, path, body):
            if method == "GET":
                return {"items": [{"department_id": "A"}, {"department_id": "B"}]}
            uid = int(path.split("/")[3].rsplit(":", 1)[-1])
            if path.endswith("/department"):
                assert body["expected_revision"] == memberships[uid]["revision"]
                memberships[uid].update(
                    department_id=body["department_id"], revision=memberships[uid]["revision"] + 1
                )
            return {"updated": True}

        async def resolve_resource_move(self, uid, operation, revision):
            return {"status": "cancelled"}

    workspaces = {}
    agents = {}

    def add_agent(aid):
        root = tmp_path / aid
        root.mkdir(exist_ok=True)
        workspace = BackendWorkspace(
            LocalShellBackend(root_dir=str(root), virtual_mode=False), root
        )
        agent = SimpleNamespace(workspace=workspace, config=SimpleNamespace(skills_dir=None))

        async def summaries():
            return [
                {
                    "slug": p.parent.name,
                    "name": p.parent.name,
                    "description": "Test skill",
                    "path": f"skills/{p.parent.name}/SKILL.md",
                    "kind": "workspace",
                    "enabled": True,
                }
                for p in (root / "skills").glob("*/SKILL.md")
            ]

        agent.list_skill_summaries = summaries
        agent.set_skills_disabled = lambda disabled: setattr(agent, "disabled", disabled)
        workspaces[aid] = root
        agents[aid] = agent
        return agent

    add_agent("source")
    skill_root = workspaces["source"] / "skills/pdf"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: PDF\ndescription: Read PDF\ncategory: Documents\n---\nInstructions"
    )
    (skill_root / "script.py").write_text("print('PDF')")
    manager = workspaces["source"] / "skills/skill-manager"
    manager.mkdir()
    (manager / "SKILL.md").write_text("---\nname: skill-manager\n---\nNot an employee skill")
    registry = SimpleNamespace(get_agent=lambda aid: agents[aid], _active_invocations={})

    async def summaries(aid):
        return await agents[aid].list_skill_summaries()

    registry.list_skill_summaries = summaries
    server = SimpleNamespace(
        management_io=BoundedIO(),
        services=services,
        communication=Bridge(),
        app_runtime=SimpleNamespace(agent_registry=registry),
    )
    service = EmployeeResources(server)
    package_store = SkillPackageStore(
        repo=services.skill_package_repo, root=services.paths.skill_packages_dir
    )
    package = package_store.create(
        name="Installed package", description="Test", created_by=str(admin)
    )
    package_root = package_store.package_skills_dir(package.id)
    for slug in ["slides", "private-sibling"]:
        directory = package_root / slug
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: {slug}\n---\nSelected skill"
        )
    extra = f"package:{package.id}:slides"
    yield SimpleNamespace(
        service=service,
        server=server,
        services=services,
        pool=pool,
        admin=admin,
        employees=employees,
        memberships=memberships,
        agents=agents,
        workspaces=workspaces,
        add_agent=add_agent,
        extra=extra,
    )
    pool.close()


async def prepare(env):
    for uid in env.employees:
        await env.service.synchronize(uid)
    catalog = await env.service.catalog()
    env.service.repo.save_pool("A", ["source"], [env.extra], 0, 1, "administrator")
    return catalog


def selection(env, uid, experts=None, skills=None):
    state = env.service.state(uid)
    return {
        "id": uid,
        "mode": "assigned_only",
        "expert_ids": experts or [],
        "skill_ids": skills or [],
        "expected_revision": state["revision"],
        "scope_id": state["scope_id"],
        "membership_revision": state["membership_revision"],
    }


async def save(env, items):
    return await env.service.save_users(
        env.admin,
        {
            "employees": items,
            "expected_source_revision": env.service.access.repo.source()["revision"],
        },
    )


@pytest.mark.asyncio
async def test_department_cap_is_enforced_and_bulk_save_is_atomic(resources):
    e = resources
    await prepare(e)
    first, second = e.employees
    items = [
        selection(e, first, ["source"], [e.extra]),
        selection(e, second, ["source"], [e.extra]),
    ]
    items[1]["expected_revision"] = 99
    with pytest.raises(OctopError, match="policy_revision_conflict"):
        await save(e, items)
    assert not e.service.state(first)["expert_ids"] and not e.service.state(first)["skill_ids"]
    forbidden = e.extra.replace("slides", "private-sibling")
    with pytest.raises(OctopError, match="resource_outside_department"):
        await save(e, [selection(e, first, ["source"], [forbidden])])
    await save(
        e, [selection(e, first, ["source"], [e.extra]), selection(e, second, ["source"], [e.extra])]
    )
    assert all(e.service.state(uid)["expert_ids"] == ["source"] for uid in e.employees)


@pytest.mark.asyncio
async def test_source_catalog_excludes_management_skills_and_other_installers(resources):
    e = resources
    catalog = await e.service.catalog()
    assert all(x["slug"] != "skill-manager" for x in catalog["skills"])
    assert any(x["id"] == e.extra for x in catalog["skills"])
    store = SkillPackageStore(
        repo=e.services.skill_package_repo, root=e.services.paths.skill_packages_dir
    )
    other = store.create(
        name="Other user's package", description="", created_by=str(e.employees[0])
    )
    directory = store.package_skills_dir(other.id) / "not-installed-by-admin"
    directory.mkdir()
    (directory / "SKILL.md").write_text("---\nname: secret\n---\n")
    assert not any(
        x["id"].startswith(f"package:{other.id}:") for x in (await e.service.catalog())["skills"]
    )


@pytest.mark.asyncio
async def test_builtin_without_harness_path_uses_builtin_mount(resources):
    e = resources
    root = e.workspaces["source"] / "_builtin_skills" / "native-skill"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: Native skill\n---\nBuiltin instructions")
    original = e.agents["source"].list_skill_summaries

    async def summaries():
        return [*await original(), {"slug": "native-skill", "kind": "builtin", "enabled": True}]

    e.agents["source"].list_skill_summaries = summaries
    catalog = await e.service.catalog()
    assert catalog["experts"][0]["available"]
    record = next(x for x in catalog["skills"] if x["id"] == "builtin:native-skill")
    assert (await e.service.files(record))[0][0] == "SKILL.md"


@pytest.mark.asyncio
async def test_mounted_package_catalog_reads_package_store_without_workspace_copy(resources):
    e = resources
    original = e.agents["source"].list_skill_summaries

    async def summaries():
        return [
            *await original(),
            {
                "slug": "slides",
                "kind": "package",
                "package_id": e.extra.split(":")[1],
                "path": "skills/slides/SKILL.md",
                "enabled": True,
            },
        ]

    e.agents["source"].list_skill_summaries = summaries
    assert not (e.workspaces["source"] / "skills/slides/SKILL.md").exists()
    catalog = await e.service.catalog()
    assert catalog["experts"][0]["available"]
    assert e.extra in catalog["experts"][0]["skill_ids"]


@pytest.mark.asyncio
async def test_pool_shrink_prunes_only_extra_permissions_and_never_regrants(resources):
    e = resources
    await prepare(e)
    uid = e.employees[0]
    await save(e, [selection(e, uid, ["source"], [e.extra])])
    affected = e.service.repo.save_pool("A", ["source"], [], 1, 2, "administrator")
    assert affected == [uid]
    assert e.service.state(uid)["expert_ids"] == ["source"]
    assert e.service.state(uid)["skill_ids"] == []
    e.service.repo.save_pool("A", ["source"], [e.extra], 2, 3, "administrator")
    assert e.service.state(uid)["skill_ids"] == []


@pytest.mark.asyncio
async def test_out_of_band_round_trip_clears_even_when_final_department_matches(resources):
    e = resources
    await prepare(e)
    uid = e.employees[0]
    await save(e, [selection(e, uid, ["source"], [e.extra])])
    e.memberships[uid]["revision"] += 2
    await e.service.synchronize(uid)
    assert e.service.state(uid)["expert_ids"] == [] and e.service.state(uid)["skill_ids"] == []


@pytest.mark.asyncio
async def test_prepared_move_retains_intersection_and_intervening_pool_removal_is_remembered(
    resources,
):
    e = resources
    await prepare(e)
    uid = e.employees[0]
    await save(e, [selection(e, uid, ["source"], [e.extra])])
    e.service.repo.save_pool("B", ["source"], [e.extra], 0, 2, "administrator")
    revision = e.service.state(uid)["revision"]
    e.service.repo.prepare_move(uid, "B", 0, revision, 3, "move-one")
    e.service.repo.save_pool("B", [], [], 1, 3, "administrator")
    e.service.repo.save_pool("B", ["source"], [e.extra], 2, 4, "administrator")
    e.memberships[uid].update(department_id="B", revision=1)
    await e.service.synchronize(uid)
    assert e.service.state(uid)["expert_ids"] == [] and e.service.state(uid)["skill_ids"] == []


@pytest.mark.asyncio
async def test_basic_settings_retains_only_common_resources_and_has_stale_form_protection(
    resources,
):
    e = resources
    await prepare(e)
    uid = e.employees[0]
    await save(e, [selection(e, uid, ["source"], [e.extra])])
    e.service.repo.save_pool("B", ["source"], [], 0, 2, "administrator")
    old = e.service.state(uid)
    body = {
        "department_id": "B",
        "job_title_id": None,
        "membership_revision": 0,
        "expected_revision": old["revision"],
        "expected_source_revision": 3,
    }
    state = await e.service.basic(e.admin, uid, "session", body)
    assert (
        state["scope_id"] == "B" and state["expert_ids"] == ["source"] and state["skill_ids"] == []
    )
    with pytest.raises(OctopError, match="organization_revision_conflict"):
        await e.service.basic(e.admin, uid, "session", body)


@pytest.mark.asyncio
async def test_lost_response_recovers_without_unblocking_a_delayed_move(resources):
    e = resources
    await prepare(e)
    uid = e.employees[0]
    e.service.repo.prepare_move(uid, "B", 0, 0, 2, "uncertain-operation")
    with pytest.raises(OctopError, match="organization_change_pending"):
        await e.service.synchronize(uid)
    with e.pool.transaction() as conn:
        row = conn.execute(
            "SELECT pending_json FROM employee_resource_scopes WHERE user_id=?", (uid,)
        ).fetchone()
        value = json.loads(row[0])
        value["created_at"] = 0
        conn.execute(
            "UPDATE employee_resource_scopes SET pending_json=? WHERE user_id=?",
            (json.dumps(value), uid),
        )
    await e.service.synchronize(uid)
    assert e.service.repo.scope(uid)["pending_scope"] is None
    assert e.service.state(uid)["scope_id"] == "A"


@pytest.mark.asyncio
async def test_runtime_receives_only_concrete_defaults_expert_and_extra_files(resources):
    e = resources
    catalog = await prepare(e)
    uid = e.employees[0]
    provided = catalog["experts"][0]["skill_ids"]
    assert len(provided) == 1
    await save(e, [selection(e, uid, ["source"], [e.extra])])
    mapping = e.service.access.repo.reserve(uid, "source")
    aid = mapping["instance_agent_id"]
    e.services.agent_repo.create(agent_id=aid, user_id=uid, name="Employee instance")
    e.add_agent(aid)
    snapshot = await e.service.snapshot(uid, aid)
    paths = [x["path"] for x in snapshot["assets"]]
    assert "skills/pdf/SKILL.md" in paths and "skills/slides/SKILL.md" in paths
    assert not any("private-sibling" in path or "skill-manager" in path for path in paths)
    await e.service.prepare(aid)
    assert (e.workspaces[aid] / "skills/pdf/script.py").is_file()
    assert not (e.workspaces[aid] / "skills/private-sibling").exists()
    old_revision = snapshot["revision"]
    await save(e, [selection(e, uid, ["source"], [])])
    assert await e.service.revision(uid) != old_revision
    with pytest.raises(OctopError, match="employee_resources_changed"):
        await e.service.authorize(uid, aid, prepared=True)
    assert not any(
        "skills/slides/" in x["path"] for x in (await e.service.snapshot(uid, aid))["assets"]
    )


@pytest.mark.asyncio
async def test_administrator_role_is_read_fresh(resources):
    e = resources
    await prepare(e)
    with e.pool.transaction() as conn:
        conn.execute("UPDATE users SET role='user' WHERE id=?", (e.admin,))
    with pytest.raises(OctopError, match="admin_required"):
        await save(e, [selection(e, e.employees[0], ["source"], [])])
