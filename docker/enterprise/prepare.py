#!/usr/bin/env python3
"""Create a NEW enterprise deployment's private configuration; never rotate existing keys."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path


def prepare(data: Path, env_file: Path, socket: Path) -> None:
    data = data.resolve()
    if env_file.exists() or (data.exists() and any(data.iterdir())):
        raise SystemExit(
            "Use empty data and a new env file; existing deployments must retain their keys"
        )
    if any(c in str(data) + str(socket) for c in '\n\r$"'):
        raise SystemExit("Paths must not contain newlines, dollar signs or quotes")
    gid = socket.stat().st_gid
    data.mkdir(parents=True, mode=0o700, exist_ok=True)
    for name in ("app", "media", "secrets", "postgres"):
        folder = data / name
        folder.mkdir(mode=0o700)
        if os.geteuid() == 0 and name != "postgres":
            os.chown(folder, 1000, 1000)
    for name in ("im_identity", "im_cursor"):
        path = data / "secrets" / name
        with path.open("x") as stream:
            stream.write(secrets.token_hex(32))
        path.chmod(0o600)
        if os.geteuid() == 0:
            os.chown(path, 1000, 1000)
    values = {
        "OCTOP_DATA_ROOT": str(data),
        "OCTOP_DOCKER_SOCKET": str(socket),
        "OCTOP_DOCKER_GID": str(gid),
        "OCTOP_BIND_ADDRESS": "127.0.0.1",
        "OCTOP_PG_BOOTSTRAP_PASSWORD": secrets.token_hex(32),
        "OCTOP_APP_PASSWORD": secrets.token_hex(32),
        "IM_APP_PASSWORD": secrets.token_hex(32),
        "OCTOP_BASE_IMAGE": "octop:source",
        "OCTOP_APP_IMAGE": "octop-enterprise:source",
        "OCTOP_IM_IMAGE": "octop-im:source",
        "OCTOP_MEDIA_HTTP_ORIGINS": "",
    }
    env_file.parent.mkdir(parents=True, exist_ok=True)
    with env_file.open("x") as stream:
        os.chmod(env_file, 0o600)
        stream.write("".join(f'{key}="{value}"\n' for key, value in values.items()))
    print(f"Created private deployment configuration: {env_file}")
    if os.geteuid() != 0 and os.getuid() != 1000:
        print(
            "Before startup, assign app/, media/, secrets/ and key files to uid/gid 1000 on the Linux host."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--socket", type=Path, default=Path("/var/run/docker.sock"))
    args = parser.parse_args()
    prepare(args.data, args.env, args.socket)
