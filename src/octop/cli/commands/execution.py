"""Local operator inspection and evidence-based reconciliation."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import click

from octop.cli.support.db import open_cli_services


@click.group()
def execution() -> None:
    """检查共享执行、确认容器停止及核对未知结果。"""


@execution.command("nodes")
def nodes() -> None:
    """列出节点、启动代次及停止状态。"""
    with open_cli_services(migrate=False) as services:
        click.echo(json.dumps(services.execution_repo.nodes(), ensure_ascii=False, indent=2))


@execution.command("inspect")
@click.argument("run_id")
def inspect_run(run_id: str) -> None:
    """列出需要人工核对的模型用量及工具回执。"""
    with open_cli_services(migrate=False) as services:
        click.echo(
            json.dumps(services.execution_repo.reconciliation(run_id), ensure_ascii=False, indent=2)
        )


@execution.command("list")
@click.option(
    "--state",
    default="unknown",
    type=click.Choice(
        [
            "unknown",
            "queued",
            "running",
            "awaiting_approval",
            "cancel_requested",
            "completed",
            "failed",
            "cancelled",
        ]
    ),
)
@click.option("--after", default="")
@click.option("--limit", default=100, type=click.IntRange(1, 500))
def list_runs(state: str, after: str, limit: int) -> None:
    """按状态分页检查任务；不会输出原始请求或凭据。"""
    with open_cli_services(migrate=False) as services:
        click.echo(
            json.dumps(
                services.execution_repo.list_runs(state=state, after=after, limit=limit),
                ensure_ascii=False,
                indent=2,
            )
        )


@execution.command("drain")
@click.option("--node", required=True)
@click.option("--incarnation", required=True)
def drain(node: str, incarnation: str) -> None:
    """停止该启动代次领取新任务，保留正在执行的任务。"""
    with open_cli_services(migrate=False) as services:
        services.execution_repo.drain(node, incarnation)
    click.echo("已停止新领取；请等待现有任务完成，再停止容器。")


@execution.command("confirm-stop")
@click.option("--node", required=True)
@click.option("--incarnation", required=True)
def confirm_stop(node: str, incarnation: str) -> None:
    """检查 Docker 中执行者及所有沙箱确已停止，再记录证明。"""
    from docker.client import DockerClient

    from octop.infra.execution.stop_verifier import confirm_container_stopped

    with (
        open_cli_services(migrate=False) as services,
        closing(DockerClient.from_env(timeout=5)) as client,
    ):
        try:
            confirm_container_stopped(services.execution_repo, client, node, incarnation)
        except Exception as exc:
            raise click.ClickException(
                "停止证明未通过；保留原归属，请核对容器身份及所有沙箱状态。"
            ) from exc
    click.echo("已记录停止证明；未知任务仍须逐项核对。")


@execution.command("reconcile")
@click.argument("run_id")
@click.option("--outcome", type=click.Choice(["completed", "failed", "cancelled"]), required=True)
@click.option(
    "--evidence", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True
)
def reconcile(run_id: str, outcome: str, evidence: Path) -> None:
    """按实际外部结果结算；证据须覆盖每个未知模型请求和工具操作。"""
    if evidence.stat().st_size > 1024 * 1024:
        raise click.ClickException("证据文件过大。")
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    with open_cli_services(migrate=False) as services:
        try:
            services.execution_repo.reconcile(run_id, outcome=outcome, evidence=payload)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
    click.echo("已保存核对结果和证据；未重放任何操作。")
