"""Read fixed context files from an isolated volume without running a container."""

from __future__ import annotations

import io
import tarfile
from pathlib import PurePosixPath
from typing import Any

from deepagents.backends.protocol import BackendProtocol, ReadResult
from octop_harness.backends.workspace import BackendWorkspace

CONTEXT_FILES = frozenset({"AGENTS.md", "SOUL.md", "USER.md", "MEMORY.md"})
_LIMIT = 262144


class _ContextBackend(BackendProtocol):
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def read(self, file_path: str, offset: int = 0, limit: int = _LIMIT) -> ReadResult:
        key = str(PurePosixPath(file_path))
        if key.startswith("/context/"):
            key = key.removeprefix("/context/")
        else:
            key = key.removeprefix("/")
        content = self.files.get(key)
        if content is None:
            return ReadResult(error="file_not_found")
        return ReadResult(
            file_data={"content": content[offset : offset + limit], "encoding": "utf-8"}
        )


class _ContextWorkspace(BackendWorkspace):
    def materialize_local(self, path: str, *, map_system_paths: bool = True) -> None:
        # Never consult host files or delivery caches for a volume snapshot.
        return None


def isolated_context_workspace(spec: dict[str, Any]) -> BackendWorkspace:
    """Mount only this row's existing volume read-only; never start/exec the reader.

    Docker's archive API reads stopped containers. Only four fixed regular text
    files are accepted, with bounded archives and no symlink following/extraction.
    New metadata-only instances have no volume and therefore no personal context.
    """
    from docker.client import DockerClient
    from docker.errors import NotFound

    client = DockerClient.from_env(timeout=15)
    container = None
    files: dict[str, str] = {}
    try:
        try:
            volume = client.volumes.get(spec["volume"])
        except NotFound:
            return _ContextWorkspace(_ContextBackend(files), "/context")
        if volume.attrs.get("Labels", {}).get("octop.agent.id") != spec[
            "agent_id"
        ] or volume.attrs.get("Options"):
            raise RuntimeError("refusing an unmanaged workspace volume")
        container = client.containers.create(
            image=spec["image"],
            command=["true"],
            entrypoint=[],
            user="1000:1000",
            read_only=True,
            network_mode="none",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            volumes={spec["volume"]: {"bind": "/context", "mode": "ro,nocopy"}},
            labels={"octop.context.reader": "1", "octop.agent.id": spec["agent_id"]},
        )
        for name in sorted(CONTEXT_FILES):
            try:
                stream, stat = container.get_archive("/context/" + name)
            except NotFound:
                continue
            try:
                if stat["size"] > _LIMIT:
                    raise ValueError("expert_context_too_large")
                data = bytearray()
                for chunk in stream:
                    data.extend(chunk)
                    if len(data) > _LIMIT + 20480:
                        raise ValueError("expert_context_too_large")
            finally:
                stream.close()
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                entries = archive.getmembers()
                if (
                    len(entries) != 1
                    or not entries[0].isreg()
                    or entries[0].name != name
                    or entries[0].size > _LIMIT
                ):
                    raise ValueError("expert_context_not_regular")
                item = archive.extractfile(entries[0])
                assert item is not None
                files[name] = item.read(_LIMIT).decode("utf-8")
        return _ContextWorkspace(_ContextBackend(files), "/context")
    finally:
        try:
            if container is not None:
                container.remove()
        finally:
            client.close()
