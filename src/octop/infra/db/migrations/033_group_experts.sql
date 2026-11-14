-- Durable group expert records do not create personal threads or checkpoints.
CREATE TABLE IF NOT EXISTS group_expert_scopes (
    conversation_id TEXT PRIMARY KEY,
    department_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>0),
    sources_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS group_expert_authorizations (
    domain_key TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision>0)
);
CREATE TABLE IF NOT EXISTS group_expert_outbox (
    operation_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('scope','invalidate','result','control_receipt')),
    target_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    acknowledged_at INTEGER,
    receipt TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts>=0),
    next_attempt_at INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS group_expert_outbox_pending ON group_expert_outbox(next_attempt_at,operation_id) WHERE acknowledged_at IS NULL;
CREATE TABLE IF NOT EXISTS group_expert_provider_versions (
    provider_id INTEGER PRIMARY KEY,
    generation TEXT NOT NULL UNIQUE,
    revision INTEGER NOT NULL CHECK (revision>0),
    deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0,1))
);
INSERT INTO group_expert_provider_versions(provider_id,generation,revision)
    SELECT id,lower(hex(randomblob(16))),1 FROM providers WHERE true ON CONFLICT(provider_id) DO NOTHING;
INSERT INTO group_expert_authorizations(domain_key,revision)
    SELECT 'provider:'||generation,revision FROM group_expert_provider_versions WHERE true ON CONFLICT(domain_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS group_expert_source_versions (
    source_agent_id TEXT NOT NULL,
    source_row_id INTEGER NOT NULL,
    revision INTEGER NOT NULL CHECK (revision>0),
    snapshot_json TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    display_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(source_agent_id,source_row_id)
);
CREATE TABLE IF NOT EXISTS group_expert_calls (
    call_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    binding_generation INTEGER NOT NULL CHECK (binding_generation>0),
    source_agent_id TEXT NOT NULL,
    source_row_id INTEGER NOT NULL CHECK (source_row_id>0),
    request_json TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    admission_state TEXT NOT NULL CHECK (admission_state IN ('accepted','rejected','cancelled')),
    reason_code TEXT NOT NULL DEFAULT '',
    run_id TEXT UNIQUE REFERENCES execution_runs(run_id),
    snapshot_json TEXT,
    snapshot_digest TEXT,
    authorization_versions TEXT NOT NULL,
    result_json TEXT,
    result_digest TEXT,
    delivery_state TEXT NOT NULL DEFAULT 'none' CHECK (delivery_state IN ('none','pending','confirmed','unpublished')),
    delivery_receipt TEXT,
    created_at INTEGER NOT NULL,
    CHECK ((admission_state='accepted' AND run_id IS NOT NULL AND snapshot_json IS NOT NULL) OR (admission_state<>'accepted' AND run_id IS NULL)),
    UNIQUE(message_id,binding_id)
);
CREATE TABLE IF NOT EXISTS group_expert_controls (
    call_id TEXT PRIMARY KEY,
    control_seq INTEGER NOT NULL DEFAULT 0 CHECK (control_seq>=0),
    dispatch_seq INTEGER CHECK (dispatch_seq>0),
    cancelled INTEGER NOT NULL DEFAULT 0 CHECK (cancelled IN (0,1))
);
CREATE TABLE IF NOT EXISTS group_expert_control_receipts (
    call_id TEXT NOT NULL,
    control_seq INTEGER NOT NULL CHECK (control_seq>0),
    request_json TEXT NOT NULL,
    PRIMARY KEY(call_id,control_seq)
);
CREATE TABLE IF NOT EXISTS group_expert_call_authorizations (
    call_id TEXT NOT NULL REFERENCES group_expert_calls(call_id),
    domain_key TEXT NOT NULL REFERENCES group_expert_authorizations(domain_key),
    revision INTEGER NOT NULL CHECK (revision>0),
    PRIMARY KEY(call_id,domain_key)
);
CREATE INDEX IF NOT EXISTS group_expert_call_auth_domain ON group_expert_call_authorizations(domain_key,revision);
ALTER TABLE execution_nodes ADD COLUMN group_expert_protocol INTEGER NOT NULL DEFAULT 0 CHECK (group_expert_protocol>=0);
CREATE TRIGGER IF NOT EXISTS group_expert_node_generation AFTER UPDATE OF incarnation ON execution_nodes WHEN OLD.incarnation IS NOT NEW.incarnation BEGIN
    UPDATE execution_nodes SET group_expert_protocol=0 WHERE node_id=NEW.node_id;
END;

-- The trigger updates a separate tombstone, so deleting/recreating a provider
-- (even with the same integer ID) cannot reconnect an old snapshot.
CREATE TRIGGER IF NOT EXISTS group_expert_provider_insert AFTER INSERT ON providers BEGIN
    INSERT INTO group_expert_provider_versions(provider_id,generation,revision,deleted) VALUES(NEW.id,lower(hex(randomblob(16))),1,0) ON CONFLICT(provider_id) DO UPDATE SET generation=excluded.generation,revision=1,deleted=0;
    INSERT INTO group_expert_authorizations(domain_key,revision) SELECT 'provider:'||generation,revision FROM group_expert_provider_versions WHERE provider_id=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS group_expert_provider_update AFTER UPDATE OF name,kind,base_url,api_key,extra_json,models_json,enabled ON providers WHEN OLD.name IS NOT NEW.name OR OLD.kind IS NOT NEW.kind OR OLD.base_url IS NOT NEW.base_url OR OLD.api_key IS NOT NEW.api_key OR OLD.extra_json IS NOT NEW.extra_json OR OLD.models_json IS NOT NEW.models_json OR OLD.enabled IS NOT NEW.enabled BEGIN
    UPDATE group_expert_provider_versions SET revision=revision+1 WHERE provider_id=NEW.id;
    UPDATE group_expert_authorizations SET revision=(SELECT revision FROM group_expert_provider_versions WHERE provider_id=NEW.id) WHERE domain_key=(SELECT 'provider:'||generation FROM group_expert_provider_versions WHERE provider_id=NEW.id);
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:provider:'||generation||':'||revision,'invalidate','provider:'||generation,json_object('domain_key','provider:'||generation,'revision',revision),'provider:'||generation||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_provider_versions WHERE provider_id=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS group_expert_provider_delete AFTER DELETE ON providers BEGIN
    UPDATE group_expert_provider_versions SET revision=revision+1,deleted=1 WHERE provider_id=OLD.id;
    UPDATE group_expert_authorizations SET revision=(SELECT revision FROM group_expert_provider_versions WHERE provider_id=OLD.id) WHERE domain_key=(SELECT 'provider:'||generation FROM group_expert_provider_versions WHERE provider_id=OLD.id);
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:provider:'||generation||':'||revision,'invalidate','provider:'||generation,json_object('domain_key','provider:'||generation,'revision',revision),'provider:'||generation||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_provider_versions WHERE provider_id=OLD.id;
END;
UPDATE _schema_version SET version=33;
