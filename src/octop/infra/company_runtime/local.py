"""Local operation receipts and supervised processes. CWD is not an OS sandbox."""

from __future__ import annotations

import asyncio
import base64
import codecs
import contextlib
import hashlib
import json
import mimetypes
import ntpath
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from deepagents.backends.protocol import ExecuteResponse
from langchain.agents.middleware import AgentMiddleware
from octop_harness.backends.local_shell import HarnessLocalShellBackend

from octop.infra.utils.asyncio import offload

_TOOL_CALL_ID: ContextVar[str] = ContextVar("company_tool_call_id", default="")


def local_attachment_metadata(project: Path, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep verified imported originals in user history without uploading their bytes."""
    if len(files) > 50:
        raise ValueError("too_many_local_attachments")
    metadata = []
    for file in files:
        path = Path(file["path"]).resolve(strict=True)
        if not path.is_relative_to(project.resolve()) or not path.is_file():
            raise ValueError("local_attachment_outside_project")
        size = path.stat().st_size
        if not 0 < size <= 50 * 1024 * 1024 or size != file["size"]:
            raise ValueError("local_attachment_size_changed")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != file["sha256"]:
            raise ValueError("local_attachment_checksum_mismatch")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        kind = mime.split("/", 1)[0]
        metadata.append(
            {
                "workspace_path": str(path),
                "filename": path.name,
                "media_type": mime,
                "kind": kind if kind in {"image", "audio", "video"} else "file",
                "execution_location": "desktop",
                "size": size,
                "sha256": digest,
            }
        )
    return metadata


def image_reference_inputs(project: Path, references: list[str]) -> list[str]:
    """Upload only explicitly selected project images to the image-editing tool."""
    if len(references) > 4:
        raise ValueError("At most four reference images are supported")
    encoded = []
    total = 0
    for reference in references:
        path = (project / reference).resolve(strict=True)
        if not path.is_relative_to(project.resolve()) or not path.is_file():
            raise ValueError("Reference image must be inside this project")
        mime = mimetypes.guess_type(path.name)[0]
        if mime not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("Reference images must be PNG, JPEG or WebP")
        with path.open("rb") as source:
            data = source.read(10 * 1024 * 1024 + 1)
        total += len(data)
        if not data or total > 10 * 1024 * 1024:
            raise ValueError("Reference images must total at most 10 MB")
        encoded.append(f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}")
    return encoded


def windows_python_command(command: str) -> list[str] | None:
    """Keep a quoted Python -c program out of cmd's second quoting/AutoRun pass."""
    match = re.fullmatch(r'\s*"([^"\r\n]+)"\s+-c\s+"([^"\r\n]*)"\s*', command)
    if not match:
        return None
    executable, code = match.groups()
    if not ntpath.isabs(executable) or ntpath.basename(executable).lower() not in {
        "python.exe",
        "python3.exe",
    }:
        return None
    return [executable, "-X", "utf8", "-u", "-c", code]


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as out:
        os.chmod(temporary, 0o600)
        json.dump(value, out, ensure_ascii=False)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)


class OperationJournal:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, operation: str) -> Path:
        return self.root / (hashlib.sha256(operation.encode()).hexdigest() + ".json")

    def get(self, operation: str) -> dict[str, Any] | None:
        path = self.path(operation)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def save(self, operation: str, **data: Any) -> None:
        atomic_json(
            self.path(operation), {"operation_id": operation, "updated_at": time.time(), **data}
        )


class ReceiptMiddleware(AgentMiddleware):
    ALLOWED = frozenset(
        {
            "execute",
            "ls",
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "grep",
            "write_todos",
            "current_time",
            "memory_search",
            "memory_get",
            "remember",
            "search_knowledge",
            "company_generate_image",
            "company_generate_video",
            "company_synthesize_speech",
            "company_transcribe_audio",
            "company_web_search",
            "send_local_file",
            "load_company_skill",
        }
    )

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        tools = [
            tool
            for tool in request.tools
            if (tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", ""))
            in self.ALLOWED
        ]
        return await handler(request.override(tools=tools))

    def __init__(
        self, journal: OperationJournal, saver: Any, authorize: Callable[..., Any]
    ) -> None:
        self.journal, self.saver, self.authorize = journal, saver, authorize
        self.locks: dict[str, asyncio.Lock] = {}

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        call = request.tool_call
        if call["name"] in {"write_file", "edit_file"} and str(
            call.get("args", {}).get("file_path", "")
        ).startswith("/company-skills/"):
            raise RuntimeError("company_skill_cache_read_only")
        if call["name"] not in self.ALLOWED:
            raise RuntimeError("company_tool_not_granted")
        key = str(call["id"])
        digest = hashlib.sha256(
            json.dumps(call, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        async with self.locks.setdefault(key, asyncio.Lock()):
            old = await offload(self.journal.get, key)
            if old:
                if old["request_hash"] != digest:
                    raise RuntimeError("operation_identity_conflict")
                if old["status"] == "completed":
                    return self.saver.unpack(old["result"])
                raise RuntimeError(
                    "operation_result_unknown: inspect the existing output before retry"
                )
            await self.authorize()
            await offload(
                self.journal.save, key, request_hash=digest, status="running", tool=call["name"]
            )
            token = _TOOL_CALL_ID.set(key)
            try:
                result = await handler(request)
            except BaseException:
                await offload(
                    self.journal.save, key, request_hash=digest, status="unknown", tool=call["name"]
                )
                raise
            finally:
                _TOOL_CALL_ID.reset(token)
            await offload(
                self.journal.save,
                key,
                request_hash=digest,
                status="completed",
                tool=call["name"],
                result=self.saver.pack(result),
            )
            return result


class ProjectBackend(HarnessLocalShellBackend):
    def __init__(
        self, project: Path, state: Path, emit: Callable[..., None], *, tool_path: str
    ) -> None:
        super().__init__(
            root_dir=project,
            workspace_dir=project,
            virtual_mode=True,
            inherit_env=False,
            timeout=900,
        )
        self.project, self.state, self.emit = project, state, emit
        self.tool_path = tool_path
        self.packages = os.environ.get("OCTOP_COMPANY_PACKAGES", "")
        self.processes: set[asyncio.subprocess.Process] = set()

    def _resolve_path(self, key: str) -> Path:
        # File tools accept both project-relative paths and the native absolute
        # paths shown in shell output; preserve virtual-root escape checks.
        native = Path(key)
        if native.is_absolute() and native.is_relative_to(self.project):
            key = native.relative_to(self.project).as_posix()
        return super()._resolve_path(key)

    def artifact_metadata(self, file_path: str) -> dict[str, Any]:
        """Describe an existing project file for the native preview/reveal bridge."""
        target = self._resolve_path(file_path)
        if not target.is_file():
            raise ValueError("local_file_not_found")
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            size = os.fstat(stream.fileno()).st_size
        return {"path": str(target), "size": size, "sha256": digest}

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise RuntimeError("Synchronous execution is disabled; use asynchronous runtime")

    async def stop_process(self, process: asyncio.subprocess.Process) -> None:
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        await process.wait()

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        effective_timeout = min(timeout if timeout is not None else self._default_timeout, 3600)
        if effective_timeout <= 0:
            return ExecuteResponse(output="timeout must be positive", exit_code=1)
        # No provider keys, company tokens or inherited cloud credentials reach children.
        allowed = (
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
            "HOME",
            "USERPROFILE",
        )
        env = {key: os.environ[key] for key in allowed if key in os.environ}
        env.update(
            PATH=self.tool_path,
            PYTHONNOUSERSITE="1",
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONUTF8="1",
            PYTHONIOENCODING="utf-8",
        )
        if self.packages:
            env["PYTHONPATH"] = self.packages
        kwargs: dict[str, Any] = {
            "cwd": self.project,
            "env": env,
            # The worker stdin carries private JSONL requests. Tools must never
            # read that channel or wait for interactive input on it.
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )
        else:
            kwargs["start_new_session"] = True
        python_args = windows_python_command(command) if os.name == "nt" else None
        if python_args:
            process = await asyncio.create_subprocess_exec(*python_args, **kwargs)
        else:
            process = await asyncio.create_subprocess_shell(command, **kwargs)
        self.processes.add(process)
        process_dir = self.state / "processes" / str(process.pid)
        tails: dict[str, bytes] = {}
        truncated = False
        readers: asyncio.Future[Any] | None = None

        async def drain(name: str, stream: Any) -> None:
            nonlocal truncated
            tail = b""
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            preview = ""
            emitted_at = 0.0

            def emit_preview() -> None:
                nonlocal preview, emitted_at
                if preview:
                    self.emit(
                        {
                            "event": "process_output",
                            "pid": process.pid,
                            "stream": name,
                            "tool_call_id": _TOOL_CALL_ID.get(),
                            "content": preview,
                        }
                    )
                    preview = ""
                    emitted_at = time.monotonic()

            with (process_dir / (name + ".log")).open("wb") as out:
                logged = 0
                while chunk := await stream.read(16384):
                    # Disk logs are bounded too; keep draining to avoid deadlock.
                    if logged < 8 * 1024 * 1024:
                        out.write(chunk)
                        logged += len(chunk)
                    tail += chunk
                    if len(tail) > 131072:
                        tail = tail[-131072:]
                        truncated = True
                    tails[name] = tail
                    preview = (preview + decoder.decode(chunk))[-16384:]
                    if time.monotonic() - emitted_at >= 0.15:
                        emit_preview()
                tails[name] = tail
                preview = (preview + decoder.decode(b"", final=True))[-16384:]
                emit_preview()

        status = "completed"

        async def wait_output() -> None:
            await process.wait()
            assert readers is not None
            await asyncio.shield(readers)

        try:
            await offload(process_dir.mkdir, parents=True, exist_ok=True)
            self.emit(
                {
                    "event": "process",
                    "pid": process.pid,
                    "status": "running",
                    "cwd": str(self.project),
                }
            )
            readers = asyncio.gather(
                drain("stdout", process.stdout), drain("stderr", process.stderr)
            )
            await asyncio.wait_for(wait_output(), timeout=effective_timeout)
            if process.returncode:
                status = "failed"
            return ExecuteResponse(
                output="stdout:\n"
                + tails["stdout"].decode("utf-8", "replace")
                + "\nstderr:\n"
                + tails["stderr"].decode("utf-8", "replace"),
                exit_code=process.returncode,
                truncated=truncated,
            )
        except BaseException as exc:
            status = (
                "timed_out"
                if isinstance(exc, TimeoutError)
                else "cancelled"
                if isinstance(exc, asyncio.CancelledError)
                else "failed"
            )
            cleanup_failed = False
            try:
                await asyncio.wait_for(self.stop_process(process), timeout=5)
            except Exception:
                cleanup_failed = True
                status = "unknown"
            if readers is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(readers), timeout=1)
                except TimeoutError:
                    # A descendant can retain the pipe even after cmd has exited.
                    # Closing a tool must not wait indefinitely for that EOF.
                    readers.cancel()
                    await asyncio.gather(readers, return_exceptions=True)
            if isinstance(exc, TimeoutError):
                return ExecuteResponse(
                    output="stdout:\n"
                    + tails.get("stdout", b"").decode("utf-8", "replace")
                    + "\nstderr:\n"
                    + tails.get("stderr", b"").decode("utf-8", "replace")
                    + f"\nprocess timed out after {effective_timeout} seconds; inspect output files before retry"
                    + ("; process termination could not be confirmed" if cleanup_failed else ""),
                    exit_code=124,
                    truncated=truncated,
                )
            raise
        finally:
            # Kill descendants even if a shell daemonized them and exited first.
            if os.name != "nt":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            self.processes.discard(process)
            await offload(
                atomic_json,
                process_dir / "status.json",
                {
                    "pid": process.pid,
                    "status": status,
                    "exit_code": process.returncode,
                    "cwd": str(self.project),
                },
            )
            self.emit(
                {
                    "event": "process",
                    "pid": process.pid,
                    "status": status,
                    "exit_code": process.returncode,
                }
            )

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(asyncio.wait_for(self.stop_process(p), timeout=5) for p in list(self.processes)),
            return_exceptions=True,
        )


def artifact_index(
    project: Path,
    *,
    previous: list[dict[str, Any]] | None = None,
    limit: int | None = 1000,
) -> list[dict[str, Any]]:
    """Index only new/changed bytes when a pre-turn snapshot is supplied."""
    known = {item["path"]: item["sha256"] for item in previous or []}
    outputs = []
    for path in sorted(project.rglob("*")):
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(project):
            continue
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if known.get(str(path)) == digest:
            continue
        outputs.append({"path": str(path), "size": path.stat().st_size, "sha256": digest})
        if limit is not None and len(outputs) >= limit:
            break
    return outputs
