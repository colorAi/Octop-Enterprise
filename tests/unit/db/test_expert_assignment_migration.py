from octop.infra.db import migrate
from octop.infra.db.pool import SqlitePool


def test_upgrade_preserves_existing_users_and_experts_in_legacy_mode(tmp_path, monkeypatch):
    db = SqlitePool(tmp_path / "upgrade.db")
    discover = migrate._discover
    try:
        with monkeypatch.context() as patch:
            # The initial fixture predates the v29 execution-control compatibility repair.
            patch.setattr(migrate, "_ensure_optional_model_limit", lambda _: None)
            patch.setattr(
                migrate,
                "_discover",
                lambda dialect="sqlite": [
                    (version, path) for version, path in discover(dialect) if version <= 25
                ],
            )
            migrate.run_migrations(db)
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO users(username,role,disabled,created_at) VALUES ('existing','user',0,123)"
            )
            uid = conn.execute("SELECT id FROM users WHERE username='existing'").fetchone()[0]
            conn.execute(
                "INSERT INTO agents(agent_id,user_id,name,enabled,created_at,updated_at) VALUES (?,?,?,1,123,123)",
                ("existing-agent", uid, "Existing expert"),
            )
        migrate.run_migrations(db)
        migrate.run_migrations(db)
        with db.connect() as conn:
            user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            assert user["expert_access_mode"] == "legacy"
            assert user["expert_assignment_revision"] == 0
            assert user["created_at"] == 123
            assert (
                conn.execute("SELECT name FROM agents WHERE agent_id='existing-agent'").fetchone()[
                    0
                ]
                == "Existing expert"
            )
            assert (
                conn.execute(
                    "SELECT source_user_id FROM expert_assignment_source WHERE id=1"
                ).fetchone()[0]
                is None
            )
    finally:
        db.close()
