"""Group admission exercises the same repository on SQLite and isolated Postgres."""

import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from random import Random
from types import SimpleNamespace

import pytest
from PIL import Image
from tests.unit.db.test_execution import fence, seed_execution, submit

from octop.infra.db import migrate
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import PostgresPool, SqlitePool
from octop.infra.db.repos.execution import MAX_PAYLOAD, ExecutionConflict
from octop.infra.db.services import RepoBundle
from octop.infra.group_experts.snapshots import build_snapshot


@pytest.fixture(params=["sqlite", "postgresql"])
def group_pool(request, tmp_path):
    if request.param == "postgresql":
        url = os.environ.get("OCTOP_GROUP_EXPERT_TEST_DATABASE_URL")
        if not url:
            pytest.skip("isolated group-expert PostgreSQL database not configured")
        pool = PostgresPool(url, max_size=4)
        with pool.transaction() as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
    else:
        pool = SqlitePool(tmp_path / "groups.db")
    try:
        yield pool
    finally:
        pool.close()


@pytest.fixture
def group_env(group_pool):
    pool = group_pool
    run_migrations(pool)
    repos = RepoBundle.from_pool(pool)
    execution, user = seed_execution(pool)
    repos.settings_repo.set("execution.mode", "distributed-v1")
    repos.settings_repo.set(
        "execution.workers", json.dumps({"worker": "http://one", "worker2": "http://two"})
    )
    for node, boot in (("worker", "boot1"), ("worker2", "boot2")):
        execution.register(node, boot, "worker", group_expert_protocol=2)
    admin = repos.user_repo.create(username="source-admin", role="admin")
    repos.agent_repo.create(agent_id="group-source", user_id=admin, name="Group expert")
    source = repos.agent_repo.get("group-source")
    repos.expert_assignment_repo.set_source(admin, 0, "source-admin")
    repos.expert_assignment_repo.save(user, ["group-source"], "assigned_only", 0, 1, "source-admin")
    repos.employee_resource_repo.reconcile(user, "department-one", 1, True)
    provider = repos.provider_repo.create(
        name="model-source",
        kind="openai",
        base_url="https://model.invalid/v1",
        api_key="secret-api-key",
        extra_json=json.dumps({"headers": {"Authorization": "secret-header"}}),
        models_json=json.dumps(
            [{"id": "example", "context_window": 8192, "max_output_tokens": 1024}]
        ),
    )
    repos.user_policy_repo.set(
        user, "model_access", json.dumps({"model_refs": ["model-source/example"]})
    )
    group = repos.group_expert_repo
    definition = {
        "source_row_id": source.id,
        "fields": {
            "default_model": "AUTO",
            "system_prompt": "Approved role",
            "name": "Group expert",
        },
        "files": {
            "SOUL.md": "Approved personality",
            "USER.md": "private user",
            "MEMORY.md": "private memory",
            "TOOLS.md": "private tool secret",
        },
        "runtime_settings": {"max_tokens": 500, "temperature": 0.2},
    }
    snapshot = build_snapshot(
        source_agent_id="group-source",
        definition=definition,
        provider=repos.provider_repo.get(provider),
        provider_version=group.provider_version(provider),
        global_model=("model-source", "example"),
    )
    head = repos.source_publication_repo.begin(admin)
    repos.source_publication_repo.publish(
        admin, generation=head["generation"], revision=head["dirty_revision"], digest="a" * 64
    )
    published = group.publish(snapshot, source_manifest="a" * 64, display={"name": "Group expert"})
    versions = {
        "scope:group-one": 1,
        "user:" + str(user): 1,
        f"source:group-source:{source.id}": published["revision"],
        "provider:" + snapshot["provider_generation"]: snapshot["provider_revision"],
    }
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO group_expert_scopes(conversation_id,sources_json) VALUES(?,?)",
            (
                "group-one",
                json.dumps([{"source_agent_id": "group-source", "source_row_id": source.id}]),
            ),
        )
        conn.execute(
            "INSERT INTO department_resource_pools(scope_id,revision,expert_ids) VALUES('department-one',1,?)",
            (json.dumps(["group-source"]),),
        )
        for domain, revision in versions.items():
            conn.execute(
                "INSERT INTO group_expert_authorizations VALUES(?,?) ON CONFLICT(domain_key) DO NOTHING",
                (domain, revision),
            )
        versions.update(
            {
                domain: conn.execute(
                    "SELECT revision FROM group_expert_authorizations WHERE domain_key=?", (domain,)
                ).fetchone()[0]
                for domain in versions
            }
        )
    env = SimpleNamespace(
        pool=pool,
        repos=repos,
        execution=execution,
        group=group,
        user=user,
        admin=admin,
        source=source,
        provider=provider,
        snapshot=snapshot,
        versions=versions,
        definition=definition,
    )
    return env


def context(e, call="call-one"):
    return {
        "call_id": call,
        "user_id": e.user,
        "conversation_id": "group-one",
        "message_id": "message-" + call,
        "binding_id": "binding-one",
        "binding_generation": 1,
        "binding_scope_revision": 1,
        "source_agent_id": "group-source",
        "source_row_id": e.source.id,
        "authorization_versions": e.versions,
        "max_text_chars": 8000,
    }


def run_call(e, call="call-one"):
    receipt = e.group.admit(context(e, call))
    assert receipt["admission_state"] == "accepted", receipt
    e.group.control(call, sequence=1, kind="dispatch")
    node = e.execution.worker_for(e.user)
    run = e.execution.claim(node, "boot1" if node == "worker" else "boot2")
    assert run and run["run_id"] == receipt["run_id"]
    return run


@pytest.mark.parametrize("authority", ["user", "model", "assignment", "department", "source"])
def test_authority_trigger_commits_invalidation_and_regrant_never_revives(group_env, authority):
    e = group_env
    call = context(e)
    receipt = e.group.admit(call)
    assert receipt["admission_state"] == "accepted"
    with e.pool.transaction() as conn:
        if authority == "user":
            conn.execute("UPDATE users SET disabled=1 WHERE id=?", (e.user,))
            conn.execute("UPDATE users SET disabled=0 WHERE id=?", (e.user,))
        elif authority == "model":
            conn.execute(
                "UPDATE user_policies SET enabled=0 WHERE user_id=? AND name='model_access'",
                (e.user,),
            )
            conn.execute(
                "UPDATE user_policies SET enabled=1 WHERE user_id=? AND name='model_access'",
                (e.user,),
            )
        elif authority == "assignment":
            conn.execute("DELETE FROM expert_assignments WHERE user_id=?", (e.user,))
            conn.execute(
                "INSERT INTO expert_assignments(user_id,source_agent_id) VALUES(?,?)",
                (e.user, e.source.agent_id),
            )
        elif authority == "department":
            conn.execute(
                "UPDATE department_resource_pools SET expert_ids='[]' WHERE scope_id='department-one'"
            )
            conn.execute(
                "UPDATE department_resource_pools SET expert_ids=? WHERE scope_id='department-one'",
                (json.dumps([e.source.agent_id]),),
            )
        else:
            conn.execute("UPDATE agents SET enabled=0 WHERE agent_id=?", (e.source.agent_id,))
            conn.execute("UPDATE agents SET enabled=1 WHERE agent_id=?", (e.source.agent_id,))
        domain = (
            f"source:{e.source.agent_id}:{e.source.id}"
            if authority == "source"
            else f"user:{e.user}"
        )
        revision = conn.execute(
            "SELECT revision FROM group_expert_authorizations WHERE domain_key=?", (domain,)
        ).fetchone()[0]
        assert revision > e.versions[domain]
        assert conn.execute(
            "SELECT 1 FROM group_expert_outbox WHERE operation_id=?",
            (f"invalidate:{domain}:{revision}",),
        ).fetchone()
    with pytest.raises(
        ExecutionConflict, match="removed" if authority == "source" else "authorization_changed"
    ):
        e.group.execution_input(call["call_id"])
    e.group.refresh()
    assert e.group.get(call["call_id"])["execution_state"] == "cancelled"


def test_scope_save_idempotence_and_display_projection(group_env):
    e = group_env
    source = {"source_agent_id": e.source.agent_id, "source_row_id": e.source.id}
    request = {
        "actor": e.admin,
        "request_id": "scope-save",
        "conversation_id": "new-group",
        "department_id": None,
        "sources": [source],
        "expected_revision": 0,
    }
    first = e.group.save_scope(**request)
    assert e.group.save_scope(**request) == first
    with pytest.raises(ExecutionConflict, match="identity_conflict"):
        e.group.save_scope(**{**request, "sources": []})
    old = e.group.scope("new-group")
    e.group.publish(e.snapshot, source_manifest="a" * 64, display={"name": "New display name"})
    e.group.refresh()
    new = e.group.scope("new-group")
    assert old["revision"] == new["revision"]
    assert json.loads(new["export_json"])[0]["display_name"] == "New display name"


def test_scope_saves_all_sources_with_legacy_large_portraits(group_env):
    e = group_env
    image = BytesIO()
    Image.frombytes("RGB", (200, 200), Random(10).randbytes(120_000)).save(image, "PNG")
    avatar = {"url": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()}
    assert len(image.getvalue()) < 128 * 1024 < len(avatar["url"])
    sources = []
    for index in range(7):
        agent = f"scope-avatar-{index}"
        e.repos.agent_repo.create(agent_id=agent, user_id=e.admin, name=agent)
        row = e.repos.agent_repo.get(agent)
        source = {"source_agent_id": agent, "source_row_id": row.id}
        e.group.publish(
            {**e.snapshot, **source},
            source_manifest="a" * 64,
            display={"name": agent, "avatar": avatar},
        )
        sources.append(source)
    assert sum(len(avatar["url"]) for _ in sources) > MAX_PAYLOAD

    request = {
        "actor": e.admin,
        "request_id": "scope-with-portraits",
        "conversation_id": "scope-with-portraits",
        "department_id": None,
        "sources": sources,
        "expected_revision": 0,
    }
    saved = e.group.save_scope(**request)
    e.group.refresh()
    assert e.group.save_scope(**request) == saved
    pending = e.group.pending(conversation_id=request["conversation_id"])
    (payload,) = [row["payload"] for row in pending if row["kind"] == "scope"]
    assert len(payload.encode()) <= MAX_PAYLOAD
    exported = json.loads(payload)["sources"]
    assert [
        {k: v for k, v in row.items() if k in {"source_agent_id", "source_row_id"}}
        for row in exported
    ] == sources
    assert all(len(row["authorization_versions"]) == 2 for row in exported)
    assert [row["avatar"] for row in exported].count(avatar) == 6
    assert exported[-1]["avatar"] == {}


def test_admission_is_idempotent_and_shares_personal_queue(group_env):
    e = group_env
    e.execution.configure(running=2, queued=3, user_queued=2)
    personal = submit(e.execution, e.user)
    first = e.group.admit(context(e))
    assert first["admission_state"] == "accepted"
    assert e.group.admit(context(e)) == first
    with pytest.raises(ExecutionConflict, match="identity_conflict"):
        e.group.admit({**context(e), "binding_id": "different"})
    refused = e.group.admit(context(e, "full"))
    assert (
        refused["admission_state"] == "rejected"
        and refused["reason_code"] == "execution_queue_full"
    )
    e.execution.command(personal["run_id"], user=e.user, key="cancel", kind="cancel", payload={})
    assert (
        e.group.admit(context(e, "full")) == refused
    )  # Capacity recovery cannot revive rejection.
    with e.pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM threads WHERE thread_id LIKE 'group-expert:%'"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM execution_runs WHERE operation='group_expert'"
            ).fetchone()[0]
            == 1
        )
    assert e.execution.get(first["run_id"], user=e.user) is None
    assert e.execution.events(first["run_id"], user=e.user) == []
    assert e.execution.by_request(user=e.user, operation="group_expert", key="call-one") is None


def test_dispatch_is_required_before_claim_and_old_worker_cannot_claim(group_env):
    e = group_env
    e.group.admit(context(e))
    for node, boot in (("worker", "boot1"), ("worker2", "boot2")):
        assert e.execution.claim(node, boot) is None
    e.group.control("call-one", sequence=1, kind="dispatch")
    node = e.execution.worker_for(e.user)
    boot = "boot1" if node == "worker" else "boot2"
    e.execution.register(node, boot, "worker")  # Absent advertisement means unsupported.
    assert e.execution.claim(node, boot) is None
    e.execution.register(node, boot, "worker", group_expert_protocol=2)
    assert e.execution.claim(node, boot)["operation"] == "group_expert"


@pytest.mark.parametrize(
    "fault", ["monolith", "missing_worker", "old_worker", "expired", "suspect"]
)
def test_admission_requires_all_configured_workers(group_env, fault):
    e = group_env
    with e.pool.transaction() as conn:
        if fault == "monolith":
            conn.execute("DELETE FROM settings WHERE key='execution.mode'")
        elif fault == "missing_worker":
            conn.execute("DELETE FROM execution_nodes WHERE node_id='worker2'")
        elif fault == "old_worker":
            conn.execute(
                "UPDATE execution_nodes SET group_expert_protocol=0 WHERE node_id='worker2'"
            )
        elif fault == "expired":
            conn.execute("UPDATE execution_nodes SET lease_until=0 WHERE node_id='worker2'")
        else:
            conn.execute("UPDATE execution_nodes SET state='suspect' WHERE node_id='worker2'")
    receipt = e.group.admit(context(e))
    assert receipt["admission_state"] == "rejected"
    assert receipt["reason_code"] == "group_expert_protocol_unavailable"
    assert e.group.admit(context(e)) == receipt


def test_cancel_before_admission_cannot_be_revived_by_late_dispatch(group_env):
    e = group_env
    assert e.group.control("call-one", sequence=2, kind="cancel")["stopped"]
    assert e.group.get("call-one")["admission_state"] == "cancelled"
    assert e.group.get("call-one")["run_id"] is None
    e.group.control("call-one", sequence=1, kind="dispatch")
    receipt = e.group.admit(context(e))
    assert receipt["admission_state"] == "cancelled" and receipt["run_id"] is None
    assert e.group.admit(context(e)) == receipt
    e.group.control("call-one", sequence=3, kind="dispatch")
    with pytest.raises(ExecutionConflict, match="control_conflict"):
        e.group.control("call-one", sequence=2, kind="dispatch")
    with e.pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT dispatch_seq FROM group_expert_controls WHERE call_id='call-one'"
            ).fetchone()[0]
            is None
        )


def test_running_cancellation_retains_occupancy_and_preserves_terminal_result(group_env):
    e = group_env
    run = run_call(e)
    receipt = e.group.control("call-one", sequence=2, kind="cancel")
    assert receipt["saved"] and not receipt["stopped"]
    assert e.execution.active(run["thread_id"])
    e.execution.transition(*fence(run), "cancelled")
    assert e.execution.active(run["thread_id"]) is None
    assert e.group.control("call-one", sequence=2, kind="cancel")["stopped"]
    assert any(row["kind"] == "result" for row in e.group.pending())
    assert e.group.get("call-one")["execution_state"] == "cancelled"


def test_completed_result_and_delivery_obligation_commit_together(group_env):
    e = group_env
    run = run_call(e)
    with pytest.raises(ExecutionConflict, match="result_required"):
        e.execution.transition(*fence(run), "completed")
    assert e.execution.get(run["run_id"])["state"] == "running"
    answer = {"text": "One persisted answer", "truncated": False, "incomplete": False}
    e.execution.transition(*fence(run), "completed", group_result=answer)
    result = next(row for row in e.group.pending() if row["kind"] == "result")
    assert e.group.control("call-one", sequence=2, kind="cancel")["execution_state"] == "completed"
    with e.pool.connect() as conn:
        row = conn.execute(
            "SELECT result_json FROM group_expert_calls WHERE call_id='call-one'"
        ).fetchone()
        assert json.loads(row[0])["result"] == answer
    e.group.acknowledge(
        result["operation_id"],
        request_digest=result["request_digest"],
        receipt={**json.loads(result["payload"]), "delivery_state": "unpublished"},
    )
    e.group.acknowledge(
        result["operation_id"],
        request_digest=result["request_digest"],
        receipt={**json.loads(result["payload"]), "delivery_state": "unpublished"},
    )
    assert e.group.get("call-one")["delivery_state"] == "unpublished"
    with pytest.raises(ExecutionConflict, match="receipt_conflict"):
        e.group.acknowledge(
            result["operation_id"],
            request_digest=result["request_digest"],
            receipt={"stored": False},
        )


def test_unknown_keeps_model_reservation_and_never_acks_a_stop(group_env):
    e = group_env
    run = run_call(e)
    e.execution.reserve_model(
        *fence(run), key="model-once", provider="model-source", tokens=100, quota_remaining=1000
    )
    e.execution.transition(*fence(run), "unknown")
    receipt = e.group.control("call-one", sequence=2, kind="cancel")
    assert receipt["execution_state"] == "unknown" and not receipt["stopped"]
    assert e.execution.active(run["thread_id"])
    with e.pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT state FROM execution_reservations WHERE reservation_id='model-once'"
            ).fetchone()[0]
            == "unknown"
        )
    assert any(row["kind"] == "result" for row in e.group.pending())


def test_provider_changes_are_transactional_and_never_restore_old_snapshot(group_env):
    e = group_env
    initial = e.group.provider_version(e.provider)
    e.repos.provider_repo.update(e.provider, note="display only")
    assert e.group.provider_version(e.provider) == initial
    first = e.group.admit(context(e))
    e.repos.provider_repo.update(e.provider, base_url="https://new.invalid/v1")
    e.repos.provider_repo.update(e.provider, base_url="https://model.invalid/v1")
    assert e.group.provider_version(e.provider)["revision"] == initial["revision"] + 2
    pending = [row for row in e.group.pending() if row["target_id"].startswith("provider:")]
    assert len(pending) == 2
    assert "secret" not in json.dumps(pending)
    e.group.control("call-one", sequence=1, kind="dispatch")
    node = e.execution.worker_for(e.user)
    assert e.execution.claim(node, "boot1" if node == "worker" else "boot2") is None
    assert e.execution.get(first["run_id"])["state"] == "cancelled"
    assert e.group.admit(context(e)) == first  # Original acceptance receipt is immutable.


def test_provider_rollback_and_recreation_keep_identity_isolated(group_env):
    e = group_env
    before = e.group.provider_version(e.provider)
    with pytest.raises(RuntimeError), e.pool.transaction() as conn:
        conn.execute("UPDATE providers SET api_key='replacement' WHERE id=?", (e.provider,))
        raise RuntimeError("rollback")
    assert e.group.provider_version(e.provider) == before
    e.repos.provider_repo.delete(e.provider)
    assert e.group.provider_version(e.provider)["deleted"]
    with e.pool.transaction() as conn:
        conn.execute(
            "INSERT INTO providers(id,name,kind,created_at,updated_at) VALUES(?,'model-source','openai',0,0)",
            (e.provider,),
        )
    assert e.group.provider_version(e.provider)["generation"] != before["generation"]
    with e.pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT revision FROM group_expert_authorizations WHERE domain_key=?",
                ("provider:" + before["generation"],),
            ).fetchone()[0]
            > before["revision"]
        )


def test_source_display_change_does_not_cancel_and_policy_cancel_works_after_disable(group_env):
    e = group_env
    first = e.group.publish(
        e.snapshot, source_manifest="a" * 64, display={"name": "New display name"}
    )
    assert first["revision"] == 1
    accepted = e.group.admit(context(e))
    with e.pool.transaction() as conn:
        conn.execute("UPDATE users SET disabled=1 WHERE id=?", (e.user,))
    e.execution.cancel_user(e.user)
    assert e.execution.get(accepted["run_id"])["state"] == "cancelled"


def test_migration_is_idempotent_without_changing_provider_identity(group_env):
    e = group_env
    before = e.group.provider_version(e.provider)
    run_migrations(e.pool)
    assert e.group.provider_version(e.provider) == before


def test_old_registration_cannot_inherit_new_protocol_advertisement(group_env):
    e = group_env
    with e.pool.transaction() as conn:
        # Older binaries do not mention the new capability column on upsert.
        conn.execute(
            "UPDATE execution_nodes SET incarnation='old-binary-restarted' WHERE node_id='worker2'"
        )
        assert (
            conn.execute(
                "SELECT group_expert_protocol FROM execution_nodes WHERE node_id='worker2'"
            ).fetchone()[0]
            == 0
        )
    assert e.group.admit(context(e))["reason_code"] == "group_expert_protocol_unavailable"


def test_terminal_delivery_survives_retry_exhaustion(group_env):
    e = group_env
    run = run_call(e)
    e.execution.transition(*fence(run), "failed")
    result = next(row for row in e.group.pending() if row["kind"] == "result")
    for _ in range(20):
        e.group.retry(result["operation_id"], request_digest=result["request_digest"])
    assert all(row["operation_id"] != result["operation_id"] for row in e.group.pending())
    with e.pool.transaction() as conn:
        conn.execute(
            "UPDATE group_expert_outbox SET next_attempt_at=0 WHERE operation_id=?",
            (result["operation_id"],),
        )
    restored = next(
        row for row in e.group.pending() if row["operation_id"] == result["operation_id"]
    )
    assert restored["attempts"] == 20 and restored["payload"] == result["payload"]


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "removed",
        "generation",
        "revision",
        "group_department",
        "caller_department",
        "moving",
    ],
)
def test_scope_and_department_changes_block_admission(group_env, change):
    e = group_env
    with e.pool.transaction() as conn:
        if change == "missing":
            conn.execute("DELETE FROM group_expert_scopes")
        elif change == "removed":
            conn.execute("UPDATE group_expert_scopes SET sources_json='[]'")
        elif change == "generation":
            conn.execute(
                "UPDATE group_expert_scopes SET sources_json=?",
                (
                    json.dumps(
                        [{"source_agent_id": "group-source", "source_row_id": e.source.id + 1}]
                    ),
                ),
            )
        elif change == "revision":
            conn.execute("UPDATE group_expert_scopes SET revision=revision+1")
        elif change == "group_department":
            conn.execute("UPDATE group_expert_scopes SET department_id='another-department'")
        elif change == "caller_department":
            conn.execute("UPDATE department_resource_pools SET expert_ids='[]'")
        else:
            conn.execute("UPDATE employee_resource_scopes SET pending_scope='another-department'")
    refused = e.group.admit(context(e))
    assert refused["admission_state"] == "rejected" and refused["run_id"] is None
    with e.pool.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM execution_sessions").fetchone()[0] == 0


def test_concurrent_admission_preserves_one_task(group_env):
    e = group_env
    with ThreadPoolExecutor(max_workers=4) as workers:
        receipts = list(workers.map(lambda _: e.group.admit(context(e)), range(8)))
    assert all(receipt == receipts[0] for receipt in receipts)
    assert receipts[0]["admission_state"] == "accepted"
    with e.pool.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM execution_sessions").fetchone()[0] == 1


def test_undispatched_backlog_does_not_hide_personal_work(group_env):
    e = group_env
    e.execution.configure(running=2, queued=50, user_queued=50)
    for i in range(35):
        assert e.group.admit(context(e, f"waiting-{i}"))["admission_state"] == "accepted"
    personal = submit(e.execution, e.user)
    with e.pool.transaction() as conn:
        conn.execute("UPDATE execution_runs SET created_at=0 WHERE operation='group_expert'")
    node = e.execution.worker_for(e.user)
    claimed = e.execution.claim(node, "boot1" if node == "worker" else "boot2")
    assert claimed and claimed["run_id"] == personal["run_id"]


def test_schema_32_upgrade_preserves_existing_execution_and_provider(group_pool, monkeypatch):
    pool = group_pool
    discover = migrate._discover
    with monkeypatch.context() as patch:
        patch.setattr(
            migrate,
            "_discover",
            lambda dialect="sqlite": [(n, path) for n, path in discover(dialect) if n <= 32],
        )
        run_migrations(pool)
    repos = RepoBundle.from_pool(pool)
    user = repos.user_repo.create(username="upgrade-user", role="user")
    repos.agent_repo.create(agent_id="expert", user_id=user, name="Existing expert")
    repos.thread_repo.insert(
        thread_id="one",
        agent_id="expert",
        user_id=user,
        channel_type="dashboard",
        session_key="one",
    )
    provider = repos.provider_repo.create(
        name="existing",
        kind="openai",
        base_url="https://model.invalid/v1",
        api_key="private-existing-key",
    )
    queued = submit(repos.execution_repo, user)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO execution_nodes(node_id,incarnation,role,state,lease_until,updated_at) VALUES('legacy-worker','old-boot','worker','ready',9999999999,1)"
        )
    tables = (
        "users",
        "agents",
        "threads",
        "providers",
        "execution_runs",
        "execution_sessions",
        "execution_events",
    )
    with pool.connect() as conn:
        before = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]
            for table in tables
        }
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 32
    run_migrations(pool)
    with pool.connect() as conn:
        after = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]
            for table in tables
        }
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 36
        assert (
            conn.execute(
                "SELECT group_expert_protocol FROM execution_nodes WHERE node_id='legacy-worker'"
            ).fetchone()[0]
            == 0
        )
        assert conn.execute("SELECT COUNT(*) FROM group_expert_calls").fetchone()[0] == 0
    assert after == before
    assert repos.execution_repo.get(queued["run_id"], user=user)["state"] == "queued"
    assert repos.group_expert_repo.provider_version(provider)["revision"] == 1
    assert repos.group_expert_repo.pending() == []


@pytest.mark.parametrize(
    "authority", ["source", "source_owner", "source_user", "department", "scope"]
)
def test_binding_revocation_survives_regrant_before_synchronization(group_env, authority):
    e = group_env
    identity = {"source_agent_id": e.source.agent_id, "source_row_id": e.source.id}
    scope = e.group.save_scope(
        actor=e.admin,
        request_id="initial-scope",
        conversation_id="group-one",
        department_id="department-one",
        sources=[identity],
        expected_revision=1,
    )
    original_revision = scope["revision"]
    if authority == "scope":
        scope = e.group.save_scope(
            actor=e.admin,
            request_id="scope-revoke",
            conversation_id="group-one",
            department_id="department-one",
            sources=[],
            expected_revision=original_revision,
        )
        e.group.save_scope(
            actor=e.admin,
            request_id="scope-regrant",
            conversation_id="group-one",
            department_id="department-one",
            sources=[identity],
            expected_revision=scope["revision"],
        )
    else:
        with e.pool.transaction() as conn:
            if authority == "source":
                conn.execute("UPDATE agents SET enabled=0 WHERE agent_id=?", (e.source.agent_id,))
                assert (
                    conn.execute(
                        "SELECT enabled FROM agents WHERE agent_id=?", (e.source.agent_id,)
                    ).fetchone()[0]
                    == 0
                )
                conn.execute("UPDATE agents SET enabled=1 WHERE agent_id=?", (e.source.agent_id,))
            elif authority == "source_owner":
                conn.execute("UPDATE expert_assignment_source SET source_user_id=NULL")
                assert (
                    conn.execute("SELECT source_user_id FROM expert_assignment_source").fetchone()[
                        0
                    ]
                    is None
                )
                conn.execute("UPDATE expert_assignment_source SET source_user_id=?", (e.admin,))
            elif authority == "source_user":
                conn.execute("UPDATE users SET disabled=1 WHERE id=?", (e.admin,))
                assert (
                    conn.execute("SELECT disabled FROM users WHERE id=?", (e.admin,)).fetchone()[0]
                    == 1
                )
                conn.execute("UPDATE users SET disabled=0 WHERE id=?", (e.admin,))
            else:
                conn.execute(
                    "UPDATE department_resource_pools SET expert_ids='[]' WHERE scope_id='department-one'"
                )
                assert (
                    conn.execute(
                        "SELECT expert_ids FROM department_resource_pools WHERE scope_id='department-one'"
                    ).fetchone()[0]
                    == "[]"
                )
                conn.execute(
                    "UPDATE department_resource_pools SET expert_ids=? WHERE scope_id='department-one'",
                    (json.dumps([e.source.agent_id]),),
                )
        if authority in {"source", "source_owner", "source_user"}:
            e.group.publish(e.snapshot, source_manifest="a" * 64, display={"name": "Group expert"})
        e.group.refresh()
    e.versions = e.group.versions(
        user=e.user, conversation_id="group-one", source=e.source.agent_id, source_row=e.source.id
    )
    with e.pool.connect() as conn:
        cutoff = conn.execute(
            "SELECT before_revision FROM group_expert_binding_revocations WHERE conversation_id='group-one'"
        ).fetchone()[0]
        assert cutoff > original_revision
        operation = f"bindings:group-one:{e.source.agent_id}:{e.source.id}:{cutoff}"
        notice = json.loads(
            conn.execute(
                "SELECT payload FROM group_expert_outbox WHERE operation_id=?", (operation,)
            ).fetchone()[0]
        )
        assert notice["revocations"] == [{**identity, "before_revision": cutoff}]
    old = {**context(e), "binding_scope_revision": original_revision}
    assert e.group.admit(old)["reason_code"] == "group_expert_removed"
    assert not e.group.can_call(
        user=e.user,
        conversation_id="group-one",
        source=e.source.agent_id,
        source_row=e.source.id,
        binding_scope_revision=original_revision,
    )
    fresh = {
        **context(e, "reinvited-call"),
        "binding_id": "new-binding",
        "binding_generation": 2,
        "binding_scope_revision": e.versions["scope:group-one"],
    }
    assert e.group.admit(fresh)["admission_state"] == "accepted"
