from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.communication_identity import CommunicationIdentityRepo
from octop.infra.db.repos.users import UserRepo


@pytest.fixture
def stack(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    yield pool, UserRepo(pool), CommunicationIdentityRepo(pool)
    pool.close()


def test_identity_revision_atomic_and_delete_tombstone(stack):
    pool, users, identities = stack
    uid = users.create(
        username="alice",
        role="user",
        password_hash="private-password-hash",
        permissions=["messaging", "contacts"],
    )
    assert identities.current(uid)["version"] == 1
    users.set_disabled(uid, True)
    users.set_display_name(uid, "Alice")
    assert identities.current(uid)["version"] == 3
    assert identities.current(uid)["status"] == "disabled"
    with pytest.raises(RuntimeError), pool.transaction() as conn:
        conn.execute("UPDATE users SET disabled=0 WHERE id=?", (uid,))
        raise RuntimeError("abort")
    assert identities.current(uid)["status"] == "disabled"
    assert identities.current(uid)["version"] == 3
    users.delete(uid)
    assert identities.current(uid)["status"] == "deleted"
    assert "password" not in identities.current(uid)
    assert [row["version"] for row in identities.pending(uid, due=False)] == [1]
    for version in (1, 2, 3):
        identities.acknowledge(uid, version)
    assert identities.pending(uid, due=False)[0]["version"] == 4
    new = users.create(username="alice", role="user")
    assert new > uid


def test_sso_and_bulk_permissions_writes_capture_revision(stack):
    pool, users, identities = stack
    uid = users.create(username="sso", role="user", password_hash=None)
    users.update_sso_profile(uid, display_name="SSO Name")
    users.set_permissions(uid, ["contacts"])
    with pool.transaction() as conn:
        conn.execute("UPDATE users SET role='admin' WHERE id=?", (uid,))
    assert identities.current(uid)["version"] == 4
    assert identities.current(uid)["role"] == "admin"
    # Password, locale and failed login counters do not need a communication revision.
    users.set_locale(uid, "en")
    users.set_password_hash(uid, "new-hash")
    assert identities.current(uid)["version"] == 4


def test_portrait_refresh_is_durable_and_keeps_identity_payload(stack):
    pool, users, identities = stack
    uid = users.create(username="portrait", role="user", permissions=["contacts"])
    original = identities.current(uid)
    identities.acknowledge(uid, 1)
    identities.refresh_profile(uid)
    assert identities.current(uid) == {**original, "version": 2}
    assert identities.pending(uid, due=False)[0]["version"] == 2
    with pool.connect() as conn:
        conn.execute(
            "CREATE TRIGGER reject_portrait_outbox BEFORE INSERT ON communication_identity_outbox "
            "WHEN NEW.version=3 BEGIN SELECT RAISE(ABORT,'outbox unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="outbox unavailable"):
        identities.refresh_profile(uid)
    assert identities.current(uid)["version"] == 2
    assert identities.pending(uid, due=False)[0]["version"] == 2


def test_one_use_ticket_expiry_and_logout(stack):
    _, users, identities = stack
    uid = users.create(username="a", role="user")
    now = int(time.time())
    digest = hashlib.sha256(b"ticket").hexdigest()
    identities.store_ticket(digest, uid, "s1", now + 30, now + 100)
    assert identities.consume_ticket(digest)["user_id"] == uid
    assert identities.consume_ticket(digest) is None
    identities.store_ticket(digest, uid, "s1", now - 1, now + 100)
    assert identities.consume_ticket(digest) is None
    identities.store_ticket(digest, uid, "s1", now + 30, now + 100)
    identities.revoke_session("s1", now + 100)
    assert identities.session_revoked("s1")
    assert identities.consume_ticket(digest) is None
    assert not identities.session_revoked("s2")


def test_delivery_backoff_keeps_order(stack):
    _, users, identities = stack
    uid = users.create(username="a", role="user")
    users.set_disabled(uid, True)
    identities.failed(uid, 1, 0)
    assert identities.pending(uid) == []
    assert identities.pending(uid, due=False)[0]["version"] == 1
    identities.acknowledge(uid, 1)
    assert identities.pending(uid)[0]["version"] == 2


def test_repeated_logout_cannot_shorten_revocation(stack, monkeypatch):
    _, _, identities = stack
    now = int(time.time())
    identities.revoke_session("s", now + 600)
    identities.revoke_session("s", now + 60)
    monkeypatch.setattr(time, "time", lambda: now + 120)
    assert identities.session_revoked("s")


def test_startup_reconciliation_requeues_current_state_and_tombstones(stack):
    _, users, identities = stack
    uid = users.create(username="a", role="user")
    identities.acknowledge(uid, 1)
    identities.enqueue_reconciliation()
    identities.enqueue_reconciliation()
    assert len(identities.pending(uid, due=False)) == 1
    users.delete(uid)
    identities.acknowledge(uid, 1)
    identities.acknowledge(uid, 2)
    identities.enqueue_reconciliation()
    assert identities.current(uid)["status"] == "deleted"
    assert identities.pending(uid, due=False)[0]["version"] == 2
