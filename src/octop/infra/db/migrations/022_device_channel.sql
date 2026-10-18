CREATE TABLE IF NOT EXISTS desktop_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL UNIQUE,
    owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    credential_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    protocol_version INTEGER NOT NULL,
    expires_at BIGINT NOT NULL,
    created_at BIGINT NOT NULL,
    last_seen_at BIGINT NOT NULL,
    revoked_at BIGINT
);
CREATE INDEX IF NOT EXISTS idx_desktop_devices_owner ON desktop_devices(owner_user_id);
CREATE TABLE IF NOT EXISTS desktop_pairing_codes (
    code_hash TEXT PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    expires_at BIGINT NOT NULL,
    credential_expires_at BIGINT NOT NULL
);
UPDATE _schema_version SET version = 22;
