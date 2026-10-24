-- Shared execution protocol. Leases never prove an executor has stopped.

-- Sandbox memory budget: startup reservations persist until verified stop.
CREATE TABLE IF NOT EXISTS execution_sandbox_budget (
    id INTEGER PRIMARY KEY CHECK (id=1),
    limit_bytes BIGINT NOT NULL DEFAULT 0 CHECK (limit_bytes>=0),
    sandbox_bytes BIGINT NOT NULL DEFAULT 0 CHECK (sandbox_bytes>=0),
    pressure_until BIGINT NOT NULL DEFAULT 0
);
INSERT INTO execution_sandbox_budget(id) VALUES(1) ON CONFLICT(id) DO NOTHING;
CREATE TABLE IF NOT EXISTS execution_sandboxes (
    container_name TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    incarnation TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    memory_bytes BIGINT NOT NULL CHECK (memory_bytes>0),
    token TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS execution_sandboxes_owner ON execution_sandboxes(node_id,incarnation);
-- End sandbox memory budget.
CREATE TABLE IF NOT EXISTS execution_control (
    id INTEGER PRIMARY KEY CHECK (id=1),
    max_running INTEGER NOT NULL DEFAULT 2 CHECK (max_running>0),
    max_models INTEGER NOT NULL DEFAULT 0 CHECK (max_models>=0),
    max_queued INTEGER NOT NULL DEFAULT 256 CHECK (max_queued>0),
    max_user_queued INTEGER NOT NULL DEFAULT 8 CHECK (max_user_queued>0)
);
INSERT INTO execution_control(id) VALUES(1) ON CONFLICT(id) DO NOTHING;

CREATE TABLE IF NOT EXISTS execution_nodes (
    node_id TEXT PRIMARY KEY,
    incarnation TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('web','worker','monolith')),
    state TEXT NOT NULL CHECK (state IN ('ready','draining','suspect','stopped')),
    container_id TEXT NOT NULL DEFAULT '',
    lease_until BIGINT NOT NULL,
    stop_evidence TEXT,
    updated_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_owners (
    user_id BIGINT PRIMARY KEY,
    node_id TEXT NOT NULL,
    incarnation TEXT NOT NULL,
    token BIGINT NOT NULL CHECK (token>0)
);
CREATE TABLE IF NOT EXISTS execution_fairness (
    user_id BIGINT PRIMARY KEY,
    last_dispatch BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS execution_runs (
    run_id TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    agent_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_key TEXT NOT NULL,
    request_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued','running','awaiting_approval','cancel_requested','unknown','completed','failed','cancelled')),
    node_id TEXT,
    incarnation TEXT,
    token BIGINT,
    event_seq BIGINT NOT NULL DEFAULT 0,
    approval_id TEXT,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    UNIQUE(user_id,operation,request_key)
);
CREATE INDEX IF NOT EXISTS execution_runs_queue ON execution_runs(state,created_at);
CREATE INDEX IF NOT EXISTS execution_runs_user ON execution_runs(user_id,state);
CREATE TABLE IF NOT EXISTS execution_sessions (
    thread_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES execution_runs(run_id)
);
CREATE TABLE IF NOT EXISTS execution_commands (
    run_id TEXT NOT NULL REFERENCES execution_runs(run_id),
    command_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('cancel','approval')),
    payload TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending','applied')),
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id,command_key)
);
CREATE TABLE IF NOT EXISTS execution_events (
    run_id TEXT NOT NULL REFERENCES execution_runs(run_id),
    seq BIGINT NOT NULL,
    event_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id,seq),
    UNIQUE(run_id,event_key)
);
CREATE TABLE IF NOT EXISTS execution_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    seq BIGINT NOT NULL,
    created_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_reservations (
    reservation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES execution_runs(run_id),
    provider TEXT NOT NULL,
    reserved BIGINT NOT NULL CHECK (reserved>=0),
    actual BIGINT,
    state TEXT NOT NULL CHECK (state IN ('reserved','settled','unknown')),
    created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS execution_reservations_active ON execution_reservations(state,provider);
CREATE TABLE IF NOT EXISTS execution_device_connections (
    device_id TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    node_id TEXT NOT NULL,
    incarnation TEXT NOT NULL,
    generation BIGINT NOT NULL CHECK (generation>0),
    state TEXT NOT NULL CHECK (state IN ('connected','disconnected')),
    metadata TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_tools (
    run_id TEXT NOT NULL REFERENCES execution_runs(run_id),
    operation_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    generation BIGINT NOT NULL,
    request_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending','sent','completed','unknown')),
    result_json TEXT,
    PRIMARY KEY(run_id,operation_id)
);
CREATE TABLE IF NOT EXISTS execution_schedules (
    schedule_id TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES execution_runs(run_id),
    PRIMARY KEY(schedule_id,scheduled_at)
);
CREATE TABLE IF NOT EXISTS execution_leaders (
    name TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    incarnation TEXT NOT NULL,
    lease_until BIGINT NOT NULL
);

ALTER TABLE usage_log ADD COLUMN execution_run_id TEXT;
CREATE INDEX IF NOT EXISTS usage_execution_run ON usage_log(execution_run_id);
CREATE TABLE IF NOT EXISTS execution_workspaces (
    node_id TEXT NOT NULL,
    incarnation TEXT NOT NULL,
    container_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    PRIMARY KEY(node_id,incarnation,container_id)
);


CREATE TABLE IF NOT EXISTS resource_source_heads (
    user_id BIGINT PRIMARY KEY,
    generation BIGINT NOT NULL DEFAULT 0,
    manifest_sha256 TEXT,
    dirty_revision BIGINT NOT NULL DEFAULT 0,
    published_revision BIGINT NOT NULL DEFAULT -1,
    editing INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    updated_at BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS resource_source_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id BIGINT NOT NULL,
    generation BIGINT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(user_id,generation)
);
CREATE TRIGGER IF NOT EXISTS source_agent_insert AFTER INSERT ON agents BEGIN UPDATE resource_source_heads SET dirty_revision=dirty_revision+1 WHERE user_id=NEW.user_id; END;
CREATE TRIGGER IF NOT EXISTS source_agent_update AFTER UPDATE ON agents WHEN OLD.agent_id IS NOT NEW.agent_id OR OLD.user_id IS NOT NEW.user_id OR OLD.name IS NOT NEW.name OR OLD.description IS NOT NEW.description OR OLD.persona_mbti IS NOT NEW.persona_mbti OR OLD.default_model IS NOT NEW.default_model OR OLD.system_prompt IS NOT NEW.system_prompt OR OLD.enabled IS NOT NEW.enabled OR OLD.config_json IS NOT NEW.config_json OR OLD.icon IS NOT NEW.icon OR OLD.template_name IS NOT NEW.template_name OR OLD.is_shared IS NOT NEW.is_shared OR OLD.color IS NOT NEW.color OR OLD.icon_name IS NOT NEW.icon_name OR OLD.icon_url IS NOT NEW.icon_url OR OLD.skill_package_ids IS NOT NEW.skill_package_ids OR OLD.published_expert_id IS NOT NEW.published_expert_id OR OLD.welcome_message IS NOT NEW.welcome_message OR OLD.knowledge_base_ids IS NOT NEW.knowledge_base_ids OR OLD.mcp_servers IS NOT NEW.mcp_servers OR OLD.kind IS NOT NEW.kind BEGIN UPDATE resource_source_heads SET dirty_revision=dirty_revision+1 WHERE user_id=OLD.user_id OR user_id=NEW.user_id; END;
CREATE TRIGGER IF NOT EXISTS source_agent_delete AFTER DELETE ON agents BEGIN UPDATE resource_source_heads SET dirty_revision=dirty_revision+1 WHERE user_id=OLD.user_id; END;
CREATE TRIGGER IF NOT EXISTS source_package_insert AFTER INSERT ON skill_packages BEGIN UPDATE resource_source_heads SET dirty_revision=dirty_revision+1; END;
CREATE TRIGGER IF NOT EXISTS source_package_update AFTER UPDATE ON skill_packages BEGIN UPDATE resource_source_heads SET dirty_revision=dirty_revision+1; END;
CREATE TRIGGER IF NOT EXISTS source_package_delete AFTER DELETE ON skill_packages BEGIN UPDATE resource_source_heads SET dirty_revision=dirty_revision+1; END;

CREATE TABLE IF NOT EXISTS execution_reconciliations (run_id TEXT PRIMARY KEY REFERENCES execution_runs(run_id), evidence TEXT NOT NULL, created_at BIGINT NOT NULL);

CREATE TABLE IF NOT EXISTS execution_stops (node_id TEXT NOT NULL, incarnation TEXT NOT NULL, evidence TEXT NOT NULL, created_at BIGINT NOT NULL, PRIMARY KEY(node_id,incarnation));

UPDATE _schema_version SET version = 29;
