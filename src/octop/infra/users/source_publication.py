"""Automatic publication of isolated source drafts into immutable objects.

Only the runtime host writes drafts. HTTP mutations and source tools share a
gate; readers use a committed head. Offline DB edits invalidate the head through
triggers. Uncontrolled host filesystem edits are drafts, never published bytes.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext, suppress
from contextvars import ContextVar
from typing import Any

from octop.infra.users.capabilities import capability_records
from octop.infra.users.expert_access import denied
from octop.infra.utils.immutable_files import read_object, store_object

logger = logging.getLogger(__name__)
_draft: ContextVar[bool] = ContextVar("octop_source_draft", default=False)
_FORMAT = "octop.source-resources.v3"


class SourcePublication:
    def __init__(self, resources: Any, *, executor: tuple[str, str] | None = None) -> None:
        self.resources = resources
        self.executor = executor
        self.repo = resources.services.source_publication_repo
        self.root = resources.services.paths.root / "company-runtime-skills"
        self._lock = asyncio.Lock()
        self._cached: tuple[str, dict[str, Any]] | None = None
        self._task: asyncio.Task[None] | None = None

    async def _source(self) -> int | None:
        policy = await self.resources._read(self.resources.access.repo.source)
        user = policy["source_user_id"]
        owner = (
            await self.resources._read(self.resources.services.user_repo.get, user)
            if user
            else None
        )
        return int(user) if owner and not owner.disabled and owner.role == "admin" else None

    async def current(self) -> tuple[str, dict[str, Any]] | None:
        if _draft.get():
            return None
        user = await self._source()
        if user is None:
            return None
        head = await self.resources._read(self.repo.head, user)
        if head is None:
            raise denied("resource_publication_pending", 409)
        # A draft is not a revocation. Readers keep using the last committed
        # immutable snapshot while its replacement is built. Assignment,
        # source ownership and enabled-state checks still use the live database.
        digest = head["manifest_sha256"]
        if not digest:
            raise denied("resource_publication_pending", 409)
        if self._cached is None or self._cached[0] != digest:
            try:
                raw = await self.resources._read(
                    read_object, self.root, digest, limit=32 * 1024 * 1024
                )
                value = json.loads(raw)
                if (
                    value.get("format")
                    not in {_FORMAT, "octop.source-resources.v1", "octop.source-resources.v2"}
                    or value.get("source_user_id") != user
                ):
                    raise ValueError("source_manifest_invalid")
            except (OSError, ValueError) as exc:
                raise denied("resource_snapshot_unavailable", 503) from exc
            # Company capabilities are application definitions, not mutable
            # expert drafts. Older committed snapshots must expose them even
            # while a failed source save correctly prevents republication.
            known = {record["id"] for record in value["catalog"]["skills"]}
            value["catalog"]["skills"].extend(
                record for record in capability_records() if record["id"] not in known
            )
            self._cached = digest, value
        return self._cached

    async def capture(self, user: int) -> dict[str, Any]:
        catalog = await self.resources._catalog()
        definitions = {}
        total = count = 0
        for expert in catalog["experts"]:
            if not expert["available"]:
                raise denied("resource_source_unavailable", 409)
            definitions[expert["agent_id"]] = await self.resources._definition(expert["agent_id"])
        for skill in catalog["skills"]:
            entries = []
            for name, data in sorted(await self.resources._files(skill)):
                total += len(data)
                count += 1
                if total > self.resources.max_bundle_bytes or count > 20000:
                    raise denied("expert_skills_too_large")
                digest = await self.resources._read(store_object, self.root, data)
                entries.append({"path": name, "sha256": digest, "size": len(data)})
            skill["_published_files"] = entries
        # Defaults are authorization data and are always read fresh, not frozen.
        catalog["defaults"] = []
        return {
            "format": _FORMAT,
            "source_user_id": user,
            "catalog": catalog,
            "definitions": definitions,
        }

    @staticmethod
    def encode(value: dict[str, Any]) -> bytes:
        raw = json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
        if len(raw) > 32 * 1024 * 1024:
            raise denied("resource_manifest_too_large", 409)
        return raw

    async def _publish(self, user: int) -> None:
        wait_updates = getattr(self.resources.registry, "wait_source_updates", None)
        if wait_updates is not None:
            await wait_updates(user)
        value = await self.capture(user)
        raw = self.encode(value)
        head = await self.resources._read(self.repo.head, user)
        assert head is not None
        # A source-side process may still be finishing a write; never freeze a
        # mixed manifest. Retrying a normal save can publish the coherent draft.
        if raw != self.encode(await self.capture(user)):
            raise denied("resource_source_changed", 409)
        digest = await self.resources._read(store_object, self.root, raw)
        await self.resources._read(
            self.repo.publish,
            user,
            generation=head["generation"],
            revision=head["dirty_revision"],
            digest=digest,
            executor=self.executor,
        )
        self._cached = digest, value

    @asynccontextmanager
    async def mutation(self) -> AsyncIterator[None]:
        if _draft.get():
            yield
            return
        shards = getattr(self.resources.registry, "runtime_shards", None)
        async with self._lock, shards.activity(None) if shards is not None else nullcontext():
            user = await self._source()
            touched = set()
            if user is not None:
                await self.resources._read(self.repo.begin, user)
                touched.add(user)
            token = _draft.set(True)
            try:
                yield
                selected = await self._source()
                if selected is not None:
                    if selected != user:
                        await self.resources._read(self.repo.begin, selected)
                        touched.add(selected)
                    await self._publish(selected)
                    touched.discard(selected)
            finally:
                _draft.reset(token)
                for source in touched:
                    await self.resources._read(self.repo.fail, source)

    async def tool(self, agent_id: str, request: Any, handler: Any) -> Any:
        user = await self._source()
        row = await self.resources._read(self.resources.services.agent_repo.get, agent_id)
        if user is None or row is None or row.user_id != user:
            return await handler(request)
        async with self.mutation():
            return await handler(request)

    async def refresh(self) -> None:
        user = await self._source()
        if user is None:
            return
        head = await self.resources._read(self.repo.head, user)
        if head and (head["editing"] or head["failed"]):
            return  # A crashed/failed save needs an explicit successful save.
        current = await self.current() if head and head["manifest_sha256"] else None
        if (
            head is None
            or head["published_revision"] != head["dirty_revision"]
            or (current is not None and current[1]["format"] != _FORMAT)
        ):
            async with self.mutation():
                pass

    async def start(self) -> None:
        user = await self._source()
        head = await self.resources._read(self.repo.head, user) if user else None
        if head and head["editing"]:
            await self.resources._read(self.repo.fail, user)
        await self.refresh()
        self._task = asyncio.create_task(self._refresh_loop(), name="resource-publication")

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(2)
            try:
                await self.refresh()
            except Exception:
                logger.exception("source publication failed; previous manifest retained")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        async with self._lock:
            pass

    async def capture_employee(self, user: int, source_id: str | None) -> Any:
        current = await self.current()
        if current is None:
            return None
        digest, value = current
        state = await self.resources._read(self.resources.state, user)
        policy = await self.resources._read(self.resources.access.repo.source)
        defaults = await self.resources._read(self.resources.repo.defaults)
        catalog = value["catalog"]
        records = {x["id"]: x for x in catalog["skills"]}
        captured = {}
        definition = None
        if source_id is not None:
            expert = next((x for x in catalog["experts"] if x["agent_id"] == source_id), None)
            if not expert or not expert["available"] or source_id not in state["expert_ids"]:
                raise denied("resource_source_unavailable", 409)
            definition = value["definitions"][source_id]
            for identity in set(defaults + state["skill_ids"] + expert["skill_ids"]):
                if identity not in records:
                    raise denied("resource_source_unavailable", 409)
                captured[identity] = await self.resources.files(records[identity])
        # Defaults can change without modifying source files.
        content = hashlib.sha256(
            json.dumps([digest, defaults], sort_keys=True).encode()
        ).hexdigest()
        return {
            "revision": f"{state['revision']}:{policy['revision']}:{state['membership_revision']}:{content}",
            "content_revision": content,
            "definition": copy.deepcopy(definition),
            "records": records,
            "files": captured,
        }
