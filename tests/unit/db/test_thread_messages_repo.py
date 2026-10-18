from __future__ import annotations

import json
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.thread_messages import ThreadMessageInput, ThreadMessageRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo


def _repos(tmp_path: Path) -> tuple[SqlitePool, ThreadRepo, ThreadMessageRepo]:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    user_id = UserRepo(db).create(username="u", password_hash="hash", role="user")
    AgentRepo(db).create(agent_id="agt", user_id=user_id, name="Agent")
    return db, ThreadRepo(db), ThreadMessageRepo(db)


def _message(mid: str | None, text: str) -> ThreadMessageInput:
    return ThreadMessageInput(
        message_id=mid,
        role="human",
        message_json=f'{{"text":"{text}"}}',
        created_at=1,
    )


def test_new_thread_projection_appends_and_pages(tmp_path: Path) -> None:
    _db, threads, messages = _repos(tmp_path)
    threads.insert(
        thread_id="thr",
        agent_id="agt",
        user_id=1,
        channel_type="dashboard",
        session_key="sk",
        last_active=0,
    )

    assert messages.projection_status("thr") == "ready"
    assert (
        messages.append_if_ready(
            "thr",
            [_message("m1", "one"), _message("m2", "two"), _message("m3", "three")],
        )
        == 3
    )
    assert messages.append_if_ready("thr", [_message("m3", "replay")]) == 0

    recent, more = messages.page("thr", limit=2)
    older, older_more = messages.page("thr", limit=2, offset=2)
    assert [row.message_id for row in recent] == ["m2", "m3"]
    assert more is True
    assert [row.message_id for row in older] == ["m1"]
    assert older_more is False


def test_pending_legacy_thread_only_publishes_after_replace(tmp_path: Path) -> None:
    _db, threads, messages = _repos(tmp_path)
    threads.insert(
        thread_id="legacy",
        agent_id="agt",
        user_id=1,
        channel_type="dashboard",
        session_key="legacy-sk",
        last_active=10,
    )
    messages.mark_projection("legacy", "pending")

    assert messages.append_if_ready("legacy", [_message("new", "new")]) == 0
    assert (
        messages.replace_all(
            "legacy",
            [_message("old", "old"), _message("new", "new")],
        )
        == 2
    )
    page, more = messages.page("legacy", limit=25)
    assert messages.projection_status("legacy") == "ready"
    assert [row.message_id for row in page] == ["old", "new"]
    assert more is False


def test_migration_summary_and_candidates_are_scoped(tmp_path: Path) -> None:
    _db, threads, messages = _repos(tmp_path)
    for thread_id, last_active in (("old-1", 10), ("old-2", 20), ("empty", 0)):
        threads.insert(
            thread_id=thread_id,
            agent_id="agt",
            user_id=1,
            channel_type="dashboard",
            session_key=thread_id,
            last_active=last_active,
        )
    messages.mark_projection("old-1", "pending")
    messages.mark_projection("old-2", "failed", error="boom")

    summary = messages.migration_summary(agent_id="agt", user_id=1)
    candidates = messages.migration_candidates(agent_id="agt", user_id=1, limit=10)

    assert summary.remaining == 2
    assert summary.pending == 1
    assert summary.failed == 1
    assert [(row.thread_id, row.status) for row in candidates] == [
        ("old-2", "failed"),
        ("old-1", "pending"),
    ]


@pytest.mark.parametrize("envelope", ["data", "kwargs", None])
def test_recent_context_metadata_is_scoped_and_bounded(
    tmp_path: Path, envelope: str | None
) -> None:
    _db, threads, messages = _repos(tmp_path)
    for tid in ("local", "other"):
        threads.insert(
            thread_id=tid,
            agent_id="agt",
            user_id=1,
            channel_type="dashboard",
            session_key=tid,
            last_active=0,
        )
    usage = {"input_tokens": 11046, "segments": {"system_prompt": 2436, "skills": 3058}}
    data = {"content": "reply", "additional_kwargs": {"context_usage": usage}}
    payload = {envelope: data} if envelope else data
    messages.append_if_ready(
        "local",
        [
            ThreadMessageInput("a1", "ai", json.dumps(payload), 1),
            ThreadMessageInput("bad", "ai", "invalid legacy json", 2),
            ThreadMessageInput("huge", "ai", json.dumps({"content": "x" * 300000}), 3),
            ThreadMessageInput(
                "user",
                "human",
                json.dumps(
                    {
                        "additional_kwargs": {
                            "context_usage": {"input_tokens": 99999, "segments": {}}
                        }
                    }
                ),
                4,
            ),
        ],
    )
    messages.append_if_ready(
        "other",
        [
            ThreadMessageInput(
                "other-a",
                "ai",
                json.dumps(
                    {
                        "additional_kwargs": {
                            "context_usage": {"input_tokens": 99999, "segments": {}}
                        }
                    }
                ),
                5,
            )
        ],
    )
    assert messages.last_context_usage("local") == usage
    assert messages.last_context_usage("missing") is None
