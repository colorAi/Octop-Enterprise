CREATE TABLE group_expert_tool_versions (
    source_agent_id TEXT NOT NULL,
    source_row_id BIGINT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1,
    PRIMARY KEY(source_agent_id,source_row_id)
);
INSERT INTO group_expert_tool_versions SELECT agent_id,id,1 FROM agents;
CREATE FUNCTION octop_group_tools_changed() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP='INSERT' THEN INSERT INTO group_expert_tool_versions VALUES(NEW.agent_id,NEW.id,1) ON CONFLICT(source_agent_id,source_row_id) DO UPDATE SET revision=group_expert_tool_versions.revision+1; ELSIF TG_OP='DELETE' THEN UPDATE group_expert_tool_versions SET revision=revision+1 WHERE source_agent_id=OLD.agent_id AND source_row_id=OLD.id; ELSIF OLD.config_json IS DISTINCT FROM NEW.config_json THEN UPDATE group_expert_tool_versions SET revision=revision+1 WHERE source_agent_id=NEW.agent_id AND source_row_id=NEW.id; END IF; RETURN NULL; END; $$;
CREATE TRIGGER group_expert_tools_change AFTER INSERT OR UPDATE OF config_json OR DELETE ON agents FOR EACH ROW EXECUTE FUNCTION octop_group_tools_changed();
UPDATE _schema_version SET version=36;
