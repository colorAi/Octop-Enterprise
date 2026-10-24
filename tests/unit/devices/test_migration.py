from octop.infra.db.migrate import run_migrations


def test_existing_schema_21_upgrades_without_touching_users(stack):
    server, alice, bob, _ = stack
    db = server.services.db
    with db.connect() as conn:
        conn.execute("DROP TABLE desktop_pairing_codes")
        conn.execute("DROP TABLE desktop_devices")
        conn.execute("UPDATE _schema_version SET version=21")
    run_migrations(db)
    run_migrations(db)
    with db.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 30
        assert conn.execute("SELECT COUNT(*) FROM desktop_devices").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM desktop_pairing_codes").fetchone()[0] == 0
    assert server.services.user_repo.get(alice).username == "alice"
    assert server.services.user_repo.get(bob).username == "bob"


def test_schema_22_adds_file_history_boundary(stack):
    server, alice, _, _ = stack
    db = server.services.db
    with db.connect() as conn:
        conn.execute("DROP TABLE desktop_file_threads")
        conn.execute("UPDATE _schema_version SET version=22")
    run_migrations(db)
    assert not server.services.device_repo.is_file_thread("unknown")
    assert server.services.user_repo.get(alice).username == "alice"
