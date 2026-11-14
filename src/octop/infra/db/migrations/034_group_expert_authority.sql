ALTER TABLE group_expert_calls ADD COLUMN usage_json TEXT;
ALTER TABLE group_expert_source_versions ADD COLUMN definition_digest TEXT NOT NULL DEFAULT '';
ALTER TABLE group_expert_source_versions ADD COLUMN invalid_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE group_expert_scopes ADD COLUMN export_digest TEXT NOT NULL DEFAULT '';
ALTER TABLE group_expert_scopes ADD COLUMN export_json TEXT NOT NULL DEFAULT '[]';
CREATE TABLE group_expert_management_receipts (
    actor_id BIGINT NOT NULL,
    request_id TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY(actor_id,request_id)
);
INSERT INTO group_expert_authorizations(domain_key,revision) SELECT 'user:'||id,1 FROM users WHERE true ON CONFLICT(domain_key) DO NOTHING;
CREATE TRIGGER group_expert_users_insert AFTER INSERT ON users WHEN 1 BEGIN
    INSERT INTO group_expert_authorizations(domain_key,revision) VALUES('user:'||NEW.id,1) ON CONFLICT(domain_key) DO UPDATE SET revision=revision+1;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:'||domain_key||':'||revision,'invalidate',domain_key,json_object('domain_key',domain_key,'revision',revision),domain_key||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_authorizations WHERE domain_key='user:'||NEW.id;
END;
CREATE TRIGGER group_expert_users_update AFTER UPDATE ON users WHEN OLD.disabled IS NOT NEW.disabled OR OLD.role IS NOT NEW.role OR OLD.expert_access_mode IS NOT NEW.expert_access_mode OR OLD.expert_assignment_revision IS NOT NEW.expert_assignment_revision OR OLD.permissions IS NOT NEW.permissions BEGIN
    INSERT INTO group_expert_authorizations(domain_key,revision) VALUES('user:'||NEW.id,1) ON CONFLICT(domain_key) DO UPDATE SET revision=revision+1;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:'||domain_key||':'||revision,'invalidate',domain_key,json_object('domain_key',domain_key,'revision',revision),domain_key||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_authorizations WHERE domain_key='user:'||NEW.id;
END;
CREATE TRIGGER group_expert_users_delete AFTER DELETE ON users WHEN 1 BEGIN
    INSERT INTO group_expert_authorizations(domain_key,revision) VALUES('user:'||OLD.id,1) ON CONFLICT(domain_key) DO UPDATE SET revision=revision+1;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:'||domain_key||':'||revision,'invalidate',domain_key,json_object('domain_key',domain_key,'revision',revision),domain_key||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_authorizations WHERE domain_key='user:'||OLD.id;
END;
CREATE TRIGGER group_expert_user_policies_insert AFTER INSERT ON user_policies WHEN NEW.name='model_access' BEGIN
    INSERT INTO group_expert_authorizations(domain_key,revision) VALUES('user:'||NEW.user_id,1) ON CONFLICT(domain_key) DO UPDATE SET revision=revision+1;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:'||domain_key||':'||revision,'invalidate',domain_key,json_object('domain_key',domain_key,'revision',revision),domain_key||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_authorizations WHERE domain_key='user:'||NEW.user_id;
END;
CREATE TRIGGER group_expert_user_policies_update AFTER UPDATE ON user_policies WHEN NEW.name='model_access' AND (OLD.enabled IS NOT NEW.enabled OR OLD.value IS NOT NEW.value) BEGIN
    INSERT INTO group_expert_authorizations(domain_key,revision) VALUES('user:'||NEW.user_id,1) ON CONFLICT(domain_key) DO UPDATE SET revision=revision+1;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:'||domain_key||':'||revision,'invalidate',domain_key,json_object('domain_key',domain_key,'revision',revision),domain_key||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_authorizations WHERE domain_key='user:'||NEW.user_id;
END;
CREATE TRIGGER group_expert_user_policies_delete AFTER DELETE ON user_policies WHEN OLD.name='model_access' BEGIN
    INSERT INTO group_expert_authorizations(domain_key,revision) VALUES('user:'||OLD.user_id,1) ON CONFLICT(domain_key) DO UPDATE SET revision=revision+1;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:'||domain_key||':'||revision,'invalidate',domain_key,json_object('domain_key',domain_key,'revision',revision),domain_key||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_authorizations WHERE domain_key='user:'||OLD.user_id;
END;
CREATE TRIGGER group_expert_expert_assignments_insert AFTER INSERT ON expert_assignments WHEN 1 BEGIN
    INSERT INTO group_expert_authorizations(domain_key,revision) VALUES('user:'||NEW.user_id,1) ON CONFLICT(domain_key) DO UPDATE SET revision=revision+1;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:'||domain_key||':'||revision,'invalidate',domain_key,json_object('domain_key',domain_key,'revision',revision),domain_key||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_authorizations WHERE domain_key='user:'||NEW.user_id;
END;
CREATE TRIGGER group_expert_expert_assignments_delete AFTER DELETE ON expert_assignments WHEN 1 BEGIN
    INSERT INTO group_expert_authorizations(domain_key,revision) VALUES('user:'||OLD.user_id,1) ON CONFLICT(domain_key) DO UPDATE SET revision=revision+1;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:'||domain_key||':'||revision,'invalidate',domain_key,json_object('domain_key',domain_key,'revision',revision),domain_key||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_authorizations WHERE domain_key='user:'||OLD.user_id;
END;
CREATE TRIGGER group_expert_employee_resource_scopes_insert AFTER INSERT ON employee_resource_scopes WHEN 1 BEGIN
    INSERT INTO group_expert_authorizations(domain_key,revision) VALUES('user:'||NEW.user_id,1) ON CONFLICT(domain_key) DO UPDATE SET revision=revision+1;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:'||domain_key||':'||revision,'invalidate',domain_key,json_object('domain_key',domain_key,'revision',revision),domain_key||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_authorizations WHERE domain_key='user:'||NEW.user_id;
END;
CREATE TRIGGER group_expert_employee_resource_scopes_update AFTER UPDATE ON employee_resource_scopes WHEN OLD.scope_id IS NOT NEW.scope_id OR OLD.membership_revision IS NOT NEW.membership_revision OR OLD.pending_scope IS NOT NEW.pending_scope BEGIN
    INSERT INTO group_expert_authorizations(domain_key,revision) VALUES('user:'||NEW.user_id,1) ON CONFLICT(domain_key) DO UPDATE SET revision=revision+1;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:'||domain_key||':'||revision,'invalidate',domain_key,json_object('domain_key',domain_key,'revision',revision),domain_key||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_authorizations WHERE domain_key='user:'||NEW.user_id;
END;
CREATE TRIGGER group_expert_employee_resource_scopes_delete AFTER DELETE ON employee_resource_scopes WHEN 1 BEGIN
    INSERT INTO group_expert_authorizations(domain_key,revision) VALUES('user:'||OLD.user_id,1) ON CONFLICT(domain_key) DO UPDATE SET revision=revision+1;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:'||domain_key||':'||revision,'invalidate',domain_key,json_object('domain_key',domain_key,'revision',revision),domain_key||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_authorizations WHERE domain_key='user:'||OLD.user_id;
END;
CREATE TRIGGER group_expert_department_insert AFTER INSERT ON department_resource_pools WHEN 1 BEGIN
    INSERT INTO group_expert_authorizations(domain_key,revision) SELECT domain_key,1 FROM (SELECT 'user:'||user_id AS domain_key FROM employee_resource_scopes WHERE scope_id=NEW.scope_id UNION SELECT 'scope:'||conversation_id FROM group_expert_scopes WHERE department_id=NEW.scope_id) WHERE true ON CONFLICT(domain_key) DO UPDATE SET revision=revision+1;
    UPDATE group_expert_scopes SET revision=revision+1,export_digest='' WHERE department_id=NEW.scope_id;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:'||domain_key||':'||revision,'invalidate',domain_key,json_object('domain_key',domain_key,'revision',revision),domain_key||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_authorizations WHERE domain_key IN (SELECT 'user:'||user_id AS domain_key FROM employee_resource_scopes WHERE scope_id=NEW.scope_id UNION SELECT 'scope:'||conversation_id FROM group_expert_scopes WHERE department_id=NEW.scope_id);
END;
CREATE TRIGGER group_expert_department_update AFTER UPDATE ON department_resource_pools WHEN OLD.expert_ids IS NOT NEW.expert_ids BEGIN
    INSERT INTO group_expert_authorizations(domain_key,revision) SELECT domain_key,1 FROM (SELECT 'user:'||user_id AS domain_key FROM employee_resource_scopes WHERE scope_id=NEW.scope_id UNION SELECT 'scope:'||conversation_id FROM group_expert_scopes WHERE department_id=NEW.scope_id) WHERE true ON CONFLICT(domain_key) DO UPDATE SET revision=revision+1;
    UPDATE group_expert_scopes SET revision=revision+1,export_digest='' WHERE department_id=NEW.scope_id;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:'||domain_key||':'||revision,'invalidate',domain_key,json_object('domain_key',domain_key,'revision',revision),domain_key||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_authorizations WHERE domain_key IN (SELECT 'user:'||user_id AS domain_key FROM employee_resource_scopes WHERE scope_id=NEW.scope_id UNION SELECT 'scope:'||conversation_id FROM group_expert_scopes WHERE department_id=NEW.scope_id);
END;
CREATE TRIGGER group_expert_department_delete AFTER DELETE ON department_resource_pools WHEN 1 BEGIN
    INSERT INTO group_expert_authorizations(domain_key,revision) SELECT domain_key,1 FROM (SELECT 'user:'||user_id AS domain_key FROM employee_resource_scopes WHERE scope_id=OLD.scope_id UNION SELECT 'scope:'||conversation_id FROM group_expert_scopes WHERE department_id=OLD.scope_id) WHERE true ON CONFLICT(domain_key) DO UPDATE SET revision=revision+1;
    UPDATE group_expert_scopes SET revision=revision+1,export_digest='' WHERE department_id=OLD.scope_id;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:'||domain_key||':'||revision,'invalidate',domain_key,json_object('domain_key',domain_key,'revision',revision),domain_key||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_authorizations WHERE domain_key IN (SELECT 'user:'||user_id AS domain_key FROM employee_resource_scopes WHERE scope_id=OLD.scope_id UNION SELECT 'scope:'||conversation_id FROM group_expert_scopes WHERE department_id=OLD.scope_id);
END;
CREATE TRIGGER group_expert_source_identity_0 BEFORE UPDATE ON agents WHEN OLD.enabled IS NOT NEW.enabled OR OLD.user_id IS NOT NEW.user_id OR OLD.kind IS NOT NEW.kind BEGIN
    UPDATE group_expert_source_versions SET revision=revision+1,invalid_reason='group_expert_configuration_changed' WHERE source_agent_id=NEW.agent_id AND source_row_id=NEW.id;
    INSERT INTO group_expert_authorizations(domain_key,revision) SELECT 'source:'||source_agent_id||':'||source_row_id,revision FROM group_expert_source_versions WHERE source_agent_id=NEW.agent_id AND source_row_id=NEW.id ON CONFLICT(domain_key) DO UPDATE SET revision=excluded.revision;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:source:'||source_agent_id||':'||source_row_id||':'||revision,'invalidate','source:'||source_agent_id||':'||source_row_id,json_object('domain_key','source:'||source_agent_id||':'||source_row_id,'revision',revision),'source:'||source_agent_id||':'||source_row_id||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_source_versions WHERE source_agent_id=NEW.agent_id AND source_row_id=NEW.id;
END;
CREATE TRIGGER group_expert_source_identity_1 BEFORE DELETE ON agents WHEN 1 BEGIN
    UPDATE group_expert_source_versions SET revision=revision+1,invalid_reason='group_expert_configuration_changed' WHERE source_agent_id=OLD.agent_id AND source_row_id=OLD.id;
    INSERT INTO group_expert_authorizations(domain_key,revision) SELECT 'source:'||source_agent_id||':'||source_row_id,revision FROM group_expert_source_versions WHERE source_agent_id=OLD.agent_id AND source_row_id=OLD.id ON CONFLICT(domain_key) DO UPDATE SET revision=excluded.revision;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:source:'||source_agent_id||':'||source_row_id||':'||revision,'invalidate','source:'||source_agent_id||':'||source_row_id,json_object('domain_key','source:'||source_agent_id||':'||source_row_id,'revision',revision),'source:'||source_agent_id||':'||source_row_id||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_source_versions WHERE source_agent_id=OLD.agent_id AND source_row_id=OLD.id;
END;
CREATE TRIGGER group_expert_source_identity_2 BEFORE UPDATE ON expert_assignment_source WHEN OLD.source_user_id IS NOT NEW.source_user_id BEGIN
    UPDATE group_expert_source_versions SET revision=revision+1,invalid_reason='group_expert_configuration_changed' WHERE 1;
    INSERT INTO group_expert_authorizations(domain_key,revision) SELECT 'source:'||source_agent_id||':'||source_row_id,revision FROM group_expert_source_versions WHERE 1 ON CONFLICT(domain_key) DO UPDATE SET revision=excluded.revision;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:source:'||source_agent_id||':'||source_row_id||':'||revision,'invalidate','source:'||source_agent_id||':'||source_row_id,json_object('domain_key','source:'||source_agent_id||':'||source_row_id,'revision',revision),'source:'||source_agent_id||':'||source_row_id||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_source_versions WHERE 1;
END;
CREATE TRIGGER group_expert_source_identity_3 BEFORE UPDATE ON users WHEN OLD.disabled IS NOT NEW.disabled OR OLD.role IS NOT NEW.role BEGIN
    UPDATE group_expert_source_versions SET revision=revision+1,invalid_reason='group_expert_configuration_changed' WHERE source_agent_id IN (SELECT agent_id FROM agents WHERE user_id=NEW.id);
    INSERT INTO group_expert_authorizations(domain_key,revision) SELECT 'source:'||source_agent_id||':'||source_row_id,revision FROM group_expert_source_versions WHERE source_agent_id IN (SELECT agent_id FROM agents WHERE user_id=NEW.id) ON CONFLICT(domain_key) DO UPDATE SET revision=excluded.revision;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:source:'||source_agent_id||':'||source_row_id||':'||revision,'invalidate','source:'||source_agent_id||':'||source_row_id,json_object('domain_key','source:'||source_agent_id||':'||source_row_id,'revision',revision),'source:'||source_agent_id||':'||source_row_id||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_source_versions WHERE source_agent_id IN (SELECT agent_id FROM agents WHERE user_id=NEW.id);
END;
CREATE TRIGGER group_expert_source_identity_4 BEFORE DELETE ON users WHEN 1 BEGIN
    UPDATE group_expert_source_versions SET revision=revision+1,invalid_reason='group_expert_configuration_changed' WHERE source_agent_id IN (SELECT agent_id FROM agents WHERE user_id=OLD.id);
    INSERT INTO group_expert_authorizations(domain_key,revision) SELECT 'source:'||source_agent_id||':'||source_row_id,revision FROM group_expert_source_versions WHERE source_agent_id IN (SELECT agent_id FROM agents WHERE user_id=OLD.id) ON CONFLICT(domain_key) DO UPDATE SET revision=excluded.revision;
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,payload,request_digest,created_at) SELECT 'invalidate:source:'||source_agent_id||':'||source_row_id||':'||revision,'invalidate','source:'||source_agent_id||':'||source_row_id,json_object('domain_key','source:'||source_agent_id||':'||source_row_id,'revision',revision),'source:'||source_agent_id||':'||source_row_id||':'||revision,CAST(strftime('%s','now') AS INTEGER) FROM group_expert_source_versions WHERE source_agent_id IN (SELECT agent_id FROM agents WHERE user_id=OLD.id);
END;
UPDATE _schema_version SET version=34;
