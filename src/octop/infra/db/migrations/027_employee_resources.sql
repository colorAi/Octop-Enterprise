ALTER TABLE expert_assignment_source ADD COLUMN default_skill_ids TEXT NOT NULL DEFAULT '[]';
CREATE TABLE IF NOT EXISTS department_resource_pools (
    scope_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL DEFAULT 0,
    expert_ids TEXT NOT NULL DEFAULT '[]',
    skill_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS employee_skill_assignments (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id TEXT NOT NULL,
    PRIMARY KEY(user_id,resource_id)
);
CREATE TABLE IF NOT EXISTS employee_resource_scopes (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    scope_id TEXT NOT NULL,
    membership_revision BIGINT NOT NULL,
    pending_scope TEXT,
    pending_json TEXT
);
UPDATE _schema_version SET version=27;
