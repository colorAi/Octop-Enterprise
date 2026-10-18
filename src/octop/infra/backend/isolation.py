"""Operator-enforced agent containers. User backend settings cannot weaken this policy."""

from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import tarfile
from pathlib import Path
from typing import Any

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout

# These tools execute in the control-plane process rather than the workspace backend.
HOST_TOOLS = frozenset(
    {"browser_use", "desktop_screenshot", "acp_runner", "read_env_file", "write_env_file"}
)


def isolation_enabled() -> bool:
    return os.environ.get("OCTOP_AGENT_ISOLATION", "").strip().lower() == "docker"


def host_mcp(spec: Any) -> bool:
    return isinstance(spec, dict) and (
        spec.get("transport") == "stdio" or bool(spec.get("command"))
    )


def isolated_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Remove host-path and host-runner overrides from the effective config."""
    if not isolation_enabled():
        return cfg
    out = {
        key: value
        for key, value in cfg.items()
        if key not in {"skills", "acp", "system_files_path", "backend"}
    }
    if isinstance(out.get("memory"), dict):
        out["memory"] = {key: value for key, value in out["memory"].items() if key != "backend"}
        prior = cfg["memory"].get("backend")
        if isinstance(prior, dict) and prior.get("type") == "sqlite":
            # Preserve existing SQLite histories, but never accept a host path.
            out["memory"]["backend"] = {"type": "sqlite"}
        elif isinstance(prior, dict) and prior.get("type") == "postgres":
            out["memory"]["backend"] = {"type": "postgres", "use_control_plane_dsn": True}
    return out


def isolated_spec(
    paths: PathLayout, agent_id: str, workspace: Path, *, instance_key: str = ""
) -> dict[str, Any]:
    expected = paths.agents_dir / agent_id
    # Check before resolving so a symlinked workspace cannot select a sibling.
    if expected.is_symlink() or workspace.resolve() != expected.absolute():
        raise OctopError(
            ErrorCode.WORKSPACE_ROOT_RESTRICTED,
            "isolated agents require their own canonical workspace",
        )
    image = os.environ.get("OCTOP_SANDBOX_IMAGE", "").strip()
    if not image:
        raise OctopError(
            ErrorCode.WORKSPACE_OP_UNSUPPORTED, "isolated sandbox image must be configured"
        )
    return {
        "type": "docker",
        "_octop_isolated": True,
        "image": image,
        "agent_id": agent_id,
        "workspace_path": str(expected.absolute()),
        # The database row identity prevents a deleted custom agent ID from
        # reopening a previous owner's volume when that ID is reused.
        "volume": "octop_workspace_"
        + hashlib.sha256(f"{expected.absolute()}:{instance_key}".encode()).hexdigest()[:24],
    }


def create_isolated_backend(
    spec: dict[str, Any],
    *,
    package_mounts: dict[str, str] | None = None,
    managed_lifecycle: bool = False,
) -> Any:
    """Build an isolated backend; managed handles are borrowed by harness runtimes.

    Harness reloads close the old agent after constructing its replacement
    config. Cached workspaces and config middleware still share this handle,
    so only the owning Octop manager may release a managed backend.
    """
    from octop.infra.utils.harness_compat import require_supported_harness

    require_supported_harness()
    from octop_harness.backends.docker_sandbox import DockerSandbox

    class IsolatedDockerSandbox(DockerSandbox):
        def __init__(self, **kwargs: Any) -> None:
            self._octop_run_options: dict[str, Any] = options
            super().__init__(**kwargs)

        def close(self) -> None:
            if not managed_lifecycle:
                super().close()

        def release(self) -> None:
            """Detach the manager-owned handle without deleting its private volume."""
            super().close()

        def _map_path(self, path: str) -> str:
            raw = posixpath.normpath(str(path).replace("\\", "/"))
            if any(
                raw == target or raw.startswith(target + "/")
                for target in (package_mounts or {}).values()
            ):
                return raw
            return super()._map_path(path)

        def _create_container(self) -> Any:
            # Content never shares a writable mount with control-plane runtime
            # files: a crafted symlink cannot trick host-side persistence into
            # opening another user's database or a platform credential.
            try:
                volume = self._client.volumes.get(spec["volume"])
            except self._docker.errors.NotFound:
                volume = self._client.volumes.create(
                    name=spec["volume"], labels={"octop.agent.id": spec["agent_id"]}
                )
            if volume.attrs.get("Labels", {}).get("octop.agent.id") != spec[
                "agent_id"
            ] or volume.attrs.get("Options"):
                raise RuntimeError("refusing an unmanaged workspace volume")
            self._client.containers.run(
                spec["image"],
                [
                    "python3",
                    "-c",
                    "import os,sys; os.chown(sys.argv[1],1000,1000)",
                    spec["workspace_path"],
                ],
                entrypoint=[],
                remove=True,
                network_mode="none",
                read_only=True,
                cap_drop=["ALL"],
                cap_add=["CHOWN"],
                security_opt=["no-new-privileges:true"],
                volumes={spec["volume"]: {"bind": spec["workspace_path"], "mode": "rw,nocopy"}},
                user="0:0",
                pids_limit=16,
                mem_limit="128m",
            )
            try:
                return self._client.containers.run(**self._octop_run_options)
            except self._docker.errors.APIError as exc:
                # Workspace reads and runtime startup can acquire the same
                # persistent container concurrently.
                if exc.status_code != 409:
                    raise
                existing = self._get_named_container(name)
                if existing is None or not self._ensure_running(existing):
                    raise
                return existing

        def _put_file(self, container_path: str, data: bytes) -> None:
            parent, name = str(Path(container_path).parent), Path(container_path).name
            result = self._container.exec_run(["mkdir", "-p", parent])
            if result.exit_code:
                raise OSError("cannot create sandbox directory")
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as archive:
                info = tarfile.TarInfo(name)
                info.size, info.uid, info.gid, info.mode = len(data), 1000, 1000, 0o600
                archive.addfile(info, io.BytesIO(data))
            if not self._container.put_archive(parent, buf.getvalue()):
                raise OSError("cannot write sandbox file")

        def _get_named_container(self, name: str) -> Any:
            existing = super()._get_named_container(name)
            if existing is None:
                return None
            existing.reload()
            actual = existing.attrs
            host = actual.get("HostConfig", {})
            config = actual.get("Config", {})
            if (
                config.get("Labels", {}).get("octop.isolation.policy") != fingerprint
                or host.get("Privileged")
                or not host.get("ReadonlyRootfs")
                or host.get("NetworkMode") != "none"
                or config.get("User") != "1000:1000"
                or "ALL" not in host.get("CapDrop", [])
                or "no-new-privileges:true" not in host.get("SecurityOpt", [])
                or sorted(host.get("Binds") or [])
                != sorted(
                    f"{source}:{mount['bind']}:{mount['mode']}" for source, mount in mounts.items()
                )
            ):
                raise RuntimeError(
                    "refusing an existing container with a different isolation policy"
                )
            return existing

        def _resolve_execute_env(self) -> dict[str, str]:
            # Workspace .env is private to this agent; never read the global env file.
            return {**self._workspace_dotenv(), "HOME": "/tmp", "OCTOP_AGENT_ID": spec["agent_id"]}

    mounts = {spec["volume"]: {"bind": spec["workspace_path"], "mode": "rw,nocopy"}}
    for host, target in (package_mounts or {}).items():
        mounts[host] = {"bind": target, "mode": "ro"}
    policy = {"version": 2, "image": spec["image"], "mounts": mounts, "agent": spec["agent_id"]}
    fingerprint = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
    name = f"octop_private_{fingerprint[:24]}"
    options = {
        "image": spec["image"],
        "command": ["sleep", "infinity"],
        "entrypoint": [],
        "name": name,
        "detach": True,
        "working_dir": spec["workspace_path"],
        "user": "1000:1000",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "network_mode": "none",
        "pids_limit": 128,
        "mem_limit": "1g",
        "nano_cpus": 1_000_000_000,
        "tmpfs": {"/tmp": "rw,nosuid,nodev,size=268435456,mode=1777"},
        "volumes": mounts,
        "environment": {"HOME": "/tmp"},
        # The application image's HTTP health check does not apply to a worker.
        "healthcheck": {"test": ["NONE"]},
        "labels": {"octop.isolation.policy": fingerprint, "octop.agent.id": spec["agent_id"]},
    }
    # __init__ acquires the container, so policy must exist before it runs.
    backend = IsolatedDockerSandbox(
        image=spec["image"],
        workspace_dir=Path(spec["workspace_path"]),
        workspace_path=spec["workspace_path"],
        system_files_path=".octop",
        container_name=name,
        agent_id=spec["agent_id"],
        auto_remove=False,
        allow_network=False,
        environment={},
    )
    return backend
