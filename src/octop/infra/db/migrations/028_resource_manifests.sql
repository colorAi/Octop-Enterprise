CREATE TABLE IF NOT EXISTS managed_resource_manifests (
    agent_id TEXT PRIMARY KEY REFERENCES agents(agent_id) ON DELETE CASCADE,
    generation BIGINT NOT NULL CHECK (generation > 0),
    manifest_sha256 TEXT NOT NULL,
    resource_revision TEXT NOT NULL,
    updated_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS managed_resource_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    generation BIGINT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    resource_revision TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(agent_id,generation)
);
UPDATE _schema_version SET version=28;
