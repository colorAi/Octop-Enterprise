"""Durable group expert orchestration, independent of personal agent runtimes.

The primary worker drains both services' outboxes. Pulling the Go outbox keeps
the lightweight web edge addressable without introducing a Go → worker route.
Every operation remains in its owning database until its receipt is confirmed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from octop.infra.db.repos.employee_resources import UNASSIGNED
from octop.infra.db.repos.execution import ExecutionConflict, wire
from octop.infra.db.repos.expert_assignments import AssignmentConflict
from octop.infra.errors import OctopError
from octop.infra.group_experts.publication import prepare_publication
from octop.infra.utils.bounded_io import BoundedIO

logger = logging.getLogger(__name__)


class GroupExpertService:
    def __init__(self, server: Any) -> None:
        self.server = server
        self.services = server.services
        self.repo = self.services.group_expert_repo
        self.bridge = server.communication
        self.io = BoundedIO(workers=2, capacity=32, wait=True)
        self._task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._heartbeat_io = BoundedIO(workers=1, capacity=4, wait=True)
        self._bootstrapped = False
        self._publication_version: tuple[str, str] | None = None
        self._refresh_version: str | None = None
        self._next_refresh = 0.0
        self._delivery = asyncio.Lock()

    async def start(self, *, background: bool = False) -> None:
        if background and self.bridge.enabled:
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="group-expert-heartbeat"
            )
            self._task = asyncio.create_task(self._loop(), name="group-expert-reconciliation")

    async def close(self) -> None:
        tasks = [task for task in (self._task, self._heartbeat_task) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.io.drain()
        await self._heartbeat_io.drain()

    async def runtime(self) -> dict[str, Any]:
        local = await self._heartbeat_io.run(self.repo.capabilities)
        remote = await self.bridge.group_request("GET", "/capabilities")
        ready = (
            local["ready"]
            and local["schema"] >= 36
            and remote.get("protocol") == 2
            and remote.get("schema", 0) >= 21
        )
        await self.bridge.group_request("POST", "/runtime", {"protocol": 2 if ready else 0})
        return {"ready": ready, "protocol": 2 if ready else 0}

    async def reconcile_user(self, user: int) -> None:
        membership = await self.bridge.resource_membership(user)
        repo = self.services.employee_resource_repo
        previous = await self.io.run(repo.scope, user)
        if (
            previous
            and previous["pending_scope"] is not None
            and previous["membership_revision"] == membership["revision"]
        ):
            prepared = json.loads(previous["pending_json"] or "{}")
            if time.time() - prepared.get("created_at", 0) > 15:
                resolved = await self.bridge.resolve_resource_move(
                    user, prepared["operation_id"], previous["membership_revision"]
                )
                if resolved["status"] == "cancelled":
                    await self.io.run(repo.cancel_move, user, previous["membership_revision"])
                membership = await self.bridge.resource_membership(user)
        try:
            await self.io.run(
                repo.reconcile,
                user,
                membership["department_id"] or UNASSIGNED,
                membership["revision"],
                membership["enabled"],
            )
        except AssignmentConflict as exc:
            raise ExecutionConflict("group_expert_authorization_syncing") from exc

    async def register(self, call: str) -> dict[str, Any]:
        metadata = await self.bridge.group_request("GET", f"/calls/{quote(call, safe='')}")
        identity = metadata["identity"]
        if await self.io.run(self.repo.get, call) is not None:
            return await self.io.run(self.repo.admit, identity)
        # Cancelled admissions still get the same immutable receipt, without
        # restoring permission to the original question or quoted text.
        try:
            await self.reconcile_user(identity["user_id"])
            await self.bridge.group_request("GET", f"/calls/{quote(call, safe='')}/context")
        except ExecutionConflict:
            # A prepared organization move is locally visible to admission,
            # which persists its explicit refusal without reading question text.
            return await self.io.run(
                self.repo.admit, identity, refusal="group_expert_authorization_syncing"
            )
        except OctopError as exc:
            if exc.status >= 500:
                raise
            await self.io.run(
                self.repo.control,
                call,
                sequence=max(1, metadata["call"]["control_seq"]),
                kind="cancel",
            )
        return await self.io.run(self.repo.admit, identity)

    async def context(self, call: str) -> dict[str, Any]:
        value = await self.io.run(self.repo.execution_input, call)
        await self.reconcile_user(value["identity"]["user_id"])
        value = await self.io.run(self.repo.execution_input, call)
        remote = await self.bridge.group_request("GET", f"/calls/{quote(call, safe='')}/context")
        remote_identity = {
            **remote["identity"],
            "max_text_chars": value["identity"]["max_text_chars"],
        }
        if remote_identity != value["identity"] or not remote["dispatched"]:
            raise ExecutionConflict("group_expert_identity_conflict")
        timezone = getattr(getattr(self.server, "config", None), "default_timezone", "UTC")
        return {
            **value,
            "question": remote["question"],
            "quote": remote["quote"],
            "current_time": datetime.now(ZoneInfo(timezone)).isoformat(),
        }

    async def deliver(self, row: dict[str, Any]) -> None:
        target = quote(row["target_id"], safe="")
        value = json.loads(row["payload"])
        if row["kind"] == "invalidate":
            receipt = await self.bridge.group_request(
                "PUT",
                f"/authorizations/{target}",
                {
                    "operation_id": row["operation_id"],
                    "revision": value["revision"],
                    **({"revocations": value["revocations"]} if "revocations" in value else {}),
                },
            )
        elif row["kind"] == "scope":
            receipt = await self.bridge.group_request("PUT", f"/groups/{target}/scope", value)
        elif row["kind"] == "result":
            receipt = (await self.bridge.group_request("GET", f"/calls/{target}/receipt"))[
                "receipt"
            ]
            if receipt is None:
                delivery = await self.io.run(self.repo.delivery, row["target_id"])
                sealed = json.loads(delivery["result_wire"])
                await self.bridge.group_request(
                    "POST",
                    f"/calls/{target}/delivery",
                    {"run_id": sealed["execution_id"], "result_digest": delivery["result_digest"]},
                )
                if not delivery["unpublished"]:
                    try:
                        await self.bridge.group_request("GET", f"/calls/{target}/context")
                    except OctopError as exc:
                        if exc.status >= 500:
                            raise
                        sealed = json.loads(delivery["result_wire"])
                        sealed["result"] = None
                        sealed["reason_code"] = (exc.details or {}).get(
                            "reason", "group_expert_cancelled"
                        )
                        delivery = {**delivery, "unpublished": True, "result_wire": wire(sealed)}
                receipt = await self.bridge.group_request(
                    "POST", f"/calls/{target}/result", delivery
                )
        else:
            raise ValueError("unknown_group_expert_obligation")
        await self.io.run(
            self.repo.acknowledge,
            row["operation_id"],
            request_digest=row["request_digest"],
            receipt=receipt,
        )

    async def flush(
        self,
        *,
        conversation_id: str | None = None,
        source: str | None = None,
        source_row: int | None = None,
    ) -> None:
        async with self._delivery:
            if not self._bootstrapped:
                await self.io.run(self.repo.bootstrap_authorizations)
                self._bootstrapped = True
            for row in await self.io.run(
                self.repo.pending,
                conversation_id=conversation_id,
                source=source,
                source_row=source_row,
            ):
                try:
                    await self.deliver(row)
                except (OctopError, ExecutionConflict) as exc:
                    await self.io.run(
                        self.repo.retry, row["operation_id"], request_digest=row["request_digest"]
                    )
                    if isinstance(exc, OctopError) and exc.status >= 500:
                        break

    async def poll(self) -> None:
        rows = (await self.bridge.group_request("GET", "/pending"))["items"]
        for row in rows:
            call, operation = row["target_id"], quote(row["operation_id"], safe="")
            try:
                if row["kind"] == "register":
                    receipt = await self.register(call)
                    await self.bridge.group_request(
                        "POST",
                        f"/calls/{quote(call, safe='')}/admission",
                        {
                            key: receipt[key] or ""
                            for key in ("admission_state", "run_id", "reason_code")
                        },
                    )
                else:
                    receipt = await self.io.run(
                        self.repo.control,
                        call,
                        sequence=row["payload"]["control_seq"],
                        kind=row["kind"],
                    )
                    await self.bridge.group_request(
                        "POST",
                        f"/pending/{operation}/ack",
                        {"request_digest": row["request_digest"], "receipt": receipt},
                    )
            except (OctopError, ExecutionConflict):
                await self.bridge.group_request(
                    "POST", f"/pending/{operation}/retry", {"request_digest": row["request_digest"]}
                )

    async def tick(self) -> None:
        if self._heartbeat_task is None:
            await self.runtime()
        if time.monotonic() >= self._next_refresh:
            await self.refresh()
            self._next_refresh = time.monotonic() + 5
        await self.flush()
        await self.poll()

    async def refresh(self) -> None:
        resources = getattr(self.server, "employee_resources", None)
        publication = getattr(resources, "publication", None)
        if publication is not None:
            try:
                current = await publication.current()
                if current:
                    manifest_id, manifest = current
                    revision = await self.io.run(self.repo.publication_revision)
                    version = (manifest_id, revision)
                    if version != self._publication_version:
                        objects = await prepare_publication(self.services, manifest)
                        await self.io.run(
                            self.repo.refresh_publication, objects, source_manifest=manifest_id
                        )
                        self._publication_version = version
            except (OctopError, ExecutionConflict):
                logger.info("group expert source publication pending")
        revision = await self.io.run(self.repo.refresh_revision)
        if revision != self._refresh_version:
            await self.io.run(self.repo.refresh)
            self._refresh_version = revision

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await self.runtime()
            except Exception:
                logger.warning("group expert heartbeat will retry", exc_info=True)
            await asyncio.sleep(3)

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                logger.warning("group expert reconciliation will retry", exc_info=True)
            await asyncio.sleep(1)
