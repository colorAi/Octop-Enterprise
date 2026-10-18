#!/usr/bin/env python3
"""Quiesced enterprise backup: two databases, local data, keys and explicit sandbox volumes.

Run on the Linux Docker host. Requires the full, reviewed list of sandbox volumes.
Employee computer projects are always backed up separately on those computers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path


def backup(env_file: Path, destination: Path, volumes: list[str], no_volumes: bool) -> None:
    if not volumes and not no_volumes:
        raise SystemExit(
            "List every sandbox with --volume, or explicitly confirm --no-sandbox-volumes"
        )
    if len(set(volumes)) != len(volumes) or any(
        not re.fullmatch(r"octop_workspace_[a-f0-9]{24}", v) for v in volumes
    ):
        raise SystemExit("Invalid or duplicate sandbox volume name")
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(env_file.resolve()),
        "-f",
        str(Path(__file__).with_name("compose.yml")),
    ]
    config = json.loads(subprocess.check_output([*compose, "config", "--format", "json"]))
    mounts = config["services"]["octop"]["volumes"]
    app = Path(next(m["source"] for m in mounts if m["target"] == "/data/.octop"))
    root = app.parent
    if destination.resolve().is_relative_to(root.resolve()):
        raise SystemExit("Backup destination must be outside the deployment data directory")
    image = config["services"]["octop"]["image"]
    subprocess.run(["docker", "image", "inspect", image], check=True, stdout=subprocess.DEVNULL)
    running = subprocess.check_output(
        [*compose, "ps", "--services", "--status", "running"], text=True
    ).split()
    services = [s for s in ("octop", "communication") if s in running]
    workers: set[str] = set()
    volume_labels = {}
    for volume in volumes:
        info = json.loads(subprocess.check_output(["docker", "volume", "inspect", volume]))[0]
        if not info.get("Labels", {}).get("octop.agent.id") or info.get("Options"):
            raise SystemExit(f"Unmanaged sandbox volume: {volume}")
        volume_labels[volume] = info["Labels"]
        workers.update(
            subprocess.check_output(
                ["docker", "ps", "-q", "--filter", f"volume={volume}"], text=True
            ).split()
        )
    destination.mkdir(parents=True, exist_ok=False)
    destination.chmod(0o700)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "complete": False,
        "components": ["octop", "octop_im", "app", "media", "secrets", "deployment.env"],
        "sandbox_volumes": volumes,
        "sandbox_volume_labels": volume_labels,
        "sandbox_inventory_operator_confirmed": True,
        "excluded": ["employee_computer_projects"],
        "files": {},
    }
    try:
        if services:
            subprocess.run([*compose, "stop", *services], check=True)
        if workers:
            subprocess.run(["docker", "stop", *sorted(workers)], check=True)
        for database in ("octop", "octop_im"):
            with (destination / f"{database}.dump").open("wb") as stream:
                subprocess.run(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "postgres",
                        "pg_dump",
                        "-U",
                        "octop_bootstrap",
                        "-Fc",
                        database,
                    ],
                    stdout=stream,
                    check=True,
                )
        with tarfile.open(destination / "files.tar.gz", "w:gz") as archive:
            for name in ("app", "media", "secrets"):
                archive.add(root / name, arcname=name)
        shutil.copy2(env_file, destination / "deployment.env")
        for volume in volumes:
            with (destination / f"{volume}.tar").open("wb") as stream:
                subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--read-only",
                        "--user",
                        "0:0",
                        "--entrypoint",
                        "tar",
                        "-v",
                        f"{volume}:/backup-source:ro",
                        image,
                        "-C",
                        "/backup-source",
                        "-cf",
                        "-",
                        ".",
                    ],
                    stdout=stream,
                    check=True,
                )
        for path in destination.iterdir():
            with path.open("rb") as stream:
                manifest["files"][path.name] = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest["complete"] = True
    finally:
        # Restart even if writing the manifest fails (for example a full backup disk).
        try:
            (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        finally:
            try:
                if workers:
                    subprocess.run(["docker", "start", *sorted(workers)], check=True)
            finally:
                if services:
                    subprocess.run([*compose, "start", *services], check=True)
    print(destination / "manifest.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--volume", action="append")
    group.add_argument("--no-sandbox-volumes", action="store_true")
    args = parser.parse_args()
    backup(args.env, args.output, args.volume or [], args.no_sandbox_volumes)
