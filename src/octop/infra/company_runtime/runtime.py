"""Embedded Octop engine in enterprise mode, controlled exclusively over stdio."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from deepagents.backends import CompositeBackend, FilesystemBackend
from langchain.agents.middleware import ModelRetryMiddleware
from langchain_core.messages import HumanMessage, messages_to_dict
from langchain_core.tools import ToolException, tool
from octop_harness import HarnessAgent, HarnessAgentConfig
from octop_harness.config import ModelConfig, ProviderConfig
from octop_harness.llm.factory import ChatModelFactory

from octop.infra.backend.grep_safety import install_safe_grep_fallback
from octop.infra.company_runtime.checkpoints import LocalCheckpointSaver, flush_checkpoints
from octop.infra.company_runtime.client import (
    CompanyChatModel,
    CompanyClient,
    CompanyModelError,
    CompanyServiceError,
)
from octop.infra.company_runtime.local import (
    OperationJournal,
    ProjectBackend,
    ReceiptMiddleware,
    artifact_index,
    atomic_json,
    image_reference_inputs,
    local_attachment_metadata,
)
from octop.infra.company_runtime.steer import CompanySteering
from octop.infra.gateway.process.message_keys import INBOUND_ATTACHMENTS_KEY
from octop.infra.utils.asyncio import offload

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class CompanyModelFactory(ChatModelFactory):
    def __init__(self, client: CompanyClient, task_id: str, models: list[str]) -> None:
        providers: dict[str, ProviderConfig] = {}
        for ref in models:
            provider, _, model = ref.partition("/")
            providers.setdefault(
                provider,
                ProviderConfig(
                    id=provider, base_url="http://company.invalid", api_key="company-proxy-only"
                ),
            ).models.append(ModelConfig(id=model))
        super().__init__(list(providers.values()))
        self.client, self.task_id = client, task_id

    def get_chat_model(self, model_ref: str, *, max_retries: int | None = None) -> Any:
        if model_ref not in self.list_refs():
            raise ValueError("model_not_available")
        return CompanyChatModel(client=self.client, task_id=self.task_id, model_ref=model_ref)


def cache_expert(root: Path, snapshot: dict[str, Any]) -> Path:
    cache = root / str(snapshot["version"])
    for item in snapshot["skills"]:
        if "content" not in item:
            continue
        path = PurePosixPath(item["path"])
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in item["path"]
            or path.parts[0] != "skills"
        ):
            raise ValueError("invalid_skill_path")
        data = base64.b64decode(item["content"], validate=True)
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("skill_checksum_mismatch")
        dest = cache.joinpath(*path.parts)
        if dest.is_symlink() or not dest.resolve().is_relative_to(root.resolve()):
            raise ValueError("invalid_skill_cache")
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not dest.exists():
            with dest.open("xb") as stream:
                stream.write(data)
    return cache


class EnterpriseRuntime:
    def __init__(self, emit: Any) -> None:
        # Employee processes do not run OctopServer.start().
        install_safe_grep_fallback()
        self.client: CompanyClient | None = None
        self.tasks: dict[str, dict[str, Any]] = {}

        def emit_scoped(event: dict[str, Any]) -> None:
            task_id = self.tasks.get(event.get("thread_id", ""), {}).get("task_id")
            emit({**event, **({"task_id": task_id} if task_id else {})})

        self.emit = emit_scoped
        self.work: dict[str, asyncio.Task[Any]] = {}
        self.sync_work: dict[str, asyncio.Task[Any]] = {}
        self.sync_locks: dict[str, asyncio.Lock] = {}
        self.workspace = Path()
        self.state = Path()
        self.authorized = False
        self.tools: dict[str, str] = {}
        self.tool_path = ""
        self.installation_token = ""
        self.steering = CompanySteering(self.emit)

    async def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.client:
            raise RuntimeError("runtime_already_initialized")
        self.workspace = Path(params["workspace"]).expanduser().resolve(strict=True)
        self.state = Path(params["state_dir"]).expanduser().resolve()
        if not self.workspace.is_dir() or self.state.is_relative_to(self.workspace):
            raise RuntimeError("runtime_data_must_be_outside_project")
        await offload(self.state.mkdir, parents=True, exist_ok=True, mode=0o700)
        from octop_harness.observability.logging import setup_logging

        setup_logging(log_dir=self.state / "logs")
        install_file = self.state / "installation.json"
        if install_file.exists():
            self.installation_token = json.loads(await offload(install_file.read_text))["token"]
        else:
            self.installation_token = secrets.token_urlsafe(32)
            await offload(atomic_json, install_file, {"token": self.installation_token})
        self.authorized = params.get("execute_authorized") is True
        self.client = CompanyClient(params["company_url"], params["credential"])
        capabilities = await self.client.request("capabilities")
        if capabilities["protocol_version"] != 1 or "checkpoint_batches" not in capabilities.get(
            "features", []
        ):
            raise RuntimeError("company_protocol_incompatible")
        self.tools = {"python": sys.version.split()[0], "harness": version("octop-harness")}
        # The native shell supplies a verified package path. Development is explicit.
        bin_dir = Path(sys.executable).parent
        tool_bin = os.environ.get("OCTOP_COMPANY_TOOL_BIN")
        ffmpeg = shutil.which("ffmpeg", path=tool_bin) if tool_bin else shutil.which("ffmpeg")
        self.tool_path = os.pathsep.join(
            [str(bin_dir), str(Path(ffmpeg).parent) if ffmpeg else str(bin_dir), os.defpath]
        )
        if ffmpeg:
            process = await asyncio.create_subprocess_exec(
                ffmpeg, "-version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            self.tools["ffmpeg"] = stdout.decode("utf-8", "replace").splitlines()[0]
        for module, package in (
            ("openpyxl", "openpyxl"),
            ("docx", "python-docx"),
            ("PIL", "Pillow"),
        ):
            try:
                __import__(module)
                self.tools[package] = version(package)
            except ImportError:
                self.tools[package] = "unavailable"
        # Restore receipt visibility only. Never restart a graph or command automatically.
        await self.restore_tasks()
        return self.status()

    async def restore_tasks(self) -> None:
        saved_tasks: list[tuple[int, Path, dict[str, Any]]] = []
        for path in self.state.glob("tasks/*/task.json"):
            saved = json.loads(await offload(path.read_text, encoding="utf-8"))
            # Freeze a legacy record's order before writing any recovery updates.
            started = int(saved.setdefault("started_at_ns", path.stat().st_mtime_ns))
            saved_tasks.append((started, path, saved))
        for _, path, saved in sorted(saved_tasks, key=lambda item: (item[0], str(item[1]))):
            if saved["status"] in {"running", "cancel_requested"}:
                saved["status"] = "unknown"
            await offload(atomic_json, path, saved)
            self.tasks[saved["thread_id"]] = saved
            if saved.get("sync_state") == "synced" and saved["status"] in {
                "completed",
                "failed",
                "cancelled",
            }:
                continue
            # Restore receipts only; never restart a graph or local command.
            with contextlib.suppress(Exception):
                await self.recover(saved, path.parent)
            if saved.get("sync_state") == "pending" and saved["task_id"] not in self.sync_work:
                self.sync_work[saved["task_id"]] = asyncio.create_task(
                    self.retry_sync(saved, path.parent)
                )

    def status(self) -> dict[str, Any]:
        return {
            "protocol_version": 1,
            "capabilities": ["edit_message"],
            "tools": self.tools,
            "execute_authorized": self.authorized,
            "active_tasks": [
                {
                    k: value[k]
                    for k in ("task_id", "thread_id", "status", "recovery_state", "sync_state")
                    if k in value
                }
                for value in self.tasks.values()
            ],
        }

    async def start(self, params: dict[str, Any]) -> dict[str, str]:
        if not self.client or not self.authorized:
            raise RuntimeError("请选择工作目录并授权本机终端执行")
        if "ffmpeg" not in self.tools or any(
            self.tools.get(k) == "unavailable" for k in ("openpyxl", "python-docx", "Pillow")
        ):
            raise RuntimeError("企业工具包不完整，请重新安装公司客户端")
        for key in ("task_id", "thread_id", "agent_id"):
            if not _IDENTIFIER.fullmatch(str(params.get(key, ""))):
                raise ValueError("invalid_task_identity")
        thread = params["thread_id"]
        if thread in self.work or len(self.work) >= 4:
            raise RuntimeError("已有任务正在本机执行")
        if self.tasks.get(thread, {}).get("status") == "unknown":
            raise RuntimeError("上次任务结果待核对，不能自动重放。请检查已有成果后新建任务")
        recovery = secrets.token_urlsafe(32)
        request = {
            key: params[key]
            for key in (
                "task_id",
                "thread_id",
                "agent_id",
                "text",
                "model",
                "reasoning_mode",
                "reasoning_effort",
                "conversation_mode",
                "knowledge_base_ids",
                "edit_message_id",
            )
            if key in params
        }
        request["recovery_token"] = recovery
        request["installation_token"] = self.installation_token
        previous = self.tasks.get(thread)
        if previous and previous.get("sync_state") == "pending":
            await self.recover(previous, self.state / "tasks" / previous["task_id"])
        if (
            previous
            and previous.get("project_path")
            and Path(previous["project_path"]).parent != self.workspace
        ):
            raise RuntimeError("此任务绑定原工作目录，请恢复原目录或创建新任务")
        inbound = await offload(
            local_attachment_metadata, self.workspace / thread, params.get("local_attachments", [])
        )
        acquired = await self.client.request("tasks", request)
        if acquired["status"] != "running":
            raise RuntimeError("task_already_finished")
        request["inbound_attachments"] = inbound
        task = {
            "task_id": params["task_id"],
            "thread_id": thread,
            "agent_id": params["agent_id"],
            "status": "running",
            "recovery_token": recovery,
            "started_at_ns": time.time_ns(),
            "seq": 0,
            "project_path": str(self.workspace / thread),
        }
        self.tasks[thread] = task
        task_dir = self.state / "tasks" / task["task_id"]
        await offload(atomic_json, task_dir / "task.json", task)
        self.work[thread] = asyncio.create_task(self.run(task, request, acquired, task_dir))
        self.steering.begin(thread)
        return {"task_id": task["task_id"]}

    async def recover(self, task: dict[str, Any], directory: Path) -> None:
        async with self.sync_locks.setdefault(task["task_id"], asyncio.Lock()):
            await self._recover(task, directory)

    async def _recover(self, task: dict[str, Any], directory: Path) -> None:
        assert self.client
        # Explicitly acknowledged missing receipts remain local; do not resurrect them.
        if task.get("recovery_state") == "missing" and (
            task.get("acknowledged_at")
            or task.get("status") in {"completed", "failed", "cancelled"}
        ):
            return
        try:
            remote = await self.client.request(
                f"tasks/{task['task_id']}/recover",
                {
                    "installation_token": self.installation_token,
                    "recovery_token": task["recovery_token"],
                },
            )
        except CompanyServiceError as exc:
            if exc.status != 404 or str(exc) != "task_not_found":
                raise
            task["recovery_state"] = "missing"
            await offload(atomic_json, directory / "task.json", task)
            return
        task.pop("recovery_state", None)
        if remote.get("status") not in {"completed", "failed", "cancelled"}:
            await flush_checkpoints(self.client, task["task_id"], directory)
        await self.flush_events(task, directory)
        remote_status = remote.get("status")
        terminal = task.get("reported_status")
        if remote_status in {"completed", "failed", "cancelled"}:
            # The server's final receipt wins over an interrupted local status.
            task["status"] = remote_status
            task["reported_status"] = remote_status
            task["sync_state"] = "synced"
            await offload(atomic_json, directory / "task.json", task)
        elif terminal in {"completed", "failed", "cancelled"}:
            await self.client.request(f"tasks/{task['task_id']}/status", {"status": terminal})
            task["status"] = terminal
            task["sync_state"] = "synced"
            await offload(atomic_json, directory / "task.json", task)

    async def flush_events(self, task: dict[str, Any], directory: Path) -> None:
        assert self.client
        for pending in sorted((directory / "pending").glob("*.json")):
            event = json.loads(await offload(pending.read_text, encoding="utf-8"))
            await self.client.request(f"tasks/{task['task_id']}/events", event)
            await offload(pending.unlink)

    async def retry_sync(self, task: dict[str, Any], directory: Path) -> None:
        delay = 1
        try:
            while task.get("sync_state") == "pending":
                await asyncio.sleep(delay)
                try:
                    await self.recover(task, directory)
                except CompanyServiceError as exc:
                    if exc.status in {401, 403, 404, 409}:
                        return
                except (httpx.TransportError, httpx.HTTPStatusError):
                    pass
                else:
                    return
                delay = min(delay * 2, 30)
        finally:
            self.sync_work.pop(task["task_id"], None)

    async def reconcile(self, params: dict[str, Any]) -> dict[str, str]:
        thread, resolution = params["thread_id"], params.get("resolution")
        if thread in self.work or resolution not in {"failed", "cancelled"}:
            raise ValueError("Only a stopped task may be acknowledged as interrupted")
        task = self.tasks.get(thread)
        if not task or params.get("task_id", task["task_id"]) != task["task_id"]:
            raise ValueError("task_selection_changed")
        directory = self.state / "tasks" / task["task_id"]
        await self.recover(task, directory)
        assert self.client
        if task["status"] in {"completed", "failed", "cancelled"}:
            return {"status": task["status"]}
        if task.get("recovery_state") != "missing":
            await self.client.request(f"tasks/{task['task_id']}/status", {"status": resolution})
        task["status"] = resolution
        task["reported_status"] = resolution
        task["acknowledged_at"] = time.time_ns()
        await offload(atomic_json, directory / "task.json", task)
        return {"status": resolution}

    async def cancel(self, params: dict[str, Any]) -> dict[str, str]:
        thread = params["thread_id"]
        work = self.work.get(thread)
        if not work:
            return {"status": "not_running"}
        self.tasks[thread]["status"] = "cancel_requested"
        self.steering.cancel(thread)
        work.cancel()
        return {"status": "cancel_requested"}

    async def event(self, task: dict[str, Any], directory: Path, payload: dict[str, Any]) -> None:
        assert self.client
        task["seq"] += 1
        seq = task["seq"]
        event = {"event_id": f"{task['task_id']}_{seq}", "seq": seq, "payload": payload}
        pending = directory / "pending" / f"{seq:08}.json"
        await offload(atomic_json, pending, event)
        await offload(atomic_json, directory / "task.json", task)

    async def run(
        self,
        task: dict[str, Any],
        request: dict[str, Any],
        acquired: dict[str, Any],
        directory: Path,
    ) -> None:
        assert self.client
        client = self.client
        thread, task_id = task["thread_id"], task["task_id"]
        project = self.workspace / acquired["project_name"]
        backend = None
        saver = None
        agent = None
        watcher = None
        status, error, artifacts = "completed", "", []
        try:
            if project.is_symlink() or not project.resolve().is_relative_to(self.workspace):
                raise RuntimeError("project_path_invalid")
            await offload(project.mkdir, parents=True, exist_ok=True)
            # Unchanged files from earlier turns must never move into this turn.
            before = await offload(artifact_index, project, limit=None)
            snapshot = acquired["expert"]
            if snapshot["harness_version"] != version("octop-harness"):
                raise RuntimeError("公司引擎版本与客户端不兼容，请安装匹配的客户端")
            cache = await offload(cache_expert, self.state / "experts", snapshot)
            saver = LocalCheckpointSaver(client, task_id, thread, directory)
            backend = ProjectBackend(
                project,
                directory,
                lambda event: self.emit({**event, "thread_id": thread}),
                tool_path=self.tool_path,
            )

            async def authorize() -> None:
                result = await client.request(f"tasks/{task_id}")
                if result["status"] != "running":
                    raise asyncio.CancelledError()

            @tool
            async def memory_search(query: str, max_results: int = 5) -> str:
                """Recall this employee expert's authoritative company memory."""
                return json.dumps(
                    await client.request(
                        f"tasks/{task_id}/memory/search",
                        {"query": query, "max_results": max_results},
                    ),
                    ensure_ascii=False,
                )

            @tool
            async def memory_get(path: str) -> str:
                """Read a virtual memory path returned by company memory_search."""
                return json.dumps(
                    await client.request(f"tasks/{task_id}/memory/get", {"path": path}),
                    ensure_ascii=False,
                )

            @tool
            async def remember(assertion: str) -> str:
                """Persist a user-requested fact in company memory, never in project files."""
                # The journal prevents re-running the tool call after a completed response.
                operation = hashlib.sha256(assertion.encode()).hexdigest()
                return json.dumps(
                    await client.request(
                        f"tasks/{task_id}/memory/remember",
                        {"assertion": assertion, "operation_id": operation},
                    ),
                    ensure_ascii=False,
                )

            @tool
            async def search_knowledge(query: str, k: int = 8) -> str:
                """Search selected company knowledge bases using employee permissions."""
                return str(
                    (await client.request(f"tasks/{task_id}/knowledge", {"query": query, "k": k}))[
                        "content"
                    ]
                )

            @tool
            async def company_generate_image(
                prompt: str,
                filename: str = "cover.png",
                size: str | None = None,
                reference_images: list[str] | None = None,
            ) -> str:
                """Generate or edit an image through the company service.

                For editing, ALWAYS pass original project image paths in reference_images;
                describing an image in prompt alone generates a new image. Preserve the
                original framing unless asked otherwise. Save to a new filename.
                size is WIDTHxHEIGHT in pixels, with both dimensions divisible by 16
                (for example 1536x864 for 16:9), not an aspect-ratio string.
                """
                target = (project / filename).resolve()
                if not target.is_relative_to(project) or target == project or target.exists():
                    raise ValueError("output_path_conflict")
                try:
                    references = await offload(
                        image_reference_inputs, project, reference_images or []
                    )
                except (OSError, ValueError) as exc:
                    raise ToolException(str(exc)) from exc
                body: dict[str, Any] = {"prompt": prompt, "size": size}
                if references:
                    body["reference_images"] = references
                operation = hashlib.sha256(
                    json.dumps({**body, "filename": filename}, sort_keys=True).encode()
                ).hexdigest()
                try:
                    result = await client.request(
                        f"tasks/{task_id}/images",
                        {"operation_id": operation, **body},
                    )
                except CompanyServiceError as exc:
                    if not str(exc).startswith("company_image_"):
                        raise
                    # A confirmed provider failure is a tool result, not an
                    # interrupted graph with a dangling tool call on the next turn.
                    raise ToolException(
                        f"{exc}. Do not retry or generate another image automatically. "
                        "Explain this failure to the user and wait for their instruction."
                    ) from exc
                item = result["artifacts"][0]
                response = await client.http.get(
                    f"tasks/{task_id}/images/{operation}/{item['file_id']}"
                )
                response.raise_for_status()
                data = response.content
                if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise RuntimeError("generated_image_checksum_mismatch")

                def save_image() -> None:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())

                await offload(save_image)
                return json.dumps(
                    {"path": str(target), "sha256": item["sha256"], "location": "desktop"}
                )

            company_generate_image.handle_tool_error = True

            instructions = (
                snapshot["instructions"]
                + f"\nEnterprise execution: project={project}; selected source folder={self.workspace}; Python={sys.executable}. Shell commands run with the current employee OS permissions. Preserve source files. Never overwrite or delete without user authorization. Do not upload raw media for local processing. This project has no personal memory store. Use memory_search/memory_get/remember for ALL personal or durable memory. Never initialize SOUL.md, USER.md, MEMORY.md, AGENTS.md or a memory database locally. Company expert skills are cached at {cache}. Only this task's granted company tools are available. Team delegation and external connectors are unavailable."
            )

            @tool
            async def load_company_skill(name: str) -> str:
                """Load a granted company skill and its pinned scripts into the local cache before following it."""
                entries = [
                    item
                    for item in snapshot["skills"]
                    if item["path"].startswith(f"skills/{name}/")
                ]
                if not entries:
                    raise ValueError("skill_not_granted")
                for item in entries:
                    relative = PurePosixPath(item["path"])
                    destination = cache.joinpath(*relative.parts)
                    if (
                        relative.is_absolute()
                        or ".." in relative.parts
                        or not destination.resolve().is_relative_to(cache)
                    ):
                        raise ValueError("invalid_skill_path")
                    if destination.exists():
                        if (
                            hashlib.sha256(await offload(destination.read_bytes)).hexdigest()
                            != item["sha256"]
                        ):
                            raise ValueError("skill_checksum_mismatch")
                        continue
                    response = await client.http.get(f"tasks/{task_id}/skills/{item['sha256']}")
                    response.raise_for_status()
                    data = response.content
                    if (
                        len(data) != item["size"]
                        or hashlib.sha256(data).hexdigest() != item["sha256"]
                    ):
                        raise ValueError("skill_checksum_mismatch")
                    await offload(destination.parent.mkdir, parents=True, exist_ok=True)

                    def save_asset(path: Path, content: bytes) -> None:
                        with path.open("xb") as stream:
                            stream.write(content)

                    await offload(save_asset, destination, data)
                manifest = cache / "skills" / name / "SKILL.md"
                return (
                    f"Native skill directory: {manifest.parent}\nRead-only virtual path: /company-skills/skills/{name}/\n"
                    + str(await offload(manifest.read_text, encoding="utf-8"))
                )

            instructions += "\nAvailable company skills (call load_company_skill to read instructions and download pinned dependencies before use):\n"
            for item in snapshot["skills"]:
                if item["path"].endswith("/SKILL.md"):
                    instructions += f"- {PurePosixPath(item['path']).parent.name}: {item.get('description', '')}\n"
            factory = CompanyModelFactory(client, task_id, snapshot["models"])
            config = HarnessAgentConfig(
                name=snapshot["name"],
                workspace_dir=project,
                backend=CompositeBackend(
                    default=backend,
                    routes={
                        "/.runtime/": FilesystemBackend(
                            root_dir=self.state / "threads" / thread / "context-cache",
                            virtual_mode=True,
                        ),
                        "/company-skills/": FilesystemBackend(root_dir=cache, virtual_mode=True),
                    },
                    artifacts_root="/.runtime/",
                ),
                skills_dir=["/company-skills/skills"],
                providers=factory.provider_configs(),
                default_model=request.get("model") or snapshot["default_model"],
                checkpointer=saver,
                memory=[],
                memory_enabled=False,
                session_log_enabled=False,
                bootstrap_enabled=False,
                subagents_auto_load=False,
                subagents=[],
                web_search_tools=False,
                ask_user_enabled=False,
                model_retry_enabled=False,
                media_offload_enabled=False,
                tools_disabled=frozenset(
                    {
                        "task",
                        "browser_use",
                        "web_fetch",
                        "desktop_screenshot",
                        "env_file",
                        "env_file_read",
                        "env_file_write",
                        "send_file_to_user",
                    }
                ),
                tools=[
                    memory_search,
                    memory_get,
                    remember,
                    search_knowledge,
                    company_generate_image,
                    load_company_skill,
                ],
                middleware=[
                    ModelRetryMiddleware(
                        max_retries=3,
                        initial_delay=1.0,
                        max_delay=10.0,
                        retry_on=lambda exc: isinstance(exc, CompanyModelError) and exc.retryable,
                        on_failure="error",
                    ),
                    ReceiptMiddleware(
                        OperationJournal(self.state / "threads" / thread / "operations"),
                        saver,
                        authorize,
                    ),
                ],
                system_prompt=instructions,
            )
            agent = await offload(
                HarnessAgent, config, model_factory=factory, agent_id=task["agent_id"]
            )
            message = HumanMessage(
                content=request["text"],
                id=task_id + "_user",
                additional_kwargs={INBOUND_ATTACHMENTS_KEY: request["inbound_attachments"]}
                if request.get("inbound_attachments")
                else {},
            )
            await self.event(
                task, directory, {"kind": "messages", "messages": messages_to_dict([message])}
            )

            async def watch() -> None:
                while True:
                    await asyncio.sleep(5)
                    try:
                        await authorize()
                    except asyncio.CancelledError:
                        running = self.work.get(thread)
                        if running:
                            running.cancel()
                        return
                    except CompanyServiceError as exc:
                        if exc.status in {401, 403, 404}:
                            running = self.work.get(thread)
                            if running:
                                running.cancel()
                            return
                    except Exception:
                        # A process already started may finish offline; the next step is gated.
                        continue

            watcher = asyncio.create_task(watch())

            async def persist_steer(steer_message: HumanMessage) -> None:
                # Commit the preceding segment before inserting its next user row.
                await self.event(
                    task,
                    directory,
                    {
                        "kind": "messages",
                        "messages": messages_to_dict(await agent.aget_history(thread)),
                    },
                )
                await self.event(
                    task,
                    directory,
                    {"kind": "messages", "messages": messages_to_dict([steer_message])},
                )

            from octop.infra.agents.settings.runtime_limits import (
                apply_agent_runtime_to_stream_request,
            )

            stream_options = apply_agent_runtime_to_stream_request(
                {"conversation_mode": request["conversation_mode"]}
                if request.get("conversation_mode")
                else {},
                snapshot.get("runtime_settings", {}),
            )
            async for chunk in self.steering.stream(
                agent, thread, message, persist_steer, options=stream_options
            ):
                # Harness native protocol state snapshots contain serializable message dicts.
                self.emit({"event": "chunk", "thread_id": thread, "data": chunk})
            messages = await agent.aget_history(thread)
            await self.event(
                task, directory, {"kind": "messages", "messages": messages_to_dict(messages)}
            )
            artifacts = await offload(artifact_index, project, previous=before)
            current_messages = []
            for item in reversed(messages):
                if item.id == message.id:
                    break
                current_messages.append(item)
            assistant_id = next(
                (item.id for item in current_messages if item.type == "ai" and item.id), None
            )
            await self.event(
                task,
                directory,
                {
                    "kind": "artifacts",
                    "location": "desktop",
                    "scope": "turn",
                    "user_message_id": message.id,
                    "message_id": assistant_id,
                    "files": artifacts,
                },
            )
        except asyncio.CancelledError:
            status = "cancelled"
        except Exception as exc:
            status, error = "failed", str(exc)
            if "unknown" in error or isinstance(exc, httpx.TransportError):
                status = "unknown"
        finally:
            if agent and status != "completed":
                # Failed/cancelled turns still have useful checkpointed messages.
                # Do not postpone them until a later successful turn's receipt.
                # event() leaves a durable pending receipt for reconnection;
                # keep the original failure if the company is unreachable.
                with contextlib.suppress(Exception):
                    await self.event(
                        task,
                        directory,
                        {
                            "kind": "messages",
                            "messages": messages_to_dict(await agent.aget_history(thread)),
                        },
                    )
            self.steering.end(thread, failed=status in {"failed", "unknown"})
            if watcher:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            if backend:
                await backend.shutdown()
            if agent:
                await agent.aclose()
            if saver:
                await saver.close()
            task["reported_status"] = status
            await offload(atomic_json, directory / "task.json", task)
            try:
                async with asyncio.timeout(5):
                    await flush_checkpoints(client, task_id, directory)
                    await self.flush_events(task, directory)
                    await client.request(f"tasks/{task_id}/status", {"status": status})
            except Exception:
                task["sync_state"] = "pending"
            else:
                task["sync_state"] = "synced"
            task["status"] = status
            await offload(atomic_json, directory / "task.json", task)
            self.work.pop(thread, None)
            self.emit(
                {
                    "event": "done",
                    "thread_id": thread,
                    "status": status,
                    "sync_state": task["sync_state"],
                    "error": error,
                    "artifacts": artifacts,
                }
            )
            if task["sync_state"] == "pending":
                self.sync_work[task_id] = asyncio.create_task(self.retry_sync(task, directory))

    async def close(self) -> None:
        for work in self.work.values():
            work.cancel()
        await asyncio.gather(*list(self.work.values()), return_exceptions=True)
        for work in self.sync_work.values():
            work.cancel()
        await asyncio.gather(*list(self.sync_work.values()), return_exceptions=True)
        if self.client:
            await self.client.close()
