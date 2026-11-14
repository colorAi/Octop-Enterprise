-- Outputs have a synthetic expert owner and are unreadable until attached to
-- the one committed group reply. Existing message visibility controls downloads.
CREATE TABLE group_expert_artifacts (
    call_id text NOT NULL REFERENCES group_expert_calls(call_id),
    operation_id text NOT NULL,
    file_id text NOT NULL,
    asset_id text NOT NULL UNIQUE REFERENCES media_assets(id) ON DELETE CASCADE,
    request_digest text NOT NULL,
    PRIMARY KEY(call_id,operation_id,file_id)
);
UPDATE group_expert_runtime SET protocol=0,valid_until=now();
