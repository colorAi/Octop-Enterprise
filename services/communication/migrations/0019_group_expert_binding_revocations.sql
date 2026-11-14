-- Never resurrect a binding when a revoke and regrant arrive out of order.
CREATE TABLE group_expert_binding_revocations (
    conversation_id text NOT NULL REFERENCES conversations(id),
    source_agent_id text NOT NULL,
    source_row_id bigint NOT NULL,
    before_revision bigint NOT NULL,
    PRIMARY KEY(conversation_id,source_agent_id,source_row_id)
);
