-- Employee read grants for centrally managed knowledge bases.
CREATE TABLE IF NOT EXISTS employee_knowledge_bases (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    created_at BIGINT NOT NULL,
    PRIMARY KEY (user_id, kb_id)
);
CREATE INDEX IF NOT EXISTS idx_employee_knowledge_kb ON employee_knowledge_bases(kb_id);

UPDATE _schema_version SET version = 30;
