"""Operator-enforced agent containers. User backend settings cannot weaken this policy."""

from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import tarfile
import threading
from pathlib import Path
from typing import Any

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout

# These tools execute in the control-plane process rather than the workspace backend.
HOST_TOOLS = frozenset(
    {"browser_use", "desktop_screenshot", "acp_runner", "read_env_file", "write_env_file"}
)

_execution_tracking: tuple[Any, str, str] | None = None
# Workspace reads and runtime startup can create separate handles concurrently.
# Bound lock storage independently of the number of users/agents.
_container_locks = tuple(threading.RLock() for _ in range(64))


def configure_execution_tracking(repo: Any, node: str, incarnation: str) -> None:
    global _execution_tracking
    _execution_tracking = (repo, node, incarnation)


def clear_execution_tracking() -> None:
    global _execution_tracking
    _execution_tracking = None


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

    from octop.infra.db.repos.execution import ExecutionConflict

    tracking = _execution_tracking

    def stopped(container: Any) -> bool:
        container.reload()
        state = container.attrs.get("State", {})
        return all(state.get(key) is False for key in ("Running", "Paused", "Restarting"))

    class IsolatedDockerSandbox(DockerSandbox):
        def __init__(self, **kwargs: Any) -> None:
            self._octop_run_options: dict[str, Any] = options
            self._budget_token: str | None = None
            try:
                super().__init__(**kwargs)
            except BaseException:
                # Never infer that a timed-out Docker start did not happen.
                if hasattr(self, "_client"):
                    self._client.close()
                raise

        def close(self) -> None:
            if not managed_lifecycle:
                super().close()

        def release(self) -> None:
            """Release RAM when the manager drops its last handle; retain the volume."""
            self.hibernate()

        def hibernate(self, *, idle: bool = False) -> None:
            """Stop the private container before dropping its handle; keep the volume."""
            with self._lock, container_lock:
                if self._closed:
                    return
                # A completed read-only view has no task to drain. Its PID 1
                # may ignore SIGTERM; do not add ten seconds to every source read.
                self._container.stop(timeout=0 if idle or spec.get("read_only_workspace") else 10)
                if not stopped(self._container):
                    raise RuntimeError("sandbox stop unconfirmed")
                helper = self._init_container()
                if helper is not None and not stopped(helper):
                    raise RuntimeError("sandbox initialization stop unconfirmed")
                if tracking is not None and self._budget_token is not None:
                    repo, node, incarnation = tracking
                    repo.release_sandbox(node, incarnation, name, self._budget_token)
                super().close()
                self._client.close()

        def _reserve(self) -> None:
            if self._closed:
                raise RuntimeError("sandbox handle is closed")
            if tracking is not None:
                repo, node, incarnation = tracking
                self._budget_token = repo.reserve_sandbox(
                    node, incarnation, spec["agent_id"], name, memory
                )
                if self._budget_token is None:
                    raise ExecutionConflict("sandbox_memory_exhausted")

        def _track(self, container: Any) -> None:
            if tracking is not None:
                repo, node, incarnation = tracking
                repo.track_workspace(node, incarnation, spec["agent_id"], container.id)

        def _acquire_container(self) -> Any:
            # Override harness orphan/recreate fallback: an uncertain start must
            # never create another container against the same reservation.
            with self._lock, container_lock:
                self._reserve()
                existing = self._get_named_container(name)
                self._reused = existing is not None
                if existing is not None:
                    self._ensure_running(existing)
                    return existing
                return self._create_container()

        def _ensure_running(self, container: Any) -> bool:
            with self._lock, container_lock:
                self._reserve()
                container.reload()
                self._track(container)
                state = container.attrs.get("State", {})
                if state.get("Running") is not True:
                    container.start()
                    container.reload()
                    state = container.attrs.get("State", {})
                if not state.get("Running") or state.get("Paused") or state.get("Restarting"):
                    raise RuntimeError("sandbox start unconfirmed")
                return True

        def _init_container(self) -> Any:
            try:
                helper = self._client.containers.get(name + "_init")
            except self._docker.errors.NotFound:
                return None
            helper.reload()
            labels = helper.attrs.get("Config", {}).get("Labels", {}) or {}
            if any(labels.get(key) != value for key, value in init_options["labels"].items()):
                raise RuntimeError("refusing an unmanaged sandbox initializer")
            return helper

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
            # The initializer and private container share one reservation and
            # never run together. Keep its stopped identity for crash recovery.
            helper = self._init_container()
            if helper is None:
                helper = self._client.containers.create(**init_options)
            self._track(helper)
            helper.reload()
            if helper.status == "created":
                helper.start()
            result = helper.wait(timeout=30)
            if result.get("StatusCode") != 0 or not stopped(helper):
                raise RuntimeError("sandbox initialization failed")
            try:
                container = self._client.containers.create(**self._octop_run_options)
            except self._docker.errors.APIError as exc:
                # Workspace reads and runtime startup can acquire the same
                # persistent container concurrently.
                if exc.status_code != 409:
                    raise
                existing = self._get_named_container(name)
                if existing is None or not self._ensure_running(existing):
                    raise
                return existing
            self._ensure_running(container)
            return container

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
                or host.get("Memory") != memory
                or host.get("MemorySwap") != memory
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

    mode = "ro,nocopy" if spec.get("read_only_workspace") else "rw,nocopy"
    mounts = {spec["volume"]: {"bind": spec["workspace_path"], "mode": mode}}
    for host, target in (package_mounts or {}).items():
        mounts[host] = {"bind": target, "mode": "ro"}
    from octop.infra.execution.profiles import memory_bytes

    memory = (
        int(tracking[0].sandbox_budget()["sandbox_bytes"])
        if tracking is not None
        else memory_bytes(os.environ.get("OCTOP_SANDBOX_MEMORY", "1g"))
    )
    if memory < 6 * 1024**2:
        raise ExecutionConflict("sandbox_budget_uninitialized")
    policy = {
        "version": 3,
        "image": spec["image"],
        "mounts": mounts,
        "agent": spec["agent_id"],
        "memory": memory,
    }
    execution_labels = {}
    if tracking is not None:
        _, node, incarnation = tracking
        execution_labels = {"org.octop.node": node, "org.octop.incarnation": incarnation}
        policy["execution"] = execution_labels
    fingerprint = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
    name = f"octop_private_{fingerprint[:24]}"
    container_lock = _container_locks[int(fingerprint[:8], 16) % len(_container_locks)]
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
        "mem_limit": memory,
        "memswap_limit": memory,
        "nano_cpus": 1_000_000_000,
        "tmpfs": {"/tmp": "rw,nosuid,nodev,size=268435456,mode=1777"},
        "volumes": mounts,
        "environment": {"HOME": "/tmp"},
        # The application image's HTTP health check does not apply to a worker.
        "healthcheck": {"test": ["NONE"]},
        "labels": {
            "octop.isolation.policy": fingerprint,
            "octop.agent.id": spec["agent_id"],
            **execution_labels,
        },
    }
    init_memory = min(memory, 128 * 1024**2)
    init_options = {
        **options,
        "name": name + "_init",
        "command": [
            "python3",
            "-c",
            "import os,sys; os.chown(sys.argv[1],1000,1000)",
            spec["workspace_path"],
        ],
        "user": "0:0",
        "cap_add": ["CHOWN"],
        "pids_limit": 16,
        "mem_limit": init_memory,
        "memswap_limit": init_memory,
        "volumes": {spec["volume"]: {"bind": spec["workspace_path"], "mode": "rw,nocopy"}},
        "labels": {**options["labels"], "octop.sandbox.init": "1"},
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
