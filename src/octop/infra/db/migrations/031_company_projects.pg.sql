-- Project metadata never contains native paths or directory grants.
CREATE TABLE IF NOT EXISTS company_projects (
    project_id TEXT PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    installation_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS company_projects_owner ON company_projects(owner_user_id, installation_hash, updated_at);
CREATE TABLE IF NOT EXISTS company_project_threads (
    thread_id TEXT PRIMARY KEY REFERENCES threads(thread_id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES company_projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS company_project_threads_project ON company_project_threads(project_id);
UPDATE _schema_version SET version = 31;
