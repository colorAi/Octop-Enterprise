"""Department-bounded resources, real skill sources and employee runtime projection."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any, TypedDict, cast

from octop.infra.company_runtime.skills import store_asset
from octop.infra.db.repos.employee_resources import UNASSIGNED
from octop.infra.db.repos.expert_assignments import AssignmentConflict
from octop.infra.db.repos.resource_manifests import ManifestConflict
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.skills.skill_package_store import SkillPackageStore
from octop.infra.users.expert_access import ExpertAccess, denied
from octop.infra.users.managed_resources import (
    DEFINITION_FIELDS,
    DEFINITION_FILES,
    protected_definition_path,
    workspace_relative_path,
)
from octop.infra.users.resource_manifest import store_manifest
from octop.infra.utils.bounded_io import BoundedIO, ReadQueueFull
from octop.infra.utils.frontmatter import parse_frontmatter
from octop.infra.utils.immutable_files import cleanup_temporary, read_object

MANAGEMENT_SKILLS = frozenset({"skill-manager", "install-skill", "skill-creator"})
logger = logging.getLogger(__name__)


class _ResourceCapture(TypedDict):
    revision: str
    content_revision: str
    definition: dict[str, Any] | None
    records: dict[str, dict[str, Any]]
    files: dict[str, list[tuple[str, bytes]]]


class EmployeeResources:
    def __init__(self, server: Any) -> None:
        self.server = server
        self.services = server.services
        self.access = ExpertAccess(self.services)
        self.repo = self.services.employee_resource_repo
        self.manifests = self.services.resource_manifest_repo
        self.publication: Any = None
        self.max_bundle_bytes = 512 * 1024 * 1024
        if getattr(server, "role", None) == "worker":
            from octop.infra.execution.profiles import profile

            self.max_bundle_bytes = profile().running * 32 * 1024 * 1024
        self._prepare_locks: dict[str, asyncio.Lock] = {}
        # Permission rechecks belong to active model streams too. Apply database
        # backpressure rather than failing those streams during a completion burst.
        self._io = BoundedIO(wait=True)
        self._catalog_io = BoundedIO(workers=2, capacity=8)
        self._workspace_io = BoundedIO(workers=4, capacity=32)
        # Snapshot work stays at two active operations; short desktop bursts
        # queue lightweight requests instead of failing the ninth employee.
        self._snapshot_io = BoundedIO(workers=2, capacity=32)

    @staticmethod
    def _capacity_error() -> OctopError:
        return OctopError(
            ErrorCode.INTERNAL_ERROR,
            "resource read capacity exceeded",
            status=503,
            details={"reason": "read_capacity_exceeded", "retryable": True},
        )

    async def _read[T](self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        return await self._io.run(fn, *args, **kwargs)

    async def drain(self) -> None:
        if self.publication is not None:
            await self.publication.close()
        await self._snapshot_io.drain()
        await self._catalog_io.drain()
        await self._workspace_io.drain()
        await self._io.drain()

    async def recover_temporary_files(self) -> None:
        try:
            await self._read(cleanup_temporary, self.services.paths.root / "company-runtime-skills")
        except OSError:
            logger.warning("unable to clean abandoned resource temporary files", exc_info=True)

    @property
    def registry(self) -> Any:
        return self.server.app_runtime.agent_registry

    @asynccontextmanager
    async def _source_agent(self, agent_id: str) -> AsyncIterator[Any]:
        scope = getattr(self.registry, "source_workspace", None)
        if scope is None:
            yield SimpleNamespace(
                workspace=self.registry.get_agent(agent_id).workspace,
                list_skill_summaries=partial(self.registry.list_skill_summaries, agent_id),
            )
        else:
            async with scope(agent_id) as workspace:
                yield SimpleNamespace(
                    workspace=workspace,
                    list_skill_summaries=partial(
                        self.registry.list_skill_summaries, agent_id, workspace=workspace
                    ),
                )

    async def synchronize(self, user_id: int) -> dict[str, Any]:
        bridge = self.server.communication
        if bridge is None or not bridge.enabled:
            raise denied("organization_unavailable", 503)
        membership = await bridge.resource_membership(user_id)
        previous = await self._read(self.repo.scope, user_id)
        if (
            previous
            and previous["pending_scope"] is not None
            and previous["membership_revision"] == membership["revision"]
        ):
            prepared = json.loads(previous["pending_json"] or "{}")
            if time.time() - prepared.get("created_at", 0) > 15:
                resolved = await bridge.resolve_resource_move(
                    user_id, prepared["operation_id"], previous["membership_revision"]
                )
                if resolved["status"] == "cancelled":
                    await self._read(
                        self.repo.cancel_move, user_id, previous["membership_revision"]
                    )
                membership = await bridge.resource_membership(user_id)
        scope = membership["department_id"] or UNASSIGNED
        try:
            await self._read(
                self.repo.reconcile, user_id, scope, membership["revision"], membership["enabled"]
            )
        except AssignmentConflict as exc:
            raise denied(str(exc), 409) from exc
        return {**membership, "scope_id": scope}

    def state(self, user_id: int) -> dict[str, Any]:
        state = self.access.repo.state(user_id)
        if state is None:
            raise denied("expert_assignment_employee_required", 404)
        scope = self.repo.scope(user_id)
        return {
            **state,
            "skill_ids": self.repo.skills(user_id),
            "scope_id": scope["scope_id"] if scope else UNASSIGNED,
            "membership_revision": scope["membership_revision"] if scope else 0,
        }

    async def authorize(self, user_id: int, agent_id: str, *, prepared: bool = False) -> None:
        instance = await self._read(self.access.repo.instance, agent_id)
        if instance is not None:
            await self.synchronize(user_id)
        await self._read(self.access.assert_use, user_id, agent_id)
        if instance is not None and prepared:
            agent = self.registry.get_agent(agent_id)
            revision = await self.revision(user_id)
            if getattr(agent, "_octop_resource_revision", None) != revision:
                raise denied("employee_resources_changed", 409)

    async def revision(self, user_id: int) -> str:
        return (await self._capture_content(user_id))["revision"]

    async def definition(self, source_id: str) -> dict[str, Any]:
        if self.publication is not None:
            published = await self.publication.current()
            if published is not None:
                value = published[1]["definitions"].get(source_id)
                if value is None:
                    raise denied("resource_source_unavailable", 409)
                return dict(copy.deepcopy(value))
        try:
            return await self._workspace_io.run_async(lambda: self._definition(source_id))
        except ReadQueueFull as exc:
            raise self._capacity_error() from exc

    async def _definition(self, source_id: str) -> dict[str, Any]:
        from octop.infra.agents.experts.avatar import (
            public_portrait_icon_url,
            read_workspace_avatar,
            sniff_avatar_media_type,
        )
        from octop.infra.agents.experts.catalog import (
            parse_task_examples,
            read_workspace_manifest_data,
            welcome_payload_from_manifest_data,
        )
        from octop.infra.agents.settings.runtime_limits import agent_runtime_values
        from octop.infra.utils.immutable_files import store_object

        row = await self._read(self.services.agent_repo.get, source_id)
        if row is None:
            raise denied("resource_source_unavailable", 409)
        async with self._source_agent(source_id) as agent:
            workspace = agent.workspace
            files = {}
            for name in DEFINITION_FILES:
                value = await workspace.aread_text(name, limit=262145)
                if value is not None:
                    if len(value) > 262144:
                        raise denied("resource_definition_too_large", 409)
                    files[name] = value
            manifest = await read_workspace_manifest_data(workspace) or {}
            presentation = {
                **welcome_payload_from_manifest_data(manifest),
                "task_examples": parse_task_examples(manifest),
            }
            avatar = None
            found = await read_workspace_avatar(workspace)
            if found is not None:
                data, _ = found
                media_type = sniff_avatar_media_type(data)
                digest = await self._read(
                    store_object, self.services.paths.root / "company-runtime-skills", data
                )
                avatar = {"sha256": digest, "media_type": media_type}
        fields = {name: getattr(row, name) for name in DEFINITION_FIELDS}
        fields["icon_url"] = public_portrait_icon_url(row.icon_url)
        return {
            "fields": fields,
            "files": files,
            "presentation": presentation,
            "avatar": avatar,
            "source_row_id": row.id,
            "runtime_settings": agent_runtime_values(json.loads(row.config_json or "{}")),
        }

    async def display_definition(self, user_id: int, agent_id: str) -> dict[str, Any] | None:
        """Authorized display data for assigned experts, without starting a runtime."""
        mapping = await self._read(self.access.repo.instance, agent_id)
        if mapping is not None:
            if mapping["user_id"] != user_id:
                # Administrators can inspect display metadata in user management;
                # execution still requires the employee's own live assignment.
                await self._read(self.access.require_admin, user_id)
            else:
                await self.authorize(user_id, agent_id)
            source_id = mapping["source_agent_id"]
        elif await self._read(self.access.repo.granted, user_id, agent_id):
            await self.synchronize(user_id)
            if not await self._read(self.access.repo.granted, user_id, agent_id):
                raise denied("resource_not_assigned")
            source_id = agent_id
        else:
            return None
        definition = await self.definition(source_id)
        row = await self._read(self.services.agent_repo.get, source_id)
        if (
            row is None
            or definition["source_row_id"] != row.id
            or (mapping is not None and mapping["source_row_id"] != row.id)
        ):
            raise denied("resource_source_unavailable", 409)
        return definition

    async def display_profile(self, user_id: int, agent_id: str) -> dict[str, Any]:
        definition = await self.display_definition(user_id, agent_id)
        if definition is None:
            return {}
        fields = definition["fields"]
        profile = {
            key: fields[key]
            for key in ("icon", "icon_name", "icon_url", "color", "template_name", "persona_mbti")
            if key in fields
        }
        if avatar := definition.get("avatar"):
            profile["icon_url"] = f"/api/agents/{agent_id}/avatar?v={avatar['sha256']}"
        return profile

    async def display_avatar(self, definition: dict[str, Any]) -> tuple[bytes, str] | None:
        from octop.infra.agents.experts.avatar import MAX_AVATAR_BYTES

        avatar = definition.get("avatar")
        if not avatar:
            return None
        data = await self._read(
            read_object,
            self.services.paths.root / "company-runtime-skills",
            avatar["sha256"],
            limit=MAX_AVATAR_BYTES,
        )
        return data, avatar["media_type"]

    async def content_revision(self, user_id: int) -> str:
        """Hash actual approved content, including script/attachment bytes.

        No TTL or client acknowledgement can keep a removed source authorized.
        This also detects source edits made through workspace tools or another process.
        """
        return (await self._capture_content(user_id))["content_revision"]

    async def _capture_content(
        self, user_id: int, source_id: str | None = None
    ) -> _ResourceCapture:
        """Hash exactly the bytes retained for a bundle, never a preceding scan.

        Legacy sources remain mutable. A caller publishing this capture must
        reauthorize and compare a second fresh revision before committing it.
        """
        if self.publication is not None:
            published = await self.publication.capture_employee(user_id, source_id)
            if published is not None:
                return cast(_ResourceCapture, published)
        state = await self._read(self.state, user_id)
        policy = await self._read(self.access.repo.source)
        catalog = await self.catalog()
        experts = {x["agent_id"]: x for x in catalog["experts"]}
        wanted = set(catalog["defaults"] + state["skill_ids"])
        selected = set(wanted) if source_id is not None else set()
        selected_expert = experts.get(source_id) if source_id is not None else None
        if source_id is not None:
            if (
                selected_expert is None
                or not selected_expert["available"]
                or source_id not in state["expert_ids"]
            ):
                raise denied("resource_source_unavailable", 409)
            selected.update(selected_expert["skill_ids"])
        content: dict[str, Any] = {"experts": {}, "skills": {}}
        for identity in state["expert_ids"]:
            expert = experts.get(identity)
            if expert is None or not expert["available"]:
                content["experts"][identity] = None
                continue
            content["experts"][identity] = await self.definition(identity)
            wanted.update(expert["skill_ids"])
        records = {x["id"]: x for x in catalog["skills"]}
        captured: dict[str, list[tuple[str, bytes]]] = {}
        total = count = 0
        for identity in sorted(wanted):
            record = records.get(identity)
            if record is None:
                content["skills"][identity] = None
                if identity in selected:
                    raise denied("resource_source_unavailable", 409)
                continue
            try:
                files = await self.files(record)
                content["skills"][identity] = [
                    (name, hashlib.sha256(data).hexdigest()) for name, data in sorted(files)
                ]
            except (OctopError, OSError):
                if identity in selected:
                    raise
                content["skills"][identity] = None
                continue
            if identity in selected:
                total += sum(len(data) for _, data in files)
                count += len(files)
                if total > self.max_bundle_bytes or count > 20000:
                    raise denied("expert_skills_too_large")
                captured[identity] = files
        content_revision = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        return {
            "revision": (
                f"{state['revision']}:{policy['revision']}:"
                f"{state['membership_revision']}:{content_revision}"
            ),
            "content_revision": content_revision,
            "definition": content["experts"].get(source_id),
            "records": records,
            "files": captured,
        }

    async def allowed_skill_records(self, user_id: int, agent_id: str) -> list[dict[str, Any]]:
        mapping = await self._read(self.access.repo.instance, agent_id)
        if mapping is None:
            return []
        await self.authorize(user_id, agent_id)
        catalog = await self.catalog()
        state = await self._read(self.state, user_id)
        expert = next(
            (x for x in catalog["experts"] if x["agent_id"] == mapping["source_agent_id"]), None
        )
        if expert is None or not expert["available"]:
            raise denied("resource_source_unavailable", 409)
        wanted = set(catalog["defaults"] + state["skill_ids"] + expert["skill_ids"])
        records = {x["id"]: x for x in catalog["skills"]}
        if not wanted <= records.keys():
            raise denied("resource_source_unavailable", 409)
        return [records[x] for x in sorted(wanted)]

    async def has_capability(self, user_id: int, capability: str) -> bool:
        return capability in await self.granted_capabilities(user_id)

    async def require_capability(self, user_id: int, capability: str) -> None:
        if not await self.has_capability(user_id, capability):
            raise denied("company_capability_not_assigned", 403)

    async def granted_capabilities(self, user_id: int) -> set[str]:
        """Capabilities require a grant; inheriting an expert never grants them."""
        from octop.infra.users.capabilities import CAPABILITIES

        user = await self._read(self.services.user_repo.get, user_id)
        if user is None or user.disabled:
            return set()
        if user.role == "admin":
            return set(CAPABILITIES)
        defaults = await self._read(self.repo.defaults)
        assigned = await self._read(self.repo.skills, user_id)
        return set(CAPABILITIES).intersection(defaults + assigned)

    async def assert_skill_read(self, user_id: int, agent_id: str, slug: str) -> None:
        if await self._read(self.access.repo.instance, agent_id) is not None:
            records = await self.allowed_skill_records(user_id, agent_id)
            if slug not in {x["slug"] for x in records}:
                raise denied("resource_not_assigned")

    async def skill_summaries(self, user_id: int, agent_id: str) -> list[dict[str, Any]]:
        """Display the authorized publication without starting a tool runtime."""
        records = await self.allowed_skill_records(user_id, agent_id)
        summaries: dict[str, dict[str, Any]] = {}
        for record in records:
            slug = record["slug"]
            summaries.setdefault(
                slug,
                {
                    "slug": slug,
                    "name": record["name"],
                    "description": record["description"],
                    "category": record["category"],
                    "kind": "workspace",
                    "path": f"skills/{slug}/SKILL.md",
                    "enabled": True,
                },
            )
        return list(summaries.values())

    async def skill_manifest(self, user_id: int, agent_id: str, slug: str) -> str:
        records = await self.allowed_skill_records(user_id, agent_id)
        selected = [record for record in records if record["slug"] == slug]
        if not selected:
            raise denied("resource_not_assigned")
        manifests = {
            data.decode("utf-8")
            for record in selected
            for name, data in await self.files(record)
            if name == "SKILL.md"
        }
        if len(manifests) != 1:
            raise denied("skill_name_conflict", 409)
        await self.authorize(user_id, agent_id)
        return manifests.pop()

    async def initialize_workspace(self, agent_id: str) -> None:
        """Materialize a lazily created managed workspace only for server execution."""
        mapping = await self._read(self.access.repo.instance, agent_id)
        if mapping is None or mapping["state"] != "ready":
            return
        await self.authorize(mapping["user_id"], agent_id)
        workspace = self.registry.workspace_for_agent(agent_id)
        if await workspace.aread_text(".octop/.bootstrapped") is not None:
            return
        definition = await self.definition(mapping["source_agent_id"])
        if definition["source_row_id"] != mapping["source_row_id"]:
            raise denied("resource_source_unavailable", 409)
        await workspace.aupload_many(
            [(name, value.encode()) for name, value in definition["files"].items()]
            + [(".octop/.bootstrapped", b"assigned expert instance\n")]
        )

    async def workspace_access(
        self,
        user_id: int,
        agent_id: str,
        workspace: Any,
        path: str,
        *,
        write: bool = False,
        recursive: bool = False,
    ) -> None:
        if await self._read(self.access.repo.instance, agent_id) is None:
            return
        try:
            relative = workspace_relative_path(workspace, path)
        except ValueError:
            raise denied(
                "managed_resource_read_only" if write else "resource_not_assigned"
            ) from None
        if write:
            if protected_definition_path(relative):
                raise denied("managed_resource_read_only")
            return
        parts = relative.split("/")
        if parts[0].casefold() == ".octop":
            parts = parts[1:]
            if not parts or parts[0].casefold() not in {"skills", "_builtin_skills"}:
                raise denied("resource_not_assigned")
        if not parts or (parts[0].startswith(".") and relative != "."):
            raise denied("resource_not_assigned")
        if parts[0].casefold() in {"skills", "_builtin_skills"}:
            if len(parts) < 2:
                raise denied("resource_not_assigned")
            await self.assert_skill_read(user_id, agent_id, parts[1])
        elif parts[0] == "agents" or (recursive and relative == "."):
            # Broad grep/archive operations can otherwise disclose excluded trees.
            raise denied("resource_not_assigned")

    async def refresh_user(self, user_id: int) -> None:
        if await self._read(self.services.company_runtime_repo.has_active_tasks, user_id):
            raise denied("client_config_busy", 409)
        mappings = await self._read(self.access.repo.instances, user_id)
        for mapping in mappings:
            if mapping["state"] != "ready":
                continue  # A failed/pending first open is retried by private-instance.
            if not await self._read(
                self.access.repo.granted,
                user_id,
                mapping["source_agent_id"],
                mapping["source_row_id"],
            ):
                continue
            agent_id = mapping["instance_agent_id"]
            if getattr(self.registry, "runtime_shards", None) is not None:
                async with self._prepare_locks.setdefault(agent_id, asyncio.Lock()):
                    await self.authorize(user_id, agent_id)
                    definition = await self.definition(mapping["source_agent_id"])
                    if definition["source_row_id"] != mapping["source_row_id"]:
                        raise denied("resource_source_unavailable", 409)
                    if self.registry._octop_harness_or_none(agent_id) is None:
                        # Desktop instances still need fresh display/configuration
                        # metadata, even when no server tool runtime is loaded.
                        await self.registry.sync_managed_definition(
                            agent_id, definition["fields"], definition["runtime_settings"]
                        )
                        continue
            await self.prepare(agent_id)

    async def cancel_streams(self, user_ids: list[int]) -> None:
        execution = getattr(self.server, "execution", None)
        if execution is not None:
            for user_id in user_ids:
                await self._read(execution.repo.cancel_user, user_id)
        runtime = getattr(self.server, "app_runtime", None)
        processor = getattr(getattr(runtime, "gateway", None), "processor", None)
        if processor is None:
            return
        for user_id in user_ids:
            for row in await self._read(self.services.agent_repo.list_by_user, user_id):
                for thread in await self._read(
                    self.services.thread_repo.list_by_agent_user,
                    agent_id=row.agent_id,
                    user_id=user_id,
                    limit=10000,
                ):
                    try:
                        processor.cancel_agent_stream(row.agent_id, thread.thread_id)
                    except Exception:
                        logger.warning(
                            "resource changes saved; live stream cancellation failed", exc_info=True
                        )

    async def catalog(self) -> dict[str, Any]:
        if self.publication is not None:
            published = await self.publication.current()
            if published is not None:
                result = copy.deepcopy(published[1]["catalog"])
                result["defaults"] = await self._read(self.repo.defaults)
                return dict(result)
        try:
            return await self._catalog_io.run_async(self._catalog)
        except ReadQueueFull as exc:
            raise self._capacity_error() from exc

    async def _catalog(self) -> dict[str, Any]:
        source = await self._read(self.access.repo.source)
        source_id = source["source_user_id"]
        owner = await self._read(self.services.user_repo.get, source_id) if source_id else None
        result: dict[str, Any] = {
            "experts": [],
            "skills": [],
            "defaults": await self._read(self.repo.defaults),
        }
        if not owner or owner.disabled or owner.role != "admin":
            return result
        records: dict[str, dict[str, Any]] = {}
        store = SkillPackageStore(
            repo=self.services.skill_package_repo, root=self.services.paths.skill_packages_dir
        )
        packages = {p.id: p for p in await self._read(self.services.skill_package_repo.list_all)}
        for row in await self._read(self.services.agent_repo.list_by_user, source_id):
            if not row.enabled or row.kind != "expert":
                continue
            skill_ids = []
            try:
                async with self._source_agent(row.agent_id) as agent:
                    summaries = await agent.list_skill_summaries()
                    for summary in summaries:
                        slug = str(summary.get("slug") or "")
                        if (
                            not summary.get("enabled", True)
                            or summary.get("corrupt")
                            or not slug
                            or slug in MANAGEMENT_SKILLS
                        ):
                            continue
                        if any(x in slug for x in ("/", "\\")) or slug.startswith("."):
                            continue
                        kind = summary.get("kind", "workspace")
                        package_id = summary.get("package_id")
                        if kind == "package" and package_id in packages:
                            identity = f"package:{package_id}:{slug}"
                            origin = packages[package_id].name
                        elif kind == "builtin":
                            identity, origin = f"builtin:{slug}", "builtin"
                        else:
                            identity, origin = f"workspace:{row.id}:{slug}", row.name
                        root = "_builtin_skills" if kind == "builtin" else "skills"
                        path = str(summary.get("path") or f"{root}/{slug}/SKILL.md")
                        metadata: dict[str, Any] = {}
                        if kind == "package" and package_id in packages:
                            manifest = store.package_skills_dir(package_id) / slug / "SKILL.md"
                            content = await self._read(manifest.read_text, encoding="utf-8")
                        else:
                            content = await agent.workspace.aread_text(path, limit=262144)
                        if content is None:
                            raise denied("resource_source_unavailable", 409)
                        metadata, _ = parse_frontmatter(content)
                        records.setdefault(
                            identity,
                            {
                                "id": identity,
                                "slug": slug,
                                "name": str(
                                    summary.get("display_name") or summary.get("name") or slug
                                ),
                                "description": str(summary.get("description") or ""),
                                "category": str(
                                    metadata.get("category") or summary.get("category") or "other"
                                ),
                                "kind": kind,
                                "source_name": origin,
                                "expert_ids": [],
                                "_agent_id": row.agent_id,
                                "_path": path,
                                "_package_id": package_id if kind == "package" else None,
                            },
                        )
                        records[identity]["expert_ids"].append(row.agent_id)
                        skill_ids.append(identity)
                available = True
            except Exception:
                # An unavailable expert cannot silently become an incomplete skill source.
                available = False
                logger.warning(
                    "expert skill catalog unavailable for %s", row.agent_id, exc_info=True
                )
            result["experts"].append(
                {
                    "agent_id": row.agent_id,
                    "name": row.name,
                    "description": row.description,
                    "skill_ids": skill_ids,
                    "available": available,
                }
            )
        # A global package is an extra candidate only when this source administrator
        # installed it, or explicitly mounted it in one of the source experts above.
        for package in packages.values():
            if package.created_by != str(source_id) or package.copy_policy == "deny":
                continue
            for summary in await self._read(store.list_skill_summaries, package.id):
                slug = summary["slug"]
                if summary.get("corrupt") or slug in MANAGEMENT_SKILLS:
                    continue
                identity = f"package:{package.id}:{slug}"
                records.setdefault(
                    identity,
                    {
                        "id": identity,
                        "slug": slug,
                        "name": summary.get("display_name") or summary.get("name") or slug,
                        "description": summary.get("description", ""),
                        "category": summary.get("category") or "other",
                        "kind": "package",
                        "source_name": package.name,
                        "expert_ids": [],
                        "_package_id": package.id,
                        "_agent_id": None,
                        "_path": None,
                    },
                )
        from octop.infra.users.capabilities import capability_records

        records.update({record["id"]: record for record in capability_records()})
        result["skills"] = list(records.values())
        return result

    @staticmethod
    def public_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
        return {
            **catalog,
            "skills": [
                {k: v for k, v in row.items() if not k.startswith("_")} for row in catalog["skills"]
            ],
        }

    @staticmethod
    def validate(catalog: dict[str, Any], expert_ids: list[str], skill_ids: list[str]) -> None:
        experts = {e["agent_id"] for e in catalog["experts"] if e["available"]}
        skills = {s["id"] for s in catalog["skills"]}
        if len(set(expert_ids)) != len(expert_ids) or len(set(skill_ids)) != len(skill_ids):
            raise denied("invalid_expert_assignment", 422)
        if not set(expert_ids) <= experts or not set(skill_ids) <= skills:
            raise denied("resource_source_unavailable", 409)

    async def save_pool(
        self, actor_id: int, scope_id: str, body: dict[str, Any], *, session: str = ""
    ) -> dict[str, Any]:
        actor = await self._read(self.access.require_admin, actor_id)
        for employee in await self._read(self.services.user_repo.list, include_disabled=True):
            if employee.role != "admin":
                await self.synchronize(employee.id)
        if scope_id != UNASSIGNED:
            departments = await self.server.communication.organization_request(
                actor_id, session, "GET", "/organization/departments", {}
            )
            if not any(d["department_id"] == scope_id for d in departments["items"]):
                raise denied("department_not_found", 404)
        catalog = await self.catalog()
        old = await self._read(self.repo.pool, scope_id)
        self.validate(
            catalog,
            [x for x in body["expert_ids"] if x not in old["expert_ids"]],
            [x for x in body["skill_ids"] if x not in old["skill_ids"]],
        )
        try:
            affected = await self._read(
                self.repo.save_pool,
                scope_id,
                body["expert_ids"],
                body["skill_ids"],
                body["expected_revision"],
                body["expected_source_revision"],
                actor.username,
            )
        except AssignmentConflict as exc:
            raise denied(str(exc), 409) from exc
        await self.cancel_streams(affected)
        return {**(await self._read(self.repo.pool, scope_id)), "affected_user_ids": affected}

    async def save_defaults(self, actor_id: int, body: dict[str, Any]) -> dict[str, Any]:
        actor = await self._read(self.access.require_admin, actor_id)
        self.validate(await self.catalog(), [], body["skill_ids"])
        try:
            await self._read(
                self.repo.save_defaults,
                body["skill_ids"],
                body["expected_source_revision"],
                actor.username,
            )
        except AssignmentConflict as exc:
            raise denied(str(exc), 409) from exc
        employees = await self._read(self.services.user_repo.list, include_disabled=True)
        await self.cancel_streams([u.id for u in employees if u.role != "admin"])
        return {"skill_ids": body["skill_ids"]}

    async def save_users(self, actor_id: int, body: dict[str, Any]) -> list[dict[str, Any]]:
        actor = await self._read(self.access.require_admin, actor_id)
        catalog = await self.catalog()
        employees = body["employees"]
        if not employees or len({x["id"] for x in employees}) != len(employees):
            raise denied("invalid_expert_assignment", 422)
        for item in employees:
            self.validate(catalog, item["expert_ids"], item["skill_ids"])
            membership = await self.synchronize(item["id"])
            if (
                membership["revision"] != item["membership_revision"]
                or membership["scope_id"] != item["scope_id"]
            ):
                raise denied("organization_revision_conflict", 409)
        if len({x["scope_id"] for x in employees}) != 1:
            raise denied("batch_same_department_required", 422)
        try:
            await self._read(
                self.repo.save_users, employees, body["expected_source_revision"], actor.username
            )
        except AssignmentConflict as exc:
            raise denied(str(exc), 409) from exc
        await self.cancel_streams([x["id"] for x in employees])
        return [await self._read(self.state, x["id"]) for x in employees]

    async def basic(
        self, actor_id: int, user_id: int, session: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        await self._read(self.access.require_admin, actor_id)
        target = await self._read(self.services.user_repo.get, user_id)
        if target is None or target.role == "admin":
            raise denied("expert_assignment_employee_required", 422)
        membership = await self.synchronize(user_id)
        if membership["revision"] != body["membership_revision"]:
            raise denied("organization_revision_conflict", 409)
        original = await self._read(self.state, user_id)
        source = await self._read(self.access.repo.source)
        if (
            original["revision"] != body["expected_revision"]
            or source["revision"] != body["expected_source_revision"]
        ):
            raise denied("policy_revision_conflict", 409)
        bridge = self.server.communication
        path = "/organization/users/" + bridge.mapped_id(user_id)
        scope_id = body["department_id"] or UNASSIGNED
        if scope_id != membership["scope_id"]:
            try:
                operation_id = uuid.uuid4().hex
                await self._read(
                    self.repo.prepare_move,
                    user_id,
                    scope_id,
                    membership["revision"],
                    body["expected_revision"],
                    body["expected_source_revision"],
                    operation_id,
                )
                await bridge.organization_request(
                    actor_id,
                    session,
                    "PUT",
                    path + "/department",
                    {
                        "department_id": body["department_id"],
                        "expected_revision": membership["revision"],
                        "operation_id": operation_id,
                    },
                )
            except AssignmentConflict as exc:
                raise denied(str(exc), 409) from exc
            except OctopError as exc:
                if exc.status < 500:
                    await self._read(self.repo.cancel_move, user_id, membership["revision"])
                exc.details.update(refresh_required=True)
                if exc.status >= 500:
                    exc.details["reason"] = "basic_settings_partial"
                raise
            try:
                await self.synchronize(user_id)
            except OctopError as exc:
                exc.details.update(reason="basic_settings_partial", refresh_required=True)
                raise
        try:
            await bridge.organization_request(
                actor_id,
                session,
                "PUT",
                path + "/job-title",
                {"job_title_id": body["job_title_id"]},
            )
        except OctopError as exc:
            if scope_id != membership["scope_id"]:
                exc.details.update(reason="basic_settings_partial", refresh_required=True)
            raise
        await self.cancel_streams([user_id])
        state = await self._read(self.state, user_id)
        return state

    async def files(self, record: dict[str, Any]) -> list[tuple[str, bytes]]:
        """Only files under the selected concrete skill; no whole-package copying."""
        try:
            return await self._workspace_io.run_async(lambda: self._files(record))
        except ReadQueueFull as exc:
            raise self._capacity_error() from exc

    async def _files(self, record: dict[str, Any]) -> list[tuple[str, bytes]]:
        if "_published_files" in record:
            root = self.services.paths.root / "company-runtime-skills"
            try:
                return [
                    (entry["path"], await self._read(read_object, root, entry["sha256"]))
                    for entry in record["_published_files"]
                ]
            except (OSError, ValueError) as exc:
                raise denied("resource_snapshot_unavailable", 503) from exc
        from octop.infra.users.capabilities import CAPABILITIES, capability_content

        if record["id"] in CAPABILITIES:
            return [("SKILL.md", capability_content(record["id"]))]
        slug = record["slug"]
        if record["_package_id"]:
            root = self.services.paths.skill_packages_dir / record["_package_id"] / "skills" / slug

            def read() -> list[tuple[str, bytes]]:
                if root.is_symlink() or not (root / "SKILL.md").is_file():
                    raise denied("resource_source_unavailable", 409)
                items: list[tuple[str, bytes]] = []
                total = 0
                for path in root.rglob("*"):
                    relative = path.relative_to(root)
                    if (
                        path.is_file()
                        and not path.is_symlink()
                        and not any(
                            p.startswith(".") or p in {"node_modules", "__pycache__"}
                            for p in relative.parts
                        )
                    ):
                        size = path.stat().st_size
                        total += size
                        if (
                            size > 64 * 1024 * 1024
                            or total > self.max_bundle_bytes
                            or len(items) >= 20000
                        ):
                            raise denied("expert_skills_too_large")
                        items.append((relative.as_posix(), path.read_bytes()))
                return items

            return await self._read(read)
        async with self._source_agent(record["_agent_id"]) as agent:
            root = str(PurePosixPath(record["_path"]).parent)
            manifest = await agent.workspace.adownload_bytes(root + "/SKILL.md")
            if manifest is None:
                raise denied("resource_source_unavailable", 409)
            if len(manifest) > 64 * 1024 * 1024:
                raise denied("expert_skills_too_large")
            items = [("SKILL.md", manifest)]
            total = len(manifest)
            listing_root = root
            if getattr(agent.workspace, "system_files_path", ""):
                canonical = agent.workspace.resolve_path(root)
                if await agent.workspace.aread_text(canonical + "/SKILL.md", limit=262144) is None:
                    listing_root = str(PurePosixPath(str(agent.workspace.workspace_dir)) / root)
            listing = await agent.workspace.aglob("**/*", listing_root)
            if listing is None:
                raise denied("resource_source_unavailable", 409)
            if listing.truncated:
                raise denied("expert_skill_listing_truncated")
            roots = {
                root,
                agent.workspace.system_rel(root),
                agent.workspace.present_path(listing_root),
            }
            for entry in listing.matches:
                path = str(entry.get("path", ""))
                matched = next((r for r in roots if path.startswith(r + "/")), None)
                if entry.get("is_dir") or matched is None:
                    continue
                relative = path[len(matched) + 1 :]
                if relative == "SKILL.md" or any(
                    p.startswith(".") or p in {"node_modules", "__pycache__", ".."}
                    for p in PurePosixPath(relative).parts
                ):
                    continue
                value = await agent.workspace.adownload_bytes(path)
                if value is not None:
                    total += len(value)
                    if (
                        len(value) > 64 * 1024 * 1024
                        or total > self.max_bundle_bytes
                        or len(items) >= 20000
                    ):
                        raise denied("expert_skills_too_large")
                    items.append((relative, value))
            return items

    async def snapshot(self, user_id: int, agent_id: str) -> dict[str, Any] | None:
        try:
            return await self._snapshot_io.run_async(lambda: self._snapshot(user_id, agent_id))
        except ReadQueueFull as exc:
            raise self._capacity_error() from exc

    async def _snapshot(self, user_id: int, agent_id: str) -> dict[str, Any] | None:
        mapping = await self._read(self.access.repo.instance, agent_id)
        if mapping is None:
            return None
        await self.authorize(user_id, agent_id)
        head = await self._read(self.manifests.head, agent_id)
        capture = await self._capture_content(user_id, mapping["source_agent_id"])
        assets: list[dict[str, Any]] = []
        uploads: dict[str, bytes] = {}
        slugs: dict[str, str] = {}
        total = 0
        cache = self.services.paths.root / "company-runtime-skills"
        for identity, files in sorted(capture["files"].items()):
            record = capture["records"][identity]
            hashes = [(name, hashlib.sha256(data).hexdigest()) for name, data in sorted(files)]
            fingerprint = hashlib.sha256(json.dumps(hashes).encode()).hexdigest()
            slug = record["slug"]
            if slug in slugs:
                if slugs[slug] != fingerprint:
                    raise denied("skill_name_conflict", 409)
                continue
            slugs[slug] = fingerprint
            for name, data in sorted(files):
                total += len(data)
                if total > self.max_bundle_bytes or len(assets) >= 20000:
                    raise denied("expert_skills_too_large")
                try:
                    digest = await self._read(store_asset, cache, data)
                except (OSError, ValueError) as exc:
                    raise denied("resource_snapshot_unavailable", 503) from exc
                item = {"path": f"skills/{slug}/{name}", "sha256": digest, "size": len(data)}
                if name == "SKILL.md":
                    try:
                        metadata, _ = parse_frontmatter(data.decode("utf-8"))
                    except UnicodeError as exc:
                        raise denied("resource_source_unavailable", 409) from exc
                    item.update(
                        content=base64.b64encode(data).decode(),
                        description=str(metadata.get("description") or ""),
                    )
                assets.append(item)
                uploads[f"skills/{slug}/{name}"] = data
        await self.authorize(user_id, agent_id)
        if capture["revision"] != await self.revision(user_id):
            raise denied("employee_resources_changed", 409)
        manifest = {
            "validation": "published_source" if self.publication is not None else "legacy_content",
            "user_id": user_id,
            "agent_id": agent_id,
            "source_agent_id": mapping["source_agent_id"],
            "source_row_id": mapping["source_row_id"],
            "resource_revision": capture["revision"],
            "definition": capture["definition"],
            "assets": [{k: v for k, v in item.items() if k != "content"} for item in assets],
        }
        try:
            digest = await self._read(store_manifest, cache, manifest)
        except (OSError, ValueError) as exc:
            raise denied("resource_snapshot_unavailable", 503) from exc
        try:
            publication = await self._read(
                self.manifests.publish,
                user_id=user_id,
                agent_id=agent_id,
                source_row_id=mapping["source_row_id"],
                expected_generation=head["generation"] if head else 0,
                manifest_sha256=digest,
                resource_revision=capture["revision"],
            )
        except ManifestConflict as exc:
            raise denied(str(exc), 409) from exc
        return {
            "assets": assets,
            "uploads": uploads,
            "slugs": set(slugs),
            "revision": capture["revision"],
            "definition": capture["definition"],
            "manifest_sha256": digest,
            "generation": publication["generation"],
        }

    async def prepare(self, agent_id: str, *, invocation: bool = False) -> None:
        mapping = await self._read(self.access.repo.instance, agent_id)
        if mapping is None:
            return
        async with self._prepare_locks.setdefault(agent_id, asyncio.Lock()):
            await self.authorize(mapping["user_id"], agent_id)
            agent = (
                await self.registry.ensure_runtime(agent_id)
                if getattr(self.registry, "runtime_shards", None) is not None
                else self.registry.get_agent(agent_id)
            )
            snapshot = await self.snapshot(mapping["user_id"], agent_id)
            assert snapshot is not None
            revision = snapshot["revision"]
            definition = snapshot["definition"]
            fingerprint = hashlib.sha256(
                json.dumps(
                    {"assets": snapshot["assets"], "definition": definition}, sort_keys=True
                ).encode()
            ).hexdigest()
            changed = (
                getattr(agent, "_octop_resource_revision", None) != revision
                or getattr(agent, "_octop_resource_fingerprint", None) != fingerprint
            )
            if changed and self.registry._active_invocations.get(agent_id, 0) > (
                1 if invocation else 0
            ):
                raise denied(
                    "employee_resources_changed" if invocation else "client_config_busy", 409
                )
            if changed:
                # Remove legacy copied skills as well as revoked skills. Nothing
                # from the administrator's unapproved directories survives sync.
                for directory in ("skills", "agents"):
                    if await agent.workspace.aexists(directory):
                        await agent.workspace.adelete(directory)
                    # New workspaces map system paths into .octop, but old
                    # published copies may also have a workspace-root tree.
                    if getattr(agent.workspace, "system_files_path", ""):
                        legacy = str(PurePosixPath(str(agent.workspace.workspace_dir)) / directory)
                        if await agent.workspace.aexists(legacy):
                            await agent.workspace.adelete(legacy)
                await agent.workspace.aupload_many(list(snapshot["uploads"].items()))
                for name in DEFINITION_FILES:
                    if name in definition["files"]:
                        await agent.workspace.aupload_many(
                            [(name, definition["files"][name].encode())]
                        )
                    elif await agent.workspace.aexists(name):
                        await agent.workspace.adelete(name)
                sync = getattr(self.registry, "sync_managed_definition", None)
                if sync is not None:
                    await sync(agent_id, definition["fields"], definition["runtime_settings"])
                    agent = self.registry.get_agent(agent_id)
            # Enforce even on a cache hit: runtime denylist changes and new
            # directories must not become authorization by being absent before.
            summaries = await agent.list_skill_summaries()
            disabled = {
                str(x["slug"]) for x in summaries if str(x["slug"]) not in snapshot["slugs"]
            }
            disabled.update(MANAGEMENT_SKILLS)
            agent.set_skills_disabled(disabled)
            agent._octop_resource_revision = snapshot["revision"]
            agent._octop_resource_fingerprint = fingerprint
            agent._octop_allowed_skills = snapshot["slugs"]
