-- Group expert protocol v1. Admission stays disabled until both services and
-- every configured worker advertise the complete protocol.
ALTER TABLE group_settings ADD COLUMN experts_enabled boolean NOT NULL DEFAULT false;

CREATE TABLE group_expert_scopes (
    conversation_id text PRIMARY KEY REFERENCES group_settings(conversation_id),
    revision bigint NOT NULL CHECK (revision > 0),
    content_digest text NOT NULL,
    sources jsonb NOT NULL DEFAULT '[]'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE group_expert_authorizations (
    domain_key text PRIMARY KEY,
    revision bigint NOT NULL CHECK (revision > 0),
    content_digest text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE group_expert_bindings (
    binding_id text PRIMARY KEY,
    conversation_id text NOT NULL REFERENCES group_settings(conversation_id),
    source_agent_id text NOT NULL,
    source_row_id bigint NOT NULL CHECK (source_row_id > 0),
    generation bigint NOT NULL CHECK (generation > 0),
    scope_revision bigint NOT NULL CHECK (scope_revision > 0),
    joined_seq bigint NOT NULL CHECK (joined_seq > 0),
    state text NOT NULL CHECK (state IN ('active','removed','invalidated')),
    display_name text NOT NULL,
    avatar jsonb NOT NULL DEFAULT '{}'::jsonb,
    invited_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    removed_at timestamptz,
    UNIQUE(conversation_id,source_agent_id,generation)
);
CREATE UNIQUE INDEX group_expert_one_active_source ON group_expert_bindings(conversation_id,source_agent_id) WHERE state='active';

-- Generated sender_type keeps existing system-message writers correct, including
-- writers in older binaries during a migration with the feature still disabled.
ALTER TABLE messages ADD COLUMN expert_binding_id text REFERENCES group_expert_bindings(binding_id);
ALTER TABLE messages ADD COLUMN sender_type text GENERATED ALWAYS AS
    (CASE WHEN type='system' THEN 'system' WHEN expert_binding_id IS NOT NULL THEN 'group_expert' ELSE 'member' END) STORED;
ALTER TABLE messages ADD CONSTRAINT messages_expert_sender_check CHECK
    (expert_binding_id IS NULL OR (type='text' AND sender_id IS NOT NULL AND sender_id=expert_binding_id AND client_message_id IS NOT NULL));
-- Keep an invalid/stale mention as submitted so a legal human message can be
-- retained with a rejected call. Deliberately no FK on mention_expert_id.
ALTER TABLE messages ADD COLUMN mention_expert_id text;

CREATE TABLE group_expert_calls (
    call_id text PRIMARY KEY,
    conversation_id text NOT NULL REFERENCES group_settings(conversation_id),
    message_id text NOT NULL REFERENCES messages(id),
    message_seq bigint NOT NULL CHECK (message_seq > 0),
    binding_id text NOT NULL,
    binding_generation bigint,
    caller_id text NOT NULL,
    request_digest text NOT NULL,
    authorization_versions jsonb NOT NULL DEFAULT '{}'::jsonb,
    call_state text NOT NULL CHECK (call_state IN ('pending_admission','rejected','queued','dispatched','awaiting_delivery','completed','failed','cancel_requested','cancelled','unknown')),
    reason_code text NOT NULL DEFAULT '',
    control_seq bigint NOT NULL DEFAULT 0 CHECK (control_seq >= 0),
    dispatch_seq bigint CHECK (dispatch_seq > 0),
    execution_id text,
    result_digest text,
    receipt jsonb,
    reply_message_id text REFERENCES messages(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(message_id,binding_id)
);
CREATE INDEX group_expert_calls_queue ON group_expert_calls(conversation_id,message_seq) WHERE call_state IN ('pending_admission','queued','dispatched','awaiting_delivery','cancel_requested');
CREATE UNIQUE INDEX group_expert_single_dispatch ON group_expert_calls(conversation_id) WHERE call_state IN ('dispatched','awaiting_delivery','cancel_requested');
CREATE TABLE group_expert_call_authorizations (
    call_id text NOT NULL REFERENCES group_expert_calls(call_id),
    domain_key text NOT NULL REFERENCES group_expert_authorizations(domain_key),
    revision bigint NOT NULL CHECK (revision > 0),
    PRIMARY KEY(call_id,domain_key)
);
CREATE INDEX group_expert_call_auth_domain ON group_expert_call_authorizations(domain_key,revision);

-- Delivery attempts may exhaust background_jobs retries; these business
-- obligations survive independently, even after their call becomes terminal.
CREATE TABLE group_expert_outbox (
    operation_id text PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('register','dispatch','cancel','invalidate','recall','scope','receipt')),
    target_id text NOT NULL,
    request_digest text NOT NULL,
    payload jsonb NOT NULL,
    acknowledged_at timestamptz,
    receipt jsonb,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX group_expert_outbox_pending ON group_expert_outbox(next_attempt_at,operation_id) WHERE acknowledged_at IS NULL;
ALTER TABLE operation_receipts DROP CONSTRAINT operation_receipts_scope_check;
ALTER TABLE operation_receipts ADD CONSTRAINT operation_receipts_scope_check
    CHECK (scope IN ('group.create','group.invite','department.group','group.mutate','group.expert'));
