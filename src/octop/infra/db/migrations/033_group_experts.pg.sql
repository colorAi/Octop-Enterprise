-- Durable group expert records do not create personal threads or checkpoints.
CREATE TABLE IF NOT EXISTS group_expert_scopes (
    conversation_id TEXT PRIMARY KEY,
    department_id TEXT,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>0),
    sources_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS group_expert_authorizations (
    domain_key TEXT PRIMARY KEY,
    revision BIGINT NOT NULL CHECK (revision>0)
);
CREATE TABLE IF NOT EXISTS group_expert_outbox (
    operation_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('scope','invalidate','result','control_receipt')),
    target_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    acknowledged_at BIGINT,
    receipt TEXT,
    attempts BIGINT NOT NULL DEFAULT 0 CHECK (attempts>=0),
    next_attempt_at BIGINT NOT NULL DEFAULT 0,
    created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS group_expert_outbox_pending ON group_expert_outbox(next_attempt_at,operation_id) WHERE acknowledged_at IS NULL;
CREATE TABLE IF NOT EXISTS group_expert_provider_versions (
    provider_id BIGINT PRIMARY KEY,
    generation TEXT NOT NULL UNIQUE,
    revision BIGINT NOT NULL CHECK (revision>0),
    deleted BIGINT NOT NULL DEFAULT 0 CHECK (deleted IN (0,1))
);
INSERT INTO group_expert_provider_versions(provider_id,generation,revision)
    SELECT id,md5(random()::text||clock_timestamp()::text||id::text),1 FROM providers WHERE true ON CONFLICT(provider_id) DO NOTHING;
INSERT INTO group_expert_authorizations(domain_key,revision)
    SELECT 'provider:'||generation,revision FROM group_expert_provider_versions WHERE true ON CONFLICT(domain_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS group_expert_source_versions (
    source_agent_id TEXT NOT NULL,
    source_row_id BIGINT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision>0),
    snapshot_json TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    display_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(source_agent_id,source_row_id)
);
CREATE TABLE IF NOT EXISTS group_expert_calls (
    call_id TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    binding_generation BIGINT NOT NULL CHECK (binding_generation>0),
    source_agent_id TEXT NOT NULL,
    source_row_id BIGINT NOT NULL CHECK (source_row_id>0),
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
    created_at BIGINT NOT NULL,
    CHECK ((admission_state='accepted' AND run_id IS NOT NULL AND snapshot_json IS NOT NULL) OR (admission_state<>'accepted' AND run_id IS NULL)),
    UNIQUE(message_id,binding_id)
);
CREATE TABLE IF NOT EXISTS group_expert_controls (
    call_id TEXT PRIMARY KEY,
    control_seq BIGINT NOT NULL DEFAULT 0 CHECK (control_seq>=0),
    dispatch_seq BIGINT CHECK (dispatch_seq>0),
    cancelled BIGINT NOT NULL DEFAULT 0 CHECK (cancelled IN (0,1))
);
CREATE TABLE IF NOT EXISTS group_expert_control_receipts (
    call_id TEXT NOT NULL,
    control_seq BIGINT NOT NULL CHECK (control_seq>0),
    request_json TEXT NOT NULL,
    PRIMARY KEY(call_id,control_seq)
);
CREATE TABLE IF NOT EXISTS group_expert_call_authorizations (
    call_id TEXT NOT NULL REFERENCES group_expert_calls(call_id),
    domain_key TEXT NOT NULL REFERENCES group_expert_authorizations(domain_key),
    revision BIGINT NOT NULL CHECK (revision>0),
    PRIMARY KEY(call_id,domain_key)
);
CREATE INDEX IF NOT EXISTS group_expert_call_auth_domain ON group_expert_call_authorizations(domain_key,revision);
ALTER TABLE execution_nodes ADD COLUMN group_expert_protocol BIGINT NOT NULL DEFAULT 0 CHECK (group_expert_protocol>=0);
CREATE OR REPLACE FUNCTION octop_group_expert_node_generation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.incarnation IS DISTINCT FROM NEW.incarnation THEN NEW.group_expert_protocol:=0; END IF; RETURN NEW; END; $$;
CREATE TRIGGER group_expert_node_generation BEFORE UPDATE OF incarnation ON execution_nodes FOR EACH ROW EXECUTE FUNCTION octop_group_expert_node_generation();


-- One-line function bodies also support the existing numbered-SQL splitter.
CREATE OR REPLACE FUNCTION octop_group_expert_provider_changed() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE v group_expert_provider_versions%ROWTYPE; pid bigint; BEGIN IF TG_OP='INSERT' THEN INSERT INTO group_expert_provider_versions(provider_id,generation,revision,deleted) VALUES(NEW.id,md5(random()::text||clock_timestamp()::text||NEW.id::text),1,0) ON CONFLICT(provider_id) DO UPDATE SET generation=excluded.generation,revision=1,deleted=0 RETURNING * INTO v; INSERT INTO group_expert_authorizations(domain_key,revision) VALUES('provider:'||v.generation,v.revision); RETURN NULL; END IF; IF TG_OP='UPDATE' THEN IF NOT (OLD.name IS DISTINCT FROM NEW.name OR OLD.kind IS DISTINCT FROM NEW.kind OR OLD.base_url IS DISTINCT FROM NEW.base_url OR OLD.api_key IS DISTINCT FROM NEW.api_key OR OLD.extra_json IS DISTINCT FROM NEW.extra_json OR OLD.models_json IS DISTINCT FROM NEW.models_json OR OLD.enabled IS DISTINCT FROM NEW.enabled) THEN RETURN NULL; END IF; pid:=NEW.id; ELSE pid:=OLD.id; END IF; UPDATE group_expert_provider_versions SET revision=revision+1,deleted=CASE WHEN TG_OP='DELETE' THEN 1 ELSE 0 END WHERE provider_id=pid RETURNING * INTO v; UPDATE group_expert_authorizations SET revision=v.revision WHERE domain_key='provider:'||v.generation; INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) VALUES('invalidate:provider:'||v.generation||':'||v.revision,'invalidate','provider:'||v.generation,json_build_object('domain_key','provider:'||v.generation,'revision',v.revision)::text,'provider:'||v.generation||':'||v.revision,CAST(EXTRACT(EPOCH FROM clock_timestamp()) AS BIGINT)); RETURN NULL; END; $$;
CREATE TRIGGER group_expert_provider_changed AFTER INSERT OR UPDATE OR DELETE ON providers FOR EACH ROW EXECUTE FUNCTION octop_group_expert_provider_changed();
UPDATE _schema_version SET version=33;
