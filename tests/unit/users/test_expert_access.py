from types import SimpleNamespace

import pytest

from octop.config import OctopConfig
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.errors import OctopError
from octop.infra.users.expert_access import ExpertAccess
from octop.infra.users.personal_history import legacy_personal_files, personal_predecessors
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def access_env(tmp_path):
    db = SqlitePool(tmp_path / "access.db")
    run_migrations(db)
    services = build_shared_services(paths=PathLayout(tmp_path), config=OctopConfig(), db=db)
    admin = services.user_repo.create(username="admin", role="admin")
    employee = services.user_repo.create(username="employee", role="user", permissions=["users"])
    other = services.user_repo.create(username="other", role="user")
    for aid, uid in [("source", admin), ("source2", admin), ("old", employee), ("other", other)]:
        services.agent_repo.create(agent_id=aid, user_id=uid, name=aid)
    access = ExpertAccess(services)
    access.configure_source(admin, admin, 0)
    yield SimpleNamespace(
        access=access,
        services=services,
        admin=admin,
        employee=employee,
        other=other,
        db=db,
    )
    db.close()


def test_old_accounts_stay_legacy_new_accounts_wait_for_assignment(access_env):
    env = access_env
    assert not env.access.assigned_only(env.employee)
    fresh = env.services.user_repo.create(username="new", role="user")
    assert env.access.assigned_only(fresh)
    with pytest.raises(OctopError, match="resource_not_assigned"):
        env.access.assert_use(fresh, "source")
    assert env.access.assert_create(env.employee) is False


def test_personal_memory_continuity_requires_unique_same_owner_predecessor(access_env):
    env = access_env
    with env.db.transaction() as conn:
        conn.execute(
            "UPDATE agents SET template_name='general' WHERE agent_id IN ('source','old','other')"
        )
    env.access.save(env.admin, env.employee, ["source"], "assigned_only", 0, 1)
    identity = env.access.repo.reserve(env.employee, "source")["instance_agent_id"]
    env.services.agent_repo.create(agent_id=identity, user_id=env.employee, name="managed")
    assert personal_predecessors(env.services, env.employee, identity) == ["old"]
    assert personal_predecessors(env.services, env.other, identity) == []
    assert personal_predecessors(env.services, env.employee, "source") == []
    # Another earlier expert with the same template makes guessing unsafe.
    with env.db.transaction() as conn:
        conn.execute("UPDATE agents SET user_id=? WHERE agent_id='other'", (env.employee,))
    assert personal_predecessors(env.services, env.employee, identity) == []


def test_legacy_personal_profile_is_server_owned_and_never_crosses_accounts(access_env):
    env = access_env
    root = env.services.paths.agents_dir / "old"
    root.mkdir(parents=True)
    (root / "USER.md").write_text("Employee's original profile", encoding="utf-8")
    env.services.agent_repo.update_config(
        "old", config_json='{"memory":{"backend":{"type":"sqlite"}}}'
    )
    assert legacy_personal_files(env.services, env.employee, "old") == {
        "USER.md": "Employee's original profile"
    }
    assert legacy_personal_files(env.services, env.other, "old") == {}
    assert legacy_personal_files(env.services, env.employee, "source") == {}


def test_source_removal_does_not_restore_new_employees_self_creation(access_env):
    env = access_env
    with env.db.transaction() as conn:
        conn.execute("UPDATE expert_assignment_source SET source_user_id=NULL WHERE id=1")
    fresh = env.services.user_repo.create(username="after-source-removal", role="user")
    assert env.access.assigned_only(fresh)
    with pytest.raises(OctopError, match="read_only"):
        env.access.assert_create(fresh)


def test_modules_do_not_grant_assignment_rights_and_selection_is_atomic(access_env):
    env = access_env
    with pytest.raises(OctopError, match="admin_required"):
        env.access.save(env.employee, env.employee, ["source"], "assigned_only", 0, 1)
    with pytest.raises(OctopError, match="source_unavailable"):
        env.access.save(env.admin, env.employee, ["source", "other"], "assigned_only", 0, 1)
    assert env.access.repo.state(env.employee)["revision"] == 0
    assert env.access.repo.state(env.employee)["expert_ids"] == []
    assert not env.access.assigned_only(env.employee)


def test_only_current_granted_private_instances_can_execute(access_env):
    env = access_env
    state = env.access.save(env.admin, env.employee, ["source"], "assigned_only", 0, 1)
    mapping = env.access.repo.reserve(env.employee, "source")
    instance = mapping["instance_agent_id"]
    env.services.agent_repo.create(agent_id=instance, user_id=env.employee, name="assigned")
    env.access.assert_use(env.employee, instance)
    for target in ("old", "source", "source2", "other"):
        with pytest.raises(OctopError, match="resource_not_assigned"):
            env.access.assert_use(env.employee, target)
    with pytest.raises(OctopError, match="read_only"):
        env.access.assert_create(env.employee, instance)
    with pytest.raises(OctopError, match="read_only"):
        env.access.assert_manage(instance)
    with pytest.raises(OctopError, match="resource_not_assigned"):
        env.access.assert_use(env.other, instance)
    env.access.save(env.admin, env.employee, [], "assigned_only", state["revision"], 1)
    with pytest.raises(OctopError, match="resource_not_assigned"):
        env.access.assert_use(env.employee, instance)
    assert env.services.agent_repo.get(instance) is not None
    assert env.services.agent_repo.get("old") is not None


def test_concurrent_saves_conflict_without_overwriting_newer_selection(access_env):
    env = access_env
    env.access.save(env.admin, env.employee, ["source"], "assigned_only", 0, 1)
    with pytest.raises(OctopError, match="revision_conflict"):
        env.access.save(env.admin, env.employee, ["source2"], "assigned_only", 0, 1)
    assert env.access.repo.state(env.employee)["expert_ids"] == ["source"]
    with pytest.raises(OctopError, match="revision_conflict"):
        env.access.save(env.admin, env.employee, [], "assigned_only", 1, 0)
    assert env.access.repo.state(env.employee)["expert_ids"] == ["source"]


def test_source_disabled_or_removed_admin_role_denies_existing_grant(access_env):
    env = access_env
    env.access.save(env.admin, env.employee, ["source"], "assigned_only", 0, 1)
    instance = env.access.repo.reserve(env.employee, "source")["instance_agent_id"]
    assert env.access.repo.granted(env.employee, "source")
    with env.db.transaction() as conn:
        conn.execute("UPDATE users SET role='user' WHERE id=?", (env.admin,))
    assert not env.access.repo.granted(env.employee, "source")
    with pytest.raises(OctopError, match="resource_not_assigned"):
        env.access.assert_use(env.employee, instance)
    with pytest.raises(OctopError, match="admin_required"):
        env.access.save(env.admin, env.employee, [], "assigned_only", 1, 1)


def test_instance_identity_is_persistent_and_cannot_be_forged_by_config(access_env):
    env = access_env
    env.access.save(env.admin, env.employee, ["source"], "assigned_only", 0, 1)
    first = env.access.repo.reserve(env.employee, "source")
    assert env.access.repo.reserve(env.employee, "source") == first
    assert (
        env.access.repo.reserve(env.other, "source")["instance_agent_id"]
        != first["instance_agent_id"]
    )
    env.services.agent_repo.update_config("old", config_json='{"shared_template_id":"source"}')
    with pytest.raises(OctopError, match="resource_not_assigned"):
        env.access.assert_use(env.employee, "old")


def test_source_switch_requires_explicit_revocation_first(access_env):
    env = access_env
    second_admin = env.services.user_repo.create(username="second-admin", role="admin")
    env.access.save(env.admin, env.employee, ["source"], "assigned_only", 0, 1)
    with pytest.raises(OctopError, match="source_has_assignments"):
        env.access.configure_source(env.admin, second_admin, 1)
    assert env.access.repo.source()["source_user_id"] == env.admin
    env.access.save(env.admin, env.employee, [], "assigned_only", 1, 1)
    env.access.configure_source(env.admin, second_admin, 1)
    assert env.access.repo.source()["source_user_id"] == second_admin


def test_disabled_employee_can_be_revoked_without_reenabling_account(access_env):
    env = access_env
    env.access.save(env.admin, env.employee, ["source"], "assigned_only", 0, 1)
    with env.db.transaction() as conn:
        conn.execute("UPDATE users SET disabled=1 WHERE id=?", (env.employee,))
    with pytest.raises(OctopError, match="employee_required"):
        env.access.save(env.admin, env.employee, ["source2"], "assigned_only", 1, 1)
    result = env.access.save(env.admin, env.employee, [], "assigned_only", 1, 1)
    assert result["expert_ids"] == []
    assert env.services.user_repo.get(env.employee).disabled


def test_recreating_same_public_id_does_not_reuse_or_reauthorize_old_employee_instance(access_env):
    env = access_env
    env.access.save(env.admin, env.employee, ["source"], "assigned_only", 0, 1)
    old = env.access.repo.reserve(env.employee, "source")
    env.services.agent_repo.create(
        agent_id=old["instance_agent_id"], user_id=env.employee, name="Old instance"
    )
    env.services.agent_repo.delete("source")
    env.services.agent_repo.create(
        agent_id="source", user_id=env.admin, name="Different installation"
    )
    env.access.save(env.admin, env.employee, ["source"], "assigned_only", 1, 1)
    new = env.access.repo.reserve(env.employee, "source")
    assert old["instance_agent_id"] != new["instance_agent_id"]
    assert old["source_row_id"] != new["source_row_id"]
    with pytest.raises(OctopError, match="resource_not_assigned"):
        env.access.assert_use(env.employee, old["instance_agent_id"])
    assert env.services.agent_repo.get(old["instance_agent_id"]) is not None


def test_revocation_cancels_local_execution_without_deleting_history(access_env):
    env = access_env
    env.access.save(env.admin, env.employee, ["source"], "assigned_only", 0, 1)
    aid = env.access.repo.reserve(env.employee, "source")["instance_agent_id"]
    env.services.agent_repo.create(agent_id=aid, user_id=env.employee, name="instance")
    env.services.thread_repo.insert(
        thread_id="thread",
        agent_id=aid,
        user_id=env.employee,
        channel_type="dashboard",
        session_key="thread",
        last_active=0,
    )
    with env.db.transaction() as conn:
        conn.execute(
            "INSERT INTO company_runtime_threads VALUES (?,?,?,?,?,?,?)",
            ("thread", env.employee, aid, "device", "project", "hash", 0),
        )
        conn.execute(
            "INSERT INTO company_runtime_tasks(task_id,thread_id,device_id,status,request_json,"
            "expert_json,recovery_hash,started_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("task", "thread", "device", "running", "{}", "{}", "hash", 0, 0),
        )
    env.access.save(env.admin, env.employee, [], "assigned_only", 1, 1)
    assert env.services.company_runtime_repo.task("task")["status"] == "cancel_requested"
    assert env.services.thread_repo.get("thread") is not None


@pytest.mark.asyncio
async def test_assigned_system_instance_bypasses_only_the_self_creation_quota(
    access_env, tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    from deepagents.backends.local_shell import LocalShellBackend
    from octop_harness.backends.workspace import BackendWorkspace

    from octop.infra.agents.manager import AgentCreateSpec, AgentManager

    env = access_env
    env.access.save(env.admin, env.employee, ["source"], "assigned_only", 0, 1)
    env.services.user_policy_repo.set(env.employee, "max_agents", "0")
    manager = AgentManager(repos=env.services.repos, paths=env.services.paths)
    monkeypatch.setattr(manager, "_start_agent", AsyncMock(return_value=None))
    source_root = tmp_path / "source-files"
    source_root.mkdir()
    source_ws = BackendWorkspace(
        LocalShellBackend(root_dir=str(source_root), virtual_mode=False), source_root
    )
    await source_ws.aupload_many([("SOUL.md", b"Approved expert")])
    original_workspace = manager.workspace_for_agent
    monkeypatch.setattr(
        manager,
        "workspace_for_agent",
        lambda aid: source_ws if aid == "source" else original_workspace(aid),
    )
    with pytest.raises(OctopError, match="read_only"):
        await manager.create(AgentCreateSpec(name="Self-created", user_id=env.employee))
    instance = await env.access.instantiate(manager, "source", env.employee)
    assert instance.user_id == env.employee
    assert instance.agent_id != "source"
    assert env.access.repo.instance(instance.agent_id)["state"] == "ready"


@pytest.mark.asyncio
async def test_running_models_and_tools_recheck_the_acting_employee_after_revocation(
    access_env, monkeypatch
):
    from unittest.mock import AsyncMock

    from octop.infra.agents.middleware import expert_access as middleware

    env = access_env
    env.access.save(env.admin, env.employee, ["source"], "assigned_only", 0, 1)
    aid = env.access.repo.reserve(env.employee, "source")["instance_agent_id"]
    env.services.agent_repo.create(agent_id=aid, user_id=env.employee, name="managed")
    monkeypatch.setattr(middleware, "configurable_user_id", lambda: env.employee)
    guard = middleware.ExpertAccessMiddleware(env.services.repos, aid)
    handler = AsyncMock(return_value="allowed")
    assert await guard.awrap_model_call(object(), handler) == "allowed"
    env.access.save(env.admin, env.employee, [], "assigned_only", 1, 1)
    for wrapper in (guard.awrap_model_call, guard.awrap_tool_call):
        with pytest.raises(OctopError, match="resource_not_assigned"):
            await wrapper(object(), handler)
    assert handler.await_count == 1


@pytest.mark.asyncio
async def test_shared_source_runtime_cannot_lend_admin_identity_to_assigned_employee(
    access_env, monkeypatch
):
    from octop.infra.agents.middleware import expert_access as middleware

    env = access_env
    env.access.save(env.admin, env.employee, ["source"], "assigned_only", 0, 1)
    monkeypatch.setattr(middleware, "configurable_user_id", lambda: env.employee)
    guard = middleware.ExpertAccessMiddleware(env.services.repos, "source")
    with pytest.raises(OctopError, match="resource_not_assigned"):
        await guard.enforce()
