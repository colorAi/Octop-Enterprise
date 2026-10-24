"""Content-addressed files, visible only after a complete durable write."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import time
from pathlib import Path

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def read_object(root: Path, digest: str, *, limit: int = 64 * 1024 * 1024) -> bytes:
    if not _DIGEST.fullmatch(digest):
        raise ValueError("invalid_object_digest")
    target = root / digest
    if target.is_symlink():
        raise ValueError("immutable_object_corrupt")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(os.open(target, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("immutable_object_corrupt")
        data = stream.read(limit + 1)
    if len(data) > limit or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("immutable_object_corrupt")
    return data


def _unlink_temporary(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        # Windows does not permit unlinking a read-only temporary hard link.
        if os.name != "nt":
            raise
        path.chmod(0o600)
        path.unlink(missing_ok=True)


def _sync_directory(root: Path) -> None:
    if os.name == "posix":
        directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _seal_existing(root: Path, digest: str, size: int) -> None:
    read_object(root, digest, limit=size)
    target = root / digest
    if target.stat().st_mode & 0o222:
        # Legacy caches were writable. Flush through a write-capable handle
        # before sealing; Windows cannot flush a read-only file handle.
        try:
            stream = target.open("r+b")
        except PermissionError:
            # A concurrent publisher may have sealed the same legacy object.
            if target.stat().st_mode & 0o222:
                raise
            read_object(root, digest, limit=size)
            return
        with stream:
            os.fsync(stream.fileno())
            target.chmod(0o400)
            os.fsync(stream.fileno())


def store_object(root: Path, data: bytes) -> str:
    """Publish with an exclusive link; never replace an existing digest.

    The writable drafts and temporary names are never exposed to readers. Mode
    bits prevent accidental writes; they are not a sandbox against the host owner.
    """
    digest = hashlib.sha256(data).hexdigest()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / digest
    if target.exists() or target.is_symlink():
        _seal_existing(root, digest, len(data))
        _sync_directory(root)
        return digest
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=root)
    temporary = Path(name)
    published = False
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            temporary.chmod(0o400)
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            # Includes old caches: a partial/corrupt file must not be reused.
            _seal_existing(root, digest, len(data))
        published = True
    finally:
        _unlink_temporary(temporary)
        if published:
            target.chmod(0o400)
    _sync_directory(root)
    return digest


def cleanup_temporary(root: Path, *, minimum_age: float = 86400) -> int:
    """Reap abandoned temporary names; never delete a published hash or receipt."""
    if minimum_age < 3600:
        raise ValueError("temporary_retention_too_short")
    cutoff = time.time() - minimum_age
    removed = 0
    if not root.is_dir():
        return removed
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.name.startswith(".pending-") or not entry.is_file(follow_symlinks=False):
                continue
            try:
                if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                    _unlink_temporary(Path(entry.path))
                    removed += 1
            except FileNotFoundError:
                continue
    return removed
