"""Identity delivery, signed requests and realtime orchestration."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import json
import logging
import os
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import psycopg

from octop.infra.communication.contracts import match_route
from octop.infra.db.services import SharedServices
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User
from octop.infra.users.permissions import effective_permissions, user_has_permission
from octop.infra.users.profile_avatar import profile_avatar_url

logger = logging.getLogger(__name__)


def sign_credential(key: bytes, claims: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).rstrip(b"=")
    sig = base64.urlsafe_b64encode(hmac.digest(key, body, "sha256")).rstrip(b"=")
    return (body + b"." + sig).decode()


class CommunicationBridge:
    def __init__(self, services: SharedServices) -> None:
        self.services = services
        self.repo = services.communication_identity_repo
        self.url = os.environ.get("OCTOP_IM_URL", "").rstrip("/")
        self.enabled = bool(self.url)
        self._key = b""
        self._instance = ""
        self._http: httpx.AsyncClient | None = None
        self._worker: asyncio.Task[None] | None = None
        self._delivery_lock = asyncio.Lock()

    async def start(self, *, background: bool = True) -> None:
        if not self.enabled:
            return
        parsed = urlsplit(self.url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise ValueError("OCTOP_IM_URL must be a fixed service origin")
        if self.services.db.dialect != "postgresql":
            raise ValueError("communication requires the migrated PostgreSQL control plane")
        key_file = Path(os.environ["OCTOP_IM_IDENTITY_KEY_FILE"])
        self._key = await asyncio.to_thread(key_file.read_bytes)
        if len(self._key) < 32:
            raise ValueError("communication identity key needs at least 32 bytes")
        instance = await asyncio.to_thread(
            self.services.secret_repo.get_or_create,
            "communication_instance",
            lambda: secrets.token_bytes(16),
        )
        self._instance = instance.hex()
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=5), follow_redirects=False, trust_env=False
        )
        if background:
            await asyncio.to_thread(self.repo.enqueue_reconciliation)
            self._worker = asyncio.create_task(self._deliver_loop())

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
        if self._http:
            await self._http.aclose()

    def mapped_id(self, user_id: int) -> str:
        return f"octop:{self._instance}:{user_id}"

    async def resource_membership(self, user_id: int) -> dict[str, Any]:
        """Fresh organization authority; resource use never trusts a browser's department."""
        if not self.enabled or self._http is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "organization unavailable", status=503)
        token = self.credential(user_id, 1, "resource-membership", [], purpose="resource-policy")
        try:
            response = await self._http.get(
                f"{self.url}/internal/resource-membership/{self.mapped_id(user_id)}",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            value = response.json()["data"]
            if not isinstance(value.get("revision"), int) or value["revision"] < 0:
                raise ValueError("invalid organization revision")
            return dict(value)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise OctopError(
                ErrorCode.INTERNAL_ERROR, "organization unavailable", status=503
            ) from None

    async def resource_memberships(
        self, actor_id: int, user_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        """Bounded management projection, never used for execution authorization."""
        if not self.enabled or self._http is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "organization unavailable", status=503)
        result: dict[int, dict[str, Any]] = {}
        ids = list(dict.fromkeys(user_ids))
        try:
            for start in range(0, len(ids), 200):
                mapped = {self.mapped_id(uid): uid for uid in ids[start : start + 200]}
                token = self.credential(
                    actor_id, 1, "resource-directory", [], purpose="resource-directory"
                )
                response = await self._http.post(
                    f"{self.url}/internal/resource-memberships",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"user_ids": list(mapped)},
                    timeout=10,
                )
                response.raise_for_status()
                values = response.json()["data"]["items"]
                if not isinstance(values, list) or len(values) != len(mapped):
                    raise ValueError("incomplete organization projection")
                seen = set()
                for value in values:
                    mid = value["user_id"]
                    if (
                        mid not in mapped
                        or mid in seen
                        or type(value["revision"]) is not int
                        or value["revision"] < 0
                        or type(value["enabled"]) is not bool
                        or type(value["found"]) is not bool
                        or not (
                            value["department_id"] is None
                            or isinstance(value["department_id"], str)
                        )
                    ):
                        raise ValueError("invalid organization projection")
                    seen.add(mid)
                    result[mapped[mid]] = dict(value)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise OctopError(
                ErrorCode.INTERNAL_ERROR, "organization unavailable", status=503
            ) from None
        return result

    async def organization_request(
        self, actor_id: int, session: str, method: str, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        async def content() -> AsyncIterator[bytes]:
            yield json.dumps(body).encode()

        response = await self.proxy(
            method=method,
            path=path,
            user_id=actor_id,
            session=session,
            query=[],
            content=content() if method not in {"GET", "HEAD"} else None,
            content_type="application/json",
        )
        try:
            await response.aread()
            if response.status_code >= 400:
                raise OctopError(
                    ErrorCode.FORBIDDEN,
                    "organization settings rejected",
                    status=response.status_code,
                    details={
                        "reason": "organization_revision_conflict"
                        if response.status_code == 409
                        else "organization_settings_rejected"
                    },
                )
            return dict(response.json()["data"])
        finally:
            await response.aclose()

    async def resolve_resource_move(
        self, user_id: int, operation_id: str, revision: int
    ) -> dict[str, Any]:
        if self._http is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "organization unavailable", status=503)
        token = self.credential(user_id, 1, "resource-membership", [], purpose="resource-policy")
        try:
            response = await self._http.post(
                f"{self.url}/internal/resource-membership/{self.mapped_id(user_id)}/resolve",
                headers={"Authorization": f"Bearer {token}"},
                json={"operation_id": operation_id, "expected_revision": revision},
            )
            response.raise_for_status()
            return dict(response.json()["data"])
        except (httpx.HTTPError, ValueError, KeyError):
            raise OctopError(
                ErrorCode.INTERNAL_ERROR, "organization unavailable", status=503
            ) from None

    def credential(
        self,
        user_id: int,
        version: int,
        session: str,
        perms: list[str],
        *,
        purpose: str = "request",
    ) -> str:
        now = int(time.time())
        return sign_credential(
            self._key,
            {
                "iss": "octop",
                "aud": "octop-im",
                "purpose": purpose,
                "sub": self.mapped_id(user_id),
                "sid": session,
                "iat": now,
                "exp": now + 30,
                "version": version,
                "permissions": perms,
            },
        )

    async def _deliver(self, row: dict[str, Any]) -> None:
        assert self._http is not None
        user_id, version = int(row["user_id"]), int(row["version"])
        value = json.loads(row["payload"])
        user = User(
            user_id,
            "",
            value.get("role", "user"),
            value.get("nickname"),
            permissions=value.get("permissions", []),
        )
        body = {
            "user_id": self.mapped_id(user_id),
            "version": version,
            "nickname": value.get("nickname", ""),
            "avatar_icon": value.get("avatar_icon", ""),
            # Portraits live in the control plane; Go stores only the authenticated API URL.
            "avatar_url": (
                await asyncio.to_thread(
                    profile_avatar_url,
                    self.services.paths.user_avatars_dir,
                    str(user_id),
                    f"/api/users/{user_id}/avatar",
                )
                if value["status"] != "deleted"
                else None
            )
            or "",
            "status": value["status"],
            "is_admin": user.is_admin,
            "permissions": effective_permissions(user) if value["status"] != "deleted" else [],
        }
        token = self.credential(user_id, version, "identity-sync", [], purpose="identity")
        try:
            response = await self._http.put(
                f"{self.url}/internal/identities/{self.mapped_id(user_id)}",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            data = response.json().get("data", {})
            if int(data.get("version", 0)) < version:
                raise ValueError("identity revision was not acknowledged")
        except (httpx.HTTPError, ValueError):
            await asyncio.to_thread(self.repo.failed, user_id, version, int(row["attempts"]))
            raise OctopError(
                ErrorCode.INTERNAL_ERROR, "communication identity unavailable", status=503
            ) from None
        await asyncio.to_thread(self.repo.acknowledge, user_id, version)

    async def _deliver_loop(self) -> None:
        while True:
            try:
                async with self._delivery_lock:
                    rows = await asyncio.to_thread(self.repo.pending)
                    for row in rows:
                        try:
                            await self._deliver(row)
                        except OctopError:
                            logger.warning(
                                "communication identity delivery will retry",
                                extra={"user_id": row["user_id"]},
                            )
            except psycopg.Error:
                logger.warning("communication control-plane unavailable; reconciliation will retry")
            await asyncio.sleep(1)

    async def authorize(
        self, user_id: int, session: str, permission: str | tuple[str, ...]
    ) -> dict[str, Any]:
        if not self.enabled or self._http is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "communication is disabled", status=503)
        state = await asyncio.to_thread(self.repo.current, user_id)
        if state is None or state["status"] != "active":
            raise OctopError(ErrorCode.USER_DISABLED, "user not active")
        if await asyncio.to_thread(self.repo.session_revoked, session):
            raise OctopError(ErrorCode.AUTH_FAILED, "communication session revoked")
        user = User(user_id, "", state["role"], state["nickname"], permissions=state["permissions"])
        permissions = (permission,) if isinstance(permission, str) else permission
        if not any(user_has_permission(user, item) for item in permissions):
            raise OctopError(
                ErrorCode.FORBIDDEN, "permission required", details={"permission": permission}
            )
        state["effective_permissions"] = effective_permissions(user)
        return state

    async def ready(
        self, user_id: int, session: str, permission: str | tuple[str, ...]
    ) -> dict[str, Any]:
        await self.authorize(user_id, session, permission)
        # Deliver every preceding revision in order; no stale credential can bypass a pending disable.
        async with self._delivery_lock:
            while rows := await asyncio.to_thread(self.repo.pending, user_id, due=False):
                await self._deliver(rows[0])
        return await self.authorize(user_id, session, permission)

    async def proxy(
        self,
        *,
        method: str,
        path: str,
        user_id: int,
        session: str,
        query: list[tuple[str, str]],
        content: AsyncIterator[bytes] | None,
        content_type: str,
    ) -> httpx.Response:
        route = match_route(method, path)
        if route is None:
            raise OctopError(ErrorCode.NOT_FOUND, "unknown communication endpoint")
        state = await self.ready(user_id, session, route.permission)
        assert self._http is not None
        token = self.credential(
            user_id, int(state["version"]), session, state["effective_permissions"]
        )
        allowed_query = {
            "q",
            "cursor",
            "limit",
            "before_seq",
            "after_seq",
            "chunk",
            "chunk_index",
            "kind",
            "include_hidden",
        }
        if any(k not in allowed_query for k, _ in query):
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "unsupported communication query")
        request = self._http.build_request(
            method,
            self.url + "/api/app/v1" + path,
            params=httpx.QueryParams(tuple(query)),
            content=content,
            headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        )
        try:
            return await self._http.send(request, stream=True)
        except httpx.HTTPError:
            raise OctopError(
                ErrorCode.INTERNAL_ERROR, "communication upstream unavailable", status=503
            ) from None

    async def issue_ticket(
        self, user_id: int, session: str, session_expires: int
    ) -> dict[str, Any]:
        await self.ready(user_id, session, ("contacts", "messaging"))
        raw = secrets.token_urlsafe(32)
        expires = min(int(time.time()) + 30, session_expires)
        await asyncio.to_thread(
            self.repo.store_ticket,
            hashlib.sha256(raw.encode()).hexdigest(),
            user_id,
            session,
            expires,
            session_expires,
        )
        return {"ticket": raw, "expires_at": expires}

    async def consume_ticket(self, raw: str) -> dict[str, Any]:
        if len(raw) > 128:
            raise OctopError(ErrorCode.AUTH_FAILED, "invalid realtime ticket")
        value = await asyncio.to_thread(
            self.repo.consume_ticket, hashlib.sha256(raw.encode()).hexdigest()
        )
        if value is None or int(value["session_expires_at"]) <= int(time.time()):
            raise OctopError(ErrorCode.AUTH_FAILED, "invalid realtime ticket")
        await self.authorize(int(value["user_id"]), value["session_id"], ("contacts", "messaging"))
        return value

    async def realtime(self, ticket: dict[str, Any], browser: Any) -> None:
        from websockets.asyncio.client import connect

        user_id, session = int(ticket["user_id"]), ticket["session_id"]
        state = await self.ready(user_id, session, ("contacts", "messaging"))
        token = self.credential(
            user_id, int(state["version"]), session, state["effective_permissions"]
        )
        url = (
            self.url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
            + "/api/app/v1/realtime"
        )
        options: dict[str, Any] = {
            "additional_headers": {"Authorization": f"Bearer {token}"},
            "max_size": 16384,
        }
        # Version 13 has no automatic proxy support; newer versions must bypass environment proxies.
        if "proxy" in inspect.signature(connect).parameters:
            options["proxy"] = None
        async with connect(url, **options) as upstream:

            async def to_browser() -> None:
                async for data in upstream:
                    await browser.send_text(data.decode() if isinstance(data, bytes) else data)

            async def to_upstream() -> None:
                while True:
                    data = await browser.receive_text()
                    if len(data) > 1024 or json.loads(data).get("type") not in {"ping", "resync"}:
                        return
                    await upstream.send(data)

            async def guard() -> None:
                while True:
                    await asyncio.sleep(1)
                    if int(ticket["session_expires_at"]) <= int(time.time()):
                        return
                    await self.authorize(user_id, session, ("contacts", "messaging"))

            tasks = [asyncio.create_task(fn()) for fn in (to_browser, to_upstream, guard)]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
