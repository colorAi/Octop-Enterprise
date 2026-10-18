"""End-to-end smoke for the orca memory router using TestClient.

Bypasses OctopServer / DB / auth — we override the FastAPI deps used
by the router (``current_user``, ``get_server``, ``require_agent_row``)
with stubs that point straight at the real ZYWZTD sqlite. Useful for
hand-checking the wire shape before the dashboard frontend is wired.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from octop.api.deps import current_user, get_server
from octop.api.routers import memory


class _StubPaths:
    """Stub of ``OctopServer.services.paths`` with ensure_agent_workspace."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def ensure_agent_workspace(self, agent_id: str) -> Path:
        return self._root / agent_id


class _StubServices:
    def __init__(self, paths: _StubPaths) -> None:
        self.paths = paths


class _StubServer:
    def __init__(self, services: _StubServices) -> None:
        self.services = services


def _build_app() -> FastAPI:
    octop_home = Path.home() / ".octop"
    server = _StubServer(_StubServices(_StubPaths(octop_home / "agents")))

    app = FastAPI()
    app.include_router(memory.router, prefix="/api")

    # OctopError → JSON envelope (production app does this in app.py).
    from fastapi.responses import JSONResponse
    from starlette.requests import Request

    from octop.infra.errors import OctopError

    @app.exception_handler(OctopError)
    async def _octop_error_handler(_req: Request, exc: OctopError):
        return JSONResponse(status_code=exc.status, content=exc.to_envelope())

    # Override the router's auth + server deps with no-op stubs.
    app.dependency_overrides[current_user] = lambda: {"id": 1, "username": "yingningchen"}
    app.dependency_overrides[get_server] = lambda: server

    # Patch require_agent_row to a permissive no-op (the router calls
    # this through call_memory_rpc).
    import octop.api.common.memory_client as memory_client

    memory_client.require_agent_row = (  # type: ignore[assignment]
        lambda agent_id, **_: type("Row", (), {"agent_id": agent_id})()
    )

    return app


def main() -> None:
    app = _build_app()
    client = TestClient(app)
    aid = "ZYWZTD"

    print("=== GET stats/counts ===")
    r = client.get(f"/api/agents/{aid}/memory/stats/counts")
    print(r.status_code, json.dumps(r.json(), ensure_ascii=False, indent=2))

    print("=== POST atoms/list (Preference filter) ===")
    r = client.post(
        f"/api/agents/{aid}/memory/atoms/list",
        json={"candidate_type": "Preference"},
    )
    body = r.json()
    print(r.status_code, f"total={body['total']} has_more={body['has_more']}")
    for it in body["items"]:
        print(f"  {it['id'][:8]} [{it['kind']}] {it['assertion'][:60]}")

    print("=== GET terminal/about_me ===")
    r = client.get(f"/api/agents/{aid}/memory/terminal/about_me?limit=3")
    print(r.status_code, [it["assertion"] for it in r.json()["items"]])

    print("=== GET stats/atom_kinds ===")
    r = client.get(f"/api/agents/{aid}/memory/stats/atom_kinds")
    print(r.status_code, r.json())

    print("=== GET terminal/recent_stories ===")
    r = client.get(f"/api/agents/{aid}/memory/terminal/recent_stories")
    print(
        r.status_code,
        [(it["emotion"], it["intensity"], it["summary"][:30]) for it in r.json()["items"]],
    )

    print("=== GET atoms/{id} (single) ===")
    aid_atom = body["items"][0]["id"] if body["items"] else None
    if aid_atom:
        r = client.get(f"/api/agents/{aid}/memory/atoms/{aid_atom}")
        print(r.status_code, "kind=", r.json().get("kind"))

    print("=== GET atoms/{id} 404 ===")
    r = client.get(f"/api/agents/{aid}/memory/atoms/no-such-atom")
    print(r.status_code, r.text[:200])


if __name__ == "__main__":
    main()
