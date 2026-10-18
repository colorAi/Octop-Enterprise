ALTER TABLE company_runtime_threads DROP CONSTRAINT IF EXISTS company_runtime_threads_agent_id_fkey;
ALTER TABLE company_runtime_threads ADD CONSTRAINT company_runtime_threads_agent_id_fkey
    FOREIGN KEY(agent_id) REFERENCES agents(agent_id) ON DELETE CASCADE;
ALTER TABLE company_runtime_threads DROP CONSTRAINT IF EXISTS company_runtime_threads_owner_user_id_fkey;
ALTER TABLE company_runtime_threads ADD CONSTRAINT company_runtime_threads_owner_user_id_fkey
    FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE CASCADE;
UPDATE _schema_version SET version=25;
