"""Read-only Docker evidence before releasing a failed executor's workspace."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from octop.infra.db.repos.execution import ExecutionConflict, ExecutionRepo


def worker_identity(docker: Any, reference: str, node: str, incarnation: str) -> tuple[str, str]:
    """A worker container must be recreated with a fresh explicit boot nonce."""
    inspected = docker.api.inspect_container(reference)
    labels = inspected.get("Config", {}).get("Labels", {}) or {}
    if (
        not node
        or not incarnation
        or labels.get("org.octop.node") != node
        or labels.get("org.octop.incarnation") != incarnation
        or inspected.get("State", {}).get("Running") is not True
        or inspected.get("RestartCount", 0) != 0
        or inspected.get("HostConfig", {}).get("RestartPolicy", {}).get("Name")
        not in {"", "no", None}
    ):
        raise ExecutionConflict("worker_container_identity_invalid")
    started = inspected.get("State", {}).get("StartedAt", "")
    if not started or str(started).startswith("0001-"):
        raise ExecutionConflict("worker_container_identity_invalid")
    # Also distinguish two attempted Python launches inside the same container.
    return str(inspected["Id"]), ":".join(
        (incarnation, hashlib.sha256(started.encode()).hexdigest()[:16], uuid.uuid4().hex)
    )


def confirm_container_stopped(
    repo: ExecutionRepo, docker: Any, node: str, incarnation: str
) -> None:
    from docker.errors import NotFound

    process = repo.node(node)
    if not process or process["incarnation"] != incarnation or not process["container_id"]:
        raise ExecutionConflict("executor_stop_unconfirmed")
    # Do not infer a stopped process from a missing container, API error or timeout.
    inspected = docker.api.inspect_container(process["container_id"])
    labels = inspected.get("Config", {}).get("Labels", {}) or {}
    state = inspected.get("State", {})
    if (
        inspected.get("Id") != process["container_id"]
        or labels.get("org.octop.node") != node
        or labels.get("org.octop.incarnation") != incarnation.split(":", 1)[0]
        or (
            ":" in incarnation
            and incarnation.split(":")[1]
            != hashlib.sha256(str(state.get("StartedAt", "")).encode()).hexdigest()[:16]
        )
        or state.get("Running") is not False
        or state.get("Restarting") is not False
        or not state.get("FinishedAt")
        or str(state["FinishedAt"]).startswith("0001-")
    ):
        raise ExecutionConflict("executor_stop_unconfirmed")
    workspace_evidence = []
    # A reservation can precede creation of either child. Only after proving the
    # owner process stopped may an absent, never-tracked name count as unused.
    sandboxes = repo.sandboxes(node, incarnation)
    for reservation in sandboxes:
        for name in (reservation["container_name"], reservation["container_name"] + "_init"):
            try:
                child = docker.api.inspect_container(name)
            except NotFound:
                continue
            child_labels = child.get("Config", {}).get("Labels", {}) or {}
            child_state = child.get("State", {})
            if (
                child_labels.get("org.octop.node") != node
                or child_labels.get("org.octop.incarnation") != incarnation
                or child_labels.get("octop.agent.id") != reservation["agent_id"]
                or child_state.get("Running") is not False
                or child_state.get("Restarting") is not False
                or child_state.get("Paused") is True
            ):
                raise ExecutionConflict("workspace_stop_unconfirmed")
    workspaces = {item["container_id"]: item for item in repo.workspaces(node, incarnation)}
    # A process can die between Docker creation and the tracking INSERT. Labels
    # make those children discoverable without treating missing rows as proof.
    for child in docker.api.containers(
        all=True,
        filters={
            "label": [
                "org.octop.node=" + node,
                "org.octop.incarnation=" + incarnation,
                "octop.agent.id",
            ]
        },
    ):
        workspaces.setdefault(
            child["Id"],
            {"container_id": child["Id"], "agent_id": child["Labels"]["octop.agent.id"]},
        )
    for workspace in workspaces.values():
        sandbox = docker.api.inspect_container(workspace["container_id"])
        sandbox_state = sandbox.get("State", {})
        if (
            sandbox.get("Id") != workspace["container_id"]
            or sandbox.get("Config", {}).get("Labels", {}).get("octop.agent.id")
            != workspace["agent_id"]
            or sandbox_state.get("Running") is not False
            or sandbox_state.get("Restarting") is not False
            or sandbox_state.get("Paused") is True
            or (
                sandbox_state.get("Status") != "created"
                and (
                    not sandbox_state.get("FinishedAt")
                    or str(sandbox_state["FinishedAt"]).startswith("0001-")
                )
            )
        ):
            raise ExecutionConflict("workspace_stop_unconfirmed")
        workspace_evidence.append(
            {
                "container_id": sandbox["Id"],
                "finished_at": sandbox_state.get("FinishedAt"),
                "status": sandbox_state.get("Status"),
            }
        )
    repo.record_stop(
        node,
        incarnation,
        {
            "source": "docker.inspect",
            "container_id": inspected["Id"],
            "running": False,
            "finished_at": state["FinishedAt"],
            "exit_code": state.get("ExitCode"),
            "workspaces": workspace_evidence,
            "sandboxes": [item["container_name"] for item in sandboxes],
        },
    )
