"""Real Python credential issuance -> native Go HTTP/WS -> isolated PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from octop.config import OctopConfig
from octop.infra.communication.bridge import CommunicationBridge
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import PostgresPool
from octop.infra.db.services import build_shared_services
from octop.infra.users.profile_avatar import notify_user_avatar_changed, profile_avatar_url
from octop.infra.utils.paths import PathLayout


@pytest.mark.skipif(os.name == "nt", reason="Go communication media backend targets POSIX")
@pytest.mark.asyncio
async def test_real_python_go_identity_messages_and_realtime(tmp_path: Path, monkeypatch):
    admin_dsn = os.environ.get("OCTOP_IM_TEST_DATABASE_URL")
    if not admin_dsn or not shutil.which("go"):
        pytest.skip("isolated PostgreSQL admin URL and Go required")
    names = ["octop_bridge_test_" + uuid.uuid4().hex[:12] for _ in range(2)]
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        for name in names:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    bridge = pool = process = None
    try:
        args = conninfo_to_dict(admin_dsn)
        args["dbname"] = names[0]
        pool = PostgresPool(make_conninfo(**args))
        run_migrations(pool)
        services = build_shared_services(db=pool, paths=PathLayout(tmp_path), config=OctopConfig())
        manager = services.user_repo.create(username="manager", role="admin")
        alice = services.user_repo.create(
            username="alice", role="user", permissions=["messaging", "contacts"]
        )
        bob = services.user_repo.create(
            username="bob", role="user", permissions=["messaging", "contacts"]
        )
        services.paths.user_avatars_dir.mkdir(parents=True)
        portrait = services.paths.user_avatars_dir / f"{alice}.png"
        portrait.write_bytes(b"existing account portrait")
        key = tmp_path / "identity.key"
        key.write_bytes(b"identity-secret" * 3)
        cursor_key = tmp_path / "cursor.key"
        cursor_key.write_bytes(b"cursor-secret" * 3)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        # Go accepts URL DSNs rather than libpq keyword form.
        from urllib.parse import quote

        im_dsn = (
            f"postgresql://{quote(args['user'], safe='')}@{args['host']}:{args['port']}/{names[1]}"
        )
        if args.get("password"):
            im_dsn = f"postgresql://{quote(args['user'], safe='')}:{quote(args['password'], safe='')}@{args['host']}:{args['port']}/{names[1]}"
        env = {
            **os.environ,
            "OCTOP_IM_DATABASE_URL": im_dsn,
            "OCTOP_IM_IDENTITY_KEY_FILE": str(key),
            "OCTOP_IM_CURSOR_KEY_FILE": str(cursor_key),
            "OCTOP_IM_MEDIA_ROOT": str(tmp_path / "media"),
            "OCTOP_IM_LISTEN": f"127.0.0.1:{port}",
        }
        binary = tmp_path / "im-server"
        service_dir = Path(__file__).parents[3] / "services/communication"
        await asyncio.to_thread(
            subprocess.run,
            ["go", "build", "-o", str(binary), "./cmd/im-server"],
            cwd=service_dir,
            check=True,
            capture_output=True,
        )
        await asyncio.to_thread(
            subprocess.run, [str(binary), "--migrate"], env=env, check=True, capture_output=True
        )
        process = subprocess.Popen(
            [str(binary)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(trust_env=False) as check:
            for _ in range(100):
                try:
                    if (await check.get(base + "/healthz")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.02)
            else:
                pytest.fail("native communication service did not start")
        monkeypatch.setenv("OCTOP_IM_URL", base)
        monkeypatch.setenv("OCTOP_IM_IDENTITY_KEY_FILE", str(key))
        bridge = CommunicationBridge(services)
        await bridge.start()

        async def request(uid, method, path, value=None):
            async def content():
                yield json.dumps(value).encode()

            response = await bridge.proxy(
                method=method,
                path=path,
                user_id=uid,
                session="e2e-session",
                query=[],
                content=content() if method != "GET" else None,
                content_type="application/json",
            )
            data = json.loads(await response.aread())
            await response.aclose()
            assert response.status_code in {200, 201}, data
            return data["data"]

        dept = (await request(manager, "POST", "/organization/departments", {"name": "研发部"}))[
            "department_id"
        ]
        for uid in (manager, alice, bob):
            await bridge.ready(uid, "e2e-session", "messaging")
            await request(
                manager,
                "PUT",
                f"/organization/users/{bridge.mapped_id(uid)}/department",
                {"department_id": dept},
            )
        contacts = await request(bob, "GET", "/contacts")
        person = next(p for p in contacts["items"] if p["user_id"] == bridge.mapped_id(alice))
        assert person["avatar_url"] == profile_avatar_url(
            services.paths.user_avatars_dir, str(alice), f"/api/users/{alice}/avatar"
        )
        before = bridge.repo.current(alice)["version"]
        portrait.unlink()
        await notify_user_avatar_changed(services, alice)
        assert bridge.repo.current(alice)["version"] == before + 1
        await bridge.ready(alice, "e2e-session", "contacts")
        contacts = await request(bob, "GET", "/contacts")
        person = next(p for p in contacts["items"] if p["user_id"] == bridge.mapped_id(alice))
        assert person["avatar_url"] == ""
        conv = (
            await request(
                alice, "POST", "/conversations/direct", {"peer_user_id": bridge.mapped_id(bob)}
            )
        )["conversation_id"]
        await request(
            alice,
            "POST",
            f"/conversations/{conv}/messages",
            {"client_message_id": "e2e-first", "text": "原账号互通"},
        )
        history = await request(bob, "GET", f"/conversations/{conv}/messages")
        assert "原账号互通" in json.dumps(history, ensure_ascii=False)
        ticket = await bridge.issue_ticket(bob, "e2e-session", int(time.time()) + 100)
        value = await bridge.consume_ticket(ticket["ticket"])
        events = asyncio.Queue()
        browser = SimpleNamespace(send_text=events.put)

        # Pending receive should remain blocked while the guard and upstream run.
        async def receive_text():
            await asyncio.Event().wait()

        browser.receive_text = receive_text
        socket_task = asyncio.create_task(bridge.realtime(value, browser))
        try:
            event = await asyncio.wait_for(events.get(), 3)
            assert json.loads(event)["type"] == "presence.changed"
            presence = await request(alice, "GET", "/presence")
            assert presence["online_user_ids"] == [bridge.mapped_id(bob)]
            for attempt in range(10):
                await request(
                    alice,
                    "POST",
                    f"/conversations/{conv}/messages",
                    {"client_message_id": f"e2e-wakeup-{attempt}", "text": "实时通知"},
                )
                try:
                    event = await asyncio.wait_for(events.get(), 0.2)
                    assert json.loads(event)["type"] == "sync.available"
                    break
                except TimeoutError:
                    if socket_task.done():
                        await socket_task
                        pytest.fail("Python/Go websocket closed before receiving a wakeup")
            else:
                pytest.fail("Python/Go websocket proxy did not forward wakeup")
            services.user_repo.set_disabled(bob, True)
            await asyncio.wait_for(socket_task, 3)
        finally:
            socket_task.cancel()
            await asyncio.gather(socket_task, return_exceptions=True)
    finally:
        if bridge:
            await bridge.stop()
        if process:
            process.terminate()
            await asyncio.to_thread(process.wait, timeout=10)
        if pool:
            pool.close()
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            for name in names:
                admin.execute(sql.SQL("DROP DATABASE {} WITH(FORCE)").format(sql.Identifier(name)))
