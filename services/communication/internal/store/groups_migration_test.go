package store_test

import (
	"context"
	"testing"

	"octop/communication/internal/testdb"
)

func TestGroupTypesUpgradePreservesHistoryAndVisibility(t *testing.T) {
	st := testdb.Open(t, testdb.New(t))
	ctx := context.Background()
	_, err := st.Pool.Exec(ctx, `
        DROP TABLE company_group;
        DROP TABLE group_member_grants;
        ALTER TABLE group_settings DROP COLUMN group_kind;
        ALTER TABLE group_settings DROP COLUMN permission_version;
        DELETE FROM schema_migrations WHERE version=13;
        INSERT INTO users(id,status,identity_version,permissions) VALUES('employee','active',1,'["messaging"]');
        INSERT INTO departments(id,name) VALUES('legacy-dept','Legacy');
        INSERT INTO department_members(department_id,user_id) VALUES('legacy-dept','employee');
        INSERT INTO conversations(id,type,last_seq,created_by) VALUES('legacy-group','group',8,'employee');
        INSERT INTO group_settings(conversation_id,owner_id,name) VALUES('legacy-group','employee','Legacy group');
        INSERT INTO department_groups(department_id,conversation_id) VALUES('legacy-dept','legacy-group');
        INSERT INTO member_epochs(id,conversation_id,user_id,epoch,visible_from_seq,visible_through_seq,left_at) VALUES('old-epoch','legacy-group','employee',1,1,3,now());
        INSERT INTO member_epochs(id,conversation_id,user_id,epoch,visible_from_seq) VALUES('current-epoch','legacy-group','employee',2,7);
        INSERT INTO messages(id,conversation_id,seq,type,sender_id,text) VALUES('old-message','legacy-group',2,'text','employee','Original'),('gap-message','legacy-group',5,'text','employee','Not visible'),('new-message','legacy-group',8,'text','employee','Current');
        INSERT INTO conversation_user_state(id,conversation_id,user_id,last_read_seq,pinned,muted,hidden_at_seq,cleared_through_seq) VALUES('legacy-state','legacy-group','employee',7,true,true,8,2);
    `)
	if err != nil {
		t.Fatal(err)
	}
	snapshot := func() string {
		t.Helper()
		var v string
		err := st.Pool.QueryRow(ctx, `SELECT jsonb_build_object(
            'conversation',(SELECT to_jsonb(c) FROM conversations c WHERE id='legacy-group'),
            'epochs',(SELECT jsonb_agg(to_jsonb(e) ORDER BY epoch) FROM member_epochs e WHERE conversation_id='legacy-group'),
            'messages',(SELECT jsonb_agg(to_jsonb(m) ORDER BY seq) FROM messages m WHERE conversation_id='legacy-group'),
            'state',(SELECT to_jsonb(s) FROM conversation_user_state s WHERE conversation_id='legacy-group'))::text`).Scan(&v)
		if err != nil {
			t.Fatal(err)
		}
		return v
	}
	before := snapshot()
	if st.CheckShape(ctx) == nil {
		t.Fatal("old schema accepted")
	}
	applied, err := st.Migrate(ctx)
	if err != nil || len(applied) != 1 || applied[0] != 13 {
		t.Fatal("upgrade", applied, err)
	}
	if st.CheckShape(ctx) != nil {
		t.Fatal("new shape rejected")
	}
	if before != snapshot() {
		t.Fatal("migration changed existing history, epochs or state")
	}
	var kind string
	if err := st.Pool.QueryRow(ctx, `SELECT group_kind FROM group_settings WHERE conversation_id='legacy-group'`).Scan(&kind); err != nil || kind != "department" {
		t.Fatal(kind, err)
	}
	applied, err = st.Migrate(ctx)
	if err != nil || len(applied) != 0 {
		t.Fatal("migration not idempotent", applied, err)
	}
}
