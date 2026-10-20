import hashlib
import os

import pytest

from octop.infra.company_runtime.local import ProjectBackend, artifact_index

from .test_runtime import acquire, install_agent


def artifact(path, data):
    return {"path": path, "size": len(data), "sha256": hashlib.sha256(data.encode()).hexdigest()}


def message(kind, ident):
    return {"type": kind, "data": {"id": ident, "content": ""}}


def test_explicit_delivery_accepts_project_paths_but_rejects_escape_and_directories(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    image = project / "图片.png"
    image.write_bytes(b"image")
    outside = tmp_path / "private.png"
    outside.write_bytes(b"private")
    backend = ProjectBackend(project, tmp_path / "state", lambda event: None, tool_path="")
    expected = artifact(str(image), "image")
    for path in [image.name, "/" + image.name, str(image)]:
        assert backend.artifact_metadata(path) == expected
    for path in ["../private.png", str(outside), ".", "missing.png"]:
        with pytest.raises(ValueError):
            backend.artifact_metadata(path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_explicit_delivery_cannot_follow_a_link_outside_the_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "private.png"
    outside.write_bytes(b"private")
    (project / "linked.png").symlink_to(outside)
    backend = ProjectBackend(project, tmp_path / "state", lambda event: None, tool_path="")
    with pytest.raises(ValueError):
        backend.artifact_metadata("linked.png")


def test_artifact_delta_excludes_unchanged_preexisting_and_preserves_overwrite(tmp_path):
    old = tmp_path / "first.png"
    old.write_bytes(b"one")
    snapshot = artifact_index(tmp_path, limit=None)
    second = tmp_path / "second.png"
    second.write_bytes(b"two")
    assert artifact_index(tmp_path, previous=snapshot) == [artifact(str(second), "two")]
    old.write_bytes(b"changed")
    assert artifact_index(tmp_path, previous=snapshot) == [
        artifact(str(old), "changed"),
        artifact(str(second), "two"),
    ]
    assert snapshot == [artifact(str(old), "one")]
    assert old.read_bytes() == b"changed"  # Never copy/move a user's overwritten file.


def test_snapshot_does_not_stop_before_files_beyond_result_cap(tmp_path):
    for i in range(1002):
        (tmp_path / f"{i:04}.txt").write_text("same")
    before = artifact_index(tmp_path, limit=None)
    assert len(before) == 1002
    last = tmp_path / "1001.txt"
    last.write_text("changed")
    assert artifact_index(tmp_path, previous=before) == [artifact(str(last), "changed")]


@pytest.mark.asyncio
async def test_legacy_cumulative_images_are_reconstructed_per_original_turn(company):
    server, client, _, _ = company
    install_agent(server)
    repo = server.services.company_runtime_repo
    first, second = artifact("/work/one.png", "one"), artifact("/work/two.png", "two")
    prior_messages = []
    for number, files in [(1, [first]), (2, [first, second]), (3, [first, second])]:
        task = f"task{number}"
        await acquire(client, task)
        prior_messages.extend([message("human", task + "_user"), message("ai", task + "_ai")])
        await client.request(
            f"tasks/{task}/events",
            {
                "event_id": task + "_messages",
                "seq": 1,
                "payload": {"kind": "messages", "messages": prior_messages},
            },
        )
        repo.append_event(task, task + "_artifacts", 2, {"kind": "artifacts", "files": files})
        repo.transition(task, "completed")
    assert repo.artifact_turns("thread1") == [
        {
            "task_id": "task1",
            "device_id": "device0",
            "user_message_id": "task1_user",
            "message_id": "task1_ai",
            "files": [first],
        },
        {
            "task_id": "task2",
            "device_id": "device0",
            "user_message_id": "task2_user",
            "message_id": "task2_ai",
            "files": [second],
        },
    ]
    assert repo.artifacts("thread1") == [first, second]
    assert repo.artifact_turns("thread2") == []
    await client.close()


@pytest.mark.asyncio
async def test_turn_deltas_keep_prior_sha_and_honor_explicit_message_anchors(company):
    server, client, _, _ = company
    install_agent(server)
    repo = server.services.company_runtime_repo
    first, changed = artifact("/work/same.png", "one"), artifact("/work/same.png", "two")
    for number, files in [(1, [first]), (2, [changed]), (3, [])]:
        task = f"task{number}"
        await acquire(client, task)
        await client.request(
            f"tasks/{task}/events",
            {
                "event_id": task + "_messages",
                "seq": 1,
                "payload": {
                    "kind": "messages",
                    "messages": [message("human", task + "_user"), message("ai", task + "_reply")],
                },
            },
        )
        repo.append_event(
            task,
            task + "_artifacts",
            2,
            {
                "kind": "artifacts",
                "scope": "turn",
                "user_message_id": task + "_user",
                "message_id": task + "_reply",
                "files": files,
            },
        )
        repo.transition(task, "completed")
    turns = repo.artifact_turns("thread1")
    assert [turn["files"] for turn in turns] == [[first], [changed]]
    assert [turn["message_id"] for turn in turns] == ["task1_reply", "task2_reply"]
    assert repo.artifacts("thread1") == [changed]
    await client.close()
