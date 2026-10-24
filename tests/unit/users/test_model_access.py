import json
from types import SimpleNamespace

import pytest

from octop.config import OctopConfig
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.model_access import MODEL_ACCESS_POLICY, ModelAccess
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def model_env(tmp_path):
    db = SqlitePool(tmp_path / "models.db")
    run_migrations(db)
    services = build_shared_services(db=db, paths=PathLayout(tmp_path), config=OctopConfig())
    admin = services.user_repo.create(username="admin", role="admin")
    employee = services.user_repo.create(username="employee", role="user")
    manager = services.user_repo.create(username="manager", role="user", permissions=["users"])
    alpha = services.provider_repo.create(
        name="alpha",
        kind="openai",
        base_url="http://provider.invalid/v1",
        api_key="secret-sentinel",
        models_json=json.dumps(
            [
                {"id": "chat", "name": "Chat", "input": ["text"]},
                {"id": "vision", "name": "Vision", "input": ["text", "image"]},
                {"id": "disabled", "enabled": False},
                {"id": "text-embedding-test", "embedding": True},
            ]
        ),
    )
    services.provider_repo.create(
        name="beta",
        kind="openai",
        base_url="http://other.invalid/v1",
        api_key="other-secret",
        models_json='[{"id":"chat","name":"Chat"}]',
    )
    services.settings_repo.set_active_model("alpha", "chat")
    yield SimpleNamespace(
        services=services,
        admin=admin,
        employee=employee,
        manager=manager,
        alpha=alpha,
        access=ModelAccess(services),
    )
    db.close()


def grant(env, refs):
    return env.access.set(
        env.employee, refs, env.access.get(env.employee)["revision"], actor="admin"
    )


def test_model_access_defaults_to_none_and_administrators_keep_all(model_env):
    e = model_env
    refs = [row["ref"] for row in e.access.catalog()]
    assert set(refs) == {"alpha/chat", "alpha/vision", "beta/chat"}
    assert e.access.filter_refs(e.employee, refs) == []
    assert e.access.filter_refs(e.admin, refs) == refs
    with pytest.raises(OctopError) as exc:
        e.access.resolve(e.employee)
    assert exc.value.code == ErrorCode.MODEL_NOT_ASSIGNED
    assert exc.value.status == 403
    assert "secret-sentinel" not in json.dumps(e.access.get(e.employee))


def test_assignments_are_provider_scoped_and_revocation_is_immediate(model_env):
    e = model_env
    assigned = grant(e, ["beta/chat", "beta/chat"])
    assert assigned["model_refs"] == ["beta/chat"]
    assert e.access.filter_refs(e.employee, ["alpha/chat", "beta/chat"]) == ["beta/chat"]
    assert e.access.resolve(e.employee, "alpha/chat") == "beta/chat"
    with pytest.raises(OctopError):
        e.access.require(e.employee, "alpha/chat")
    e.access.require(e.employee, "beta/chat")
    grant(e, [])
    with pytest.raises(OctopError):
        e.access.require(e.employee, "beta/chat")
    audit = e.services.audit_repo.query(action="employee.models")
    assert len(audit) == 2 and all(row.actor == "admin" for row in audit)


@pytest.mark.parametrize("ref", ["missing/model", "alpha/disabled", "alpha/text-embedding-test"])
def test_only_available_chat_models_can_be_assigned(model_env, ref):
    e = model_env
    with pytest.raises(OctopError):
        grant(e, [ref])
    assert e.access.get(e.employee)["model_refs"] == []


def test_stale_model_forms_cannot_overwrite_current_assignments(model_env):
    e = model_env
    grant(e, ["alpha/chat"])
    with pytest.raises(OctopError) as exc:
        e.access.set(e.employee, ["beta/chat"], "0", actor="admin")
    assert exc.value.status == 409
    assert e.access.get(e.employee)["model_refs"] == ["alpha/chat"]


def test_vision_fallback_stays_within_grants_and_disabled_providers_disappear(model_env):
    e = model_env
    grant(e, ["alpha/chat", "beta/chat"])
    with pytest.raises(OctopError):
        e.access.resolve(e.employee, "alpha/chat", needs_multimodal=True)
    grant(e, ["alpha/chat", "alpha/vision"])
    assert e.access.resolve(e.employee, "alpha/chat", needs_multimodal=True) == "alpha/vision"
    e.services.provider_repo.update(e.alpha, enabled=False)
    with pytest.raises(OctopError):
        e.access.resolve(e.employee)


def test_disabled_or_missing_policy_never_means_unrestricted(model_env):
    e = model_env
    grant(e, ["alpha/chat"])
    e.services.user_policy_repo.set(e.employee, MODEL_ACCESS_POLICY, None)
    assert e.access.filter_refs(e.employee, ["alpha/chat"]) == []
    with pytest.raises(OctopError):
        e.access.get(e.admin)
