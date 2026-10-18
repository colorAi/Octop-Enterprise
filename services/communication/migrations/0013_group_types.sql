-- Additive upgrade: existing department IDs, messages, epochs and user state stay intact.
ALTER TABLE group_settings ADD COLUMN group_kind text NOT NULL DEFAULT 'project'
    CHECK (group_kind IN ('company','department','project'));
ALTER TABLE group_settings ADD COLUMN permission_version bigint NOT NULL DEFAULT 1;
UPDATE group_settings SET group_kind='department'
    WHERE conversation_id IN (SELECT conversation_id FROM department_groups);

CREATE TABLE company_group (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    conversation_id text NOT NULL UNIQUE REFERENCES group_settings(conversation_id)
);
CREATE UNIQUE INDEX group_settings_one_company ON group_settings(group_kind) WHERE group_kind='company';

-- Explicit grants are independent from department/company automatic membership.
CREATE TABLE group_member_grants (
    conversation_id text NOT NULL REFERENCES group_settings(conversation_id),
    user_id text NOT NULL REFERENCES users(id),
    granted_by text NOT NULL,
    granted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id,user_id)
);
INSERT INTO group_member_grants(conversation_id,user_id,granted_by)
    SELECT me.conversation_id,me.user_id,COALESCE(c.created_by,g.owner_id)
    FROM member_epochs me JOIN group_settings g ON g.conversation_id=me.conversation_id
    JOIN conversations c ON c.id=me.conversation_id
    WHERE g.group_kind='project' AND me.left_at IS NULL;

ALTER TABLE conversations DROP CONSTRAINT conversations_status_check;
ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
    CHECK (status IN ('active','dissolved','archived'));
ALTER TABLE operation_receipts DROP CONSTRAINT operation_receipts_scope_check;
ALTER TABLE operation_receipts ADD CONSTRAINT operation_receipts_scope_check
    CHECK (scope IN ('group.create','group.invite','department.group','group.mutate'));
