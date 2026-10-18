#!/usr/bin/env python3
"""Copy existing Agent content into private Docker volumes during a stopped cutover.

The original tree and its runtime databases remain untouched. Run this from the
trusted Octop container with its normal data mount and Docker socket. By default
only inventory is produced. --apply requires the named main container stopped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
import tempfile
from pathlib import Path

import docker
import psycopg

from octop.infra.backend.isolation import create_isolated_backend, isolated_spec
from octop.infra.utils.paths import PathLayout


def content_entries(root: Path) -> list[Path]:
    entries = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        rel = base.relative_to(root)
        if str(rel) == ".octop":
            dirs[:] = [name for name in dirs if name not in {"sessions", "logs"}]
        for name in dirs + files:
            path = base / name
            if name.startswith("memory.sqlite") or name.startswith("checkpoints.sqlite"):
                continue
            if path.is_file() or path.is_symlink():
                entries.append(path)
    return sorted(entries)


def copy_workspace(backend: object, root: Path, entries: list[Path]) -> dict[str, str]:
    hashes = {}
    # Stream one disk-backed archive to Docker; large venvs must not require
    # thousands of round trips or loading the entire workspace into RAM.
    directories = sorted(
        {
            parent
            for path in entries
            for parent in path.relative_to(root).parents
            if str(parent) != "."
        },
        key=lambda p: (len(p.parts), str(p)),
    )
    with tempfile.TemporaryFile() as buffer:
        with tarfile.open(fileobj=buffer, mode="w", dereference=False) as archive:
            for directory in directories:
                info = tarfile.TarInfo(directory.as_posix())
                info.type, info.uid, info.gid, info.mode = tarfile.DIRTYPE, 1000, 1000, 0o755
                archive.addfile(info)
            for path in entries:
                relative = path.relative_to(root).as_posix()
                info = archive.gettarinfo(str(path), arcname=relative)
                info.uid = info.gid = 1000
                info.uname = info.gname = ""
                info.mode = (info.mode & 0o777) | 0o600
                if info.issym():
                    archive.addfile(info)
                    hashes[relative] = "symlink:" + info.linkname
                else:
                    with path.open("rb") as source:
                        hashes[relative] = hashlib.file_digest(source, "sha256").hexdigest()
                        source.seek(0)
                        archive.addfile(info, source)
        buffer.seek(0)
        if not backend._container.put_archive(backend._workspace_root, buffer):
            raise RuntimeError("workspace copy failed")
    return hashes


VERIFY_SCRIPT = """
import hashlib,json,os,pathlib,sys
root=pathlib.Path(sys.argv[1]); result={}
for folder,dirs,files in os.walk(root,followlinks=False):
 for name in dirs+files:
  p=pathlib.Path(folder)/name; rel=p.relative_to(root).as_posix()
  if p.is_symlink(): result[rel]='symlink:'+os.readlink(p)
  elif p.is_file(): result[rel]=hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps(result))
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--home", type=Path, default=Path(os.environ.get("OCTOP_HOME", "/data/.octop"))
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--stopped-container", default="octop")
    args = parser.parse_args()
    client = docker.from_env()
    if args.apply and client.containers.get(args.stopped_container).status == "running":
        raise RuntimeError("stop the main Octop container before migrating")
    paths = PathLayout(args.home.resolve())
    with psycopg.connect(os.environ["OCTOP_DATABASE_URL"]) as db:
        rows = db.execute(
            "SELECT id,user_id,agent_id,config_json FROM agents ORDER BY agent_id"
        ).fetchall()
    report = []
    for row_id, user_id, agent_id, raw in rows:
        root = paths.agents_dir / agent_id
        cfg = json.loads(raw or "{}")
        if cfg.get("workspace_dir") not in {None, str(root)}:
            raise RuntimeError(f"custom workspace requires an explicit migration: {agent_id}")
        spec = isolated_spec(paths, agent_id, root, instance_key=f"{row_id}:{user_id}")
        entries = content_entries(root)
        marker = paths.root / "isolation-migrations" / f"{agent_id}.json"
        row = {
            "agent_id": agent_id,
            "volume": spec["volume"],
            "files": len(entries),
            "applied": False,
        }
        if args.apply:
            if marker.exists():
                if json.loads(marker.read_text())["volume"] != spec["volume"]:
                    raise RuntimeError(f"migration record belongs to an older agent: {agent_id}")
                client.volumes.get(spec["volume"])
                row["applied"] = True
                row["already_migrated"] = True
            else:
                try:
                    client.volumes.get(spec["volume"])
                except docker.errors.NotFound:
                    pass
                else:
                    raise RuntimeError(
                        f"volume already exists without migration record: {agent_id}"
                    )
                backend = create_isolated_backend(spec)
                hashes = copy_workspace(backend, root, entries)
                result = backend._container.exec_run(["python3", "-c", VERIFY_SCRIPT, str(root)])
                if result.exit_code or json.loads(result.output) != hashes:
                    raise RuntimeError(f"workspace verification failed: {agent_id}")
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(
                    json.dumps({**row, "applied": True, "hashes": hashes}, ensure_ascii=False)
                )
                row["applied"] = True
                backend.close()
        report.append(row)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
