ALTER TABLE execution_reservations ADD COLUMN IF NOT EXISTS estimated BIGINT CHECK (estimated >= 0);
ALTER TABLE company_runtime_tasks ADD COLUMN IF NOT EXISTS installation_hash TEXT;
CREATE INDEX IF NOT EXISTS company_projects_account ON company_projects(owner_user_id, updated_at, project_id);
CREATE TABLE IF NOT EXISTS company_runtime_checkpoint_heads (
    thread_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    PRIMARY KEY(thread_id,namespace),
    FOREIGN KEY(thread_id,namespace,checkpoint_id)
        REFERENCES company_runtime_checkpoints(thread_id,namespace,checkpoint_id) ON DELETE CASCADE
);
UPDATE _schema_version SET version = 32;
