-- Existing employees remain in legacy mode until an administrator assigns experts.
ALTER TABLE users ADD COLUMN expert_access_mode TEXT NOT NULL DEFAULT 'legacy'
    CHECK (expert_access_mode IN ('legacy', 'assigned_only'));
ALTER TABLE users ADD COLUMN expert_assignment_revision INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS expert_assignment_source (
    id INTEGER PRIMARY KEY CHECK (id=1),
    source_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    revision INTEGER NOT NULL DEFAULT 0
);
INSERT INTO expert_assignment_source(id) VALUES (1) ON CONFLICT(id) DO NOTHING;
CREATE TABLE IF NOT EXISTS expert_assignments (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    PRIMARY KEY(user_id, source_agent_id)
);
-- Keep the source identity after source deletion so old employee history stays private.
CREATE TABLE IF NOT EXISTS assigned_expert_instances (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_agent_id TEXT NOT NULL,
    source_row_id INTEGER NOT NULL,
    instance_agent_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT 'pending',
    claimed_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id, source_agent_id, source_row_id)
);
UPDATE _schema_version SET version=26;
