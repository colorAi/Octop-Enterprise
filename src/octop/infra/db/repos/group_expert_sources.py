"""SQL projection of committed source publications and authority outboxes."""

from __future__ import annotations

import json
from typing import Any

from octop.infra.db.repos.execution import MAX_PAYLOAD, wire


def _scope_export(
    sources: list[dict[str, Any]], *, envelope: dict[str, Any]
) -> list[dict[str, Any]]:
    """Fit optional portraits around the full authorization-bearing scope."""
    exported = [{**source, "avatar": {}} for source in sources]
    remaining = MAX_PAYLOAD - len(wire({**envelope, "sources": exported}).encode())
    # Consider small portraits first so a few large files do not crowd out the
    # rest. Keep the source ordering stable for authorization fingerprints.
    sizes = sorted(
        (len(wire(source["avatar"]).encode()) - 2, index)
        for index, source in enumerate(sources)
        if source["avatar"]
    )
    for size, index in sizes:
        if size > remaining:
            break
        exported[index]["avatar"] = sources[index]["avatar"]
        remaining -= size
    return exported


def publish_objects(
    conn: Any, now: int, objects: list[dict[str, Any]], *, source_manifest: str
) -> None:
    from octop.infra.db.repos.group_experts import digest, enqueue

    for value in objects:
        source, row_id = value["source_agent_id"], value["source_row_id"]
        valid = conn.execute(
            "SELECT 1 FROM agents a JOIN expert_assignment_source s ON s.source_user_id=a.user_id "
            "JOIN resource_source_heads h ON h.user_id=a.user_id JOIN users u ON u.id=a.user_id "
            "WHERE a.agent_id=? AND a.id=? AND a.enabled=1 AND a.kind='expert' AND u.disabled=0 AND u.role='admin' AND h.manifest_sha256=?",
            (source, row_id, source_manifest),
        ).fetchone()
        if not valid:
            # A committed manifest can outlive one enabled source. Its live
            # eligibility/revocation checks remain authoritative; other sources
            # must still be able to refresh their projections.
            continue
        old = conn.execute(
            "SELECT * FROM group_expert_source_versions WHERE source_agent_id=? AND source_row_id=?",
            (source, row_id),
        ).fetchone()
        snapshot = value.get("snapshot")
        reason = value.get("invalid_reason", "")
        provider_args = None
        if snapshot is not None:
            provider_args = (
                snapshot["provider_id"],
                snapshot["provider_generation"],
                snapshot["provider_revision"],
            )
            if not conn.execute(
                "SELECT 1 FROM group_expert_provider_versions WHERE provider_id=? AND generation=? AND revision=? AND deleted=0",
                provider_args,
            ).fetchone():
                reason = "group_expert_configuration_changed"
        else:
            snapshot = (
                json.loads(old["snapshot_json"])
                if old
                else {"source_agent_id": source, "source_row_id": row_id}
            )
        fingerprint = digest(snapshot)
        display = wire(value["display"])
        definition = value.get("definition_digest", "")
        domain = f"source:{source}:{row_id}"
        authority = conn.execute(
            "SELECT revision FROM group_expert_authorizations WHERE domain_key=?", (domain,)
        ).fetchone()
        floor = max(old["revision"] if old else 0, authority[0] if authority else 0)
        changed = (
            not old or old["snapshot_digest"] != fingerprint or old["invalid_reason"] != reason
        )
        revision = max(1, floor + int(changed))
        if (
            old
            and not changed
            and old["revision"] == revision
            and old["display_json"] == display
            and old["definition_digest"] == definition
        ):
            continue
        if provider_args is not None:
            # Only changed projections need a write lock against provider edits.
            conn.execute(
                "UPDATE group_expert_provider_versions SET revision=revision WHERE provider_id=?",
                (provider_args[0],),
            )
            if not conn.execute(
                "SELECT 1 FROM group_expert_provider_versions WHERE provider_id=? AND generation=? AND revision=? AND deleted=0",
                provider_args,
            ).fetchone():
                reason = "group_expert_configuration_changed"
                changed = (
                    not old
                    or old["snapshot_digest"] != fingerprint
                    or old["invalid_reason"] != reason
                )
                revision = max(1, floor + int(changed))
        conn.execute(
            "INSERT INTO group_expert_source_versions(source_agent_id,source_row_id,revision,snapshot_json,snapshot_digest,display_json,definition_digest,invalid_reason) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(source_agent_id,source_row_id) DO UPDATE SET revision=excluded.revision,snapshot_json=excluded.snapshot_json,snapshot_digest=excluded.snapshot_digest,display_json=excluded.display_json,definition_digest=excluded.definition_digest,invalid_reason=excluded.invalid_reason",
            (source, row_id, revision, wire(snapshot), fingerprint, display, definition, reason),
        )
        if not authority or authority[0] < revision:
            conn.execute(
                "INSERT INTO group_expert_authorizations VALUES(?,?) ON CONFLICT(domain_key) DO UPDATE SET revision=excluded.revision WHERE group_expert_authorizations.revision<excluded.revision",
                (domain, revision),
            )
        if changed:
            enqueue(
                conn,
                now,
                operation=f"invalidate:{domain}:{revision}",
                kind="invalidate",
                target=domain,
                payload={"domain_key": domain, "revision": revision},
            )


def available_sources(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT s.* FROM group_expert_source_versions s JOIN agents a ON a.agent_id=s.source_agent_id AND a.id=s.source_row_id "
        "JOIN expert_assignment_source policy ON policy.source_user_id=a.user_id JOIN users u ON u.id=a.user_id "
        "WHERE a.enabled=1 AND a.kind='expert' AND u.role='admin' AND u.disabled=0 AND s.invalid_reason='' ORDER BY s.source_agent_id"
    ).fetchall()
    result = []
    for row in rows:
        snapshot = json.loads(row["snapshot_json"])
        current = conn.execute(
            "SELECT 1 FROM group_expert_provider_versions WHERE provider_id=? AND generation=? AND revision=? AND deleted=0",
            (
                snapshot["provider_id"],
                snapshot["provider_generation"],
                snapshot["provider_revision"],
            ),
        ).fetchone()
        if not current:
            continue
        display = json.loads(row["display_json"])
        result.append(
            {
                "source_agent_id": row["source_agent_id"],
                "source_row_id": row["source_row_id"],
                "display_name": display.get("name") or row["source_agent_id"],
                "avatar": display.get("avatar") or {},
                "authorization_versions": {
                    f"source:{row['source_agent_id']}:{row['source_row_id']}": row["revision"],
                    "provider:" + snapshot["provider_generation"]: snapshot["provider_revision"],
                },
            }
        )
    return result


def refresh_scopes(conn: Any, now: int) -> None:
    from octop.infra.db.repos.group_experts import digest, enqueue

    sources = available_sources(conn)
    for scope in conn.execute(
        "SELECT * FROM group_expert_scopes ORDER BY conversation_id"
    ).fetchall():
        desired = {
            (v["source_agent_id"], v["source_row_id"]) for v in json.loads(scope["sources_json"])
        }
        candidates = sources
        if scope["department_id"] is not None:
            pool = conn.execute(
                "SELECT expert_ids FROM department_resource_pools WHERE scope_id=?",
                (scope["department_id"],),
            ).fetchone()
            ids = json.loads(pool[0]) if pool else []
            candidates = [v for v in candidates if v["source_agent_id"] in ids]
        exported = [v for v in candidates if (v["source_agent_id"], v["source_row_id"]) in desired]
        execution = [
            {
                key: item[key]
                for key in ("source_agent_id", "source_row_id", "authorization_versions")
            }
            for item in exported
        ]
        fingerprint = digest(execution)
        domain = "scope:" + scope["conversation_id"]
        auth = conn.execute(
            "SELECT revision FROM group_expert_authorizations WHERE domain_key=?", (domain,)
        ).fetchone()
        revision = max(scope["revision"], auth[0] if auth else 0) + int(
            scope["export_digest"] != fingerprint
        )
        operation_prefix = f"scope:{scope['conversation_id']}:{revision}:"
        exported = _scope_export(
            exported, envelope={"operation_id": operation_prefix + "0" * 64, "revision": revision}
        )
        export_json = wire(exported)
        if fingerprint == scope["export_digest"] and export_json == scope["export_json"]:
            continue
        current_ids = {(v["source_agent_id"], v["source_row_id"]) for v in exported}
        for previous in json.loads(scope["export_json"]):
            source, source_row = previous["source_agent_id"], previous["source_row_id"]
            if (source, source_row) in current_ids:
                continue
            conn.execute(
                "INSERT INTO group_expert_binding_revocations VALUES(?,?,?,?) ON CONFLICT(conversation_id,source_agent_id,source_row_id) DO UPDATE SET before_revision=CASE WHEN excluded.before_revision>group_expert_binding_revocations.before_revision THEN excluded.before_revision ELSE group_expert_binding_revocations.before_revision END",
                (scope["conversation_id"], source, source_row, revision),
            )
            operation = f"bindings:{scope['conversation_id']}:{source}:{source_row}:{revision}"
            if not conn.execute(
                "SELECT 1 FROM group_expert_outbox WHERE operation_id=?", (operation,)
            ).fetchone():
                enqueue(
                    conn,
                    now,
                    operation=operation,
                    kind="invalidate",
                    target="bindings:" + scope["conversation_id"],
                    payload={
                        "domain_key": "bindings:" + scope["conversation_id"],
                        "revision": revision,
                        "revocations": [
                            {
                                "source_agent_id": source,
                                "source_row_id": source_row,
                                "before_revision": revision,
                            }
                        ],
                    },
                )
        conn.execute(
            "UPDATE group_expert_scopes SET revision=?,export_digest=?,export_json=? WHERE conversation_id=?",
            (revision, fingerprint, export_json, scope["conversation_id"]),
        )
        conn.execute(
            "INSERT INTO group_expert_authorizations VALUES(?,?) ON CONFLICT(domain_key) DO UPDATE SET revision=excluded.revision",
            (domain, revision),
        )
        operation = operation_prefix + digest(exported)
        enqueue(
            conn,
            now,
            operation=operation,
            kind="scope",
            target=scope["conversation_id"],
            payload={"operation_id": operation, "revision": revision, "sources": exported},
        )
