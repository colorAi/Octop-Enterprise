-- SQLite cannot add ON DELETE CASCADE to an existing FK in-place.
-- Parent-owned cleanup triggers preserve existing rows and avoid rebuilding tables.
CREATE TRIGGER IF NOT EXISTS company_runtime_delete_agent
BEFORE DELETE ON agents
BEGIN
    DELETE FROM company_runtime_threads WHERE agent_id=OLD.agent_id;
END;
CREATE TRIGGER IF NOT EXISTS company_runtime_delete_user
BEFORE DELETE ON users
BEGIN
    DELETE FROM company_runtime_threads WHERE owner_user_id=OLD.id;
END;
UPDATE _schema_version SET version=25;
