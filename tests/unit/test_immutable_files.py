import hashlib
import os
import stat
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from octop.infra.utils.immutable_files import cleanup_temporary, read_object, store_object


@pytest.mark.parametrize("legacy", [False, True])
def test_concurrent_writers_publish_complete_identical_objects(tmp_path, monkeypatch, legacy):
    data = b"a complete immutable object\n" * 4096
    digest = hashlib.sha256(data).hexdigest()
    link = os.link
    barrier = threading.Barrier(2)
    if legacy:
        (tmp_path / digest).write_bytes(data)

    def publish(source, target):
        assert source.read_bytes() == data
        barrier.wait(timeout=10)
        return link(source, target)

    monkeypatch.setattr(os, "link", publish)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: store_object(tmp_path, data), range(2)))
    assert results == [digest, digest]
    assert list(tmp_path.iterdir()) == [tmp_path / digest]
    assert read_object(tmp_path, digest) == data
    if os.name == "posix":
        assert (tmp_path / digest).stat().st_mode & 0o222 == 0


@pytest.mark.parametrize("operation", ["fsync", "link"])
def test_failed_publication_leaves_no_visible_or_temporary_file(tmp_path, monkeypatch, operation):
    def fail(*args):
        raise OSError("injected disk failure")

    monkeypatch.setattr(os, operation, fail)
    with pytest.raises(OSError, match="disk failure"):
        store_object(tmp_path, b"incomplete")
    assert list(tmp_path.iterdir()) == []


def test_existing_corrupt_object_is_never_overwritten(tmp_path):
    data = b"complete"
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / digest).write_bytes(b"partial")
    with pytest.raises(ValueError, match="corrupt"):
        store_object(tmp_path, data)
    assert (tmp_path / digest).read_bytes() == b"partial"
    assert not list(tmp_path.glob(".pending-*"))


def test_unchanged_object_does_not_rewrite_its_contents(tmp_path, monkeypatch):
    digest = store_object(tmp_path, b"unchanged")

    def no_staging(*args, **kwargs):
        pytest.fail("an existing verified hash must not be rewritten")

    monkeypatch.setattr(tempfile, "mkstemp", no_staging)
    assert store_object(tmp_path, b"unchanged") == digest


def test_file_flushes_use_write_capable_handles_even_for_legacy_cache(tmp_path, monkeypatch):
    sync = os.fsync

    def check_handle(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            # A zero-byte write checks access without changing the content.
            os.write(fd, b"")
        sync(fd)

    monkeypatch.setattr(os, "fsync", check_handle)
    data = b"legacy cache"
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / digest).write_bytes(data)
    assert store_object(tmp_path, data) == digest
    assert store_object(tmp_path, data) == digest
    assert read_object(tmp_path, digest) == data


def test_object_reads_reject_symlinks_and_wrong_hashes(tmp_path):
    data = b"correct"
    digest = hashlib.sha256(data).hexdigest()
    target = tmp_path / "draft"
    target.write_bytes(data)
    try:
        (tmp_path / digest).symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="corrupt"):
        read_object(tmp_path, digest)
    with pytest.raises(ValueError, match="corrupt"):
        store_object(tmp_path, data)
    assert target.read_bytes() == data


def test_object_reads_are_bounded_and_names_are_digests(tmp_path):
    digest = store_object(tmp_path, b"payload")
    with pytest.raises(ValueError, match="corrupt"):
        read_object(tmp_path, digest, limit=2)
    for name in ("../draft", "", "f" * 63, "A" * 64):
        with pytest.raises(ValueError, match="invalid_object_digest"):
            read_object(tmp_path, name)


def test_recovery_only_removes_old_temporary_names(tmp_path):
    digest = store_object(tmp_path, b"committed or awaiting a database commit")
    recent, old = tmp_path / ".pending-recent", tmp_path / ".pending-abandoned"
    recent.write_bytes(b"writing")
    old.write_bytes(b"interrupted")
    timestamp = time.time() - 172800
    os.utime(old, (timestamp, timestamp))
    os.utime(tmp_path / digest, (timestamp, timestamp))
    assert cleanup_temporary(tmp_path) == 1
    assert recent.is_file() and not old.exists()
    assert read_object(tmp_path, digest)
    assert cleanup_temporary(tmp_path) == 0
    with pytest.raises(ValueError, match="retention"):
        cleanup_temporary(tmp_path, minimum_age=0)
