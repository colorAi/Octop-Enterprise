-- A disable/re-enable of an expert tool cannot revive an old execution.
CREATE TABLE group_expert_tool_versions (
    source_agent_id TEXT NOT NULL,
    source_row_id BIGINT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1,
    PRIMARY KEY(source_agent_id,source_row_id)
);
INSERT INTO group_expert_tool_versions SELECT agent_id,id,1 FROM agents;
CREATE TRIGGER group_expert_tools_insert AFTER INSERT ON agents BEGIN
    INSERT INTO group_expert_tool_versions VALUES(NEW.agent_id,NEW.id,1) ON CONFLICT(source_agent_id,source_row_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER group_expert_tools_update AFTER UPDATE OF config_json ON agents WHEN OLD.config_json IS NOT NEW.config_json BEGIN
    UPDATE group_expert_tool_versions SET revision=revision+1 WHERE source_agent_id=NEW.agent_id AND source_row_id=NEW.id;
END;
CREATE TRIGGER group_expert_tools_delete AFTER DELETE ON agents BEGIN
    UPDATE group_expert_tool_versions SET revision=revision+1 WHERE source_agent_id=OLD.agent_id AND source_row_id=OLD.id;
END;
UPDATE _schema_version SET version=36;
