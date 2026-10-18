CREATE TABLE IF NOT EXISTS desktop_file_threads (
    thread_id TEXT PRIMARY KEY REFERENCES threads(thread_id) ON DELETE CASCADE
);
UPDATE _schema_version SET version = 23;
