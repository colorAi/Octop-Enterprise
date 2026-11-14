package store_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"

	"octop/communication/internal/store"
	"octop/communication/internal/testdb"
)

func TestGroupExpertsUpgradePreservesMessagesAndMembership(t *testing.T) {
	ctx := context.Background()
	st := testdb.Open(t, testdb.New(t))
	// Reconstruct the previous schema in this disposable test database only.
	_, err := st.Pool.Exec(ctx, `
        DROP FUNCTION group_expert_authority_changed() CASCADE;
        DROP FUNCTION group_expert_revoke_call(text,text);
        DROP TABLE group_expert_binding_revocations;
        DROP TABLE group_expert_runtime;
        DROP TABLE group_expert_call_authorizations;
        DROP TABLE group_expert_artifacts;
        DROP TABLE group_expert_calls;
        ALTER TABLE messages DROP CONSTRAINT messages_expert_sender_check;
        ALTER TABLE messages DROP COLUMN sender_type;
        ALTER TABLE messages DROP COLUMN expert_binding_id;
        ALTER TABLE messages DROP COLUMN mention_expert_id;
        DROP TABLE group_expert_bindings;
        DROP TABLE group_expert_scopes;
        DROP TABLE group_expert_authorizations;
        DROP TABLE group_expert_outbox;
        ALTER TABLE group_settings DROP COLUMN experts_enabled;
        ALTER TABLE operation_receipts DROP CONSTRAINT operation_receipts_scope_check;
        ALTER TABLE operation_receipts ADD CONSTRAINT operation_receipts_scope_check CHECK(scope IN ('group.create','group.invite','department.group','group.mutate'));
        DELETE FROM schema_migrations WHERE version>=17;
        INSERT INTO users(id,status,identity_version) VALUES('employee','active',1);
        INSERT INTO conversations(id,type,last_seq) VALUES('group','group',9);
        INSERT INTO group_settings(conversation_id,owner_id,name) VALUES('group','employee','Existing group');
        INSERT INTO member_epochs(id,conversation_id,user_id,epoch,visible_from_seq,visible_through_seq,left_at) VALUES('past','group','employee',1,1,3,now());
        INSERT INTO member_epochs(id,conversation_id,user_id,epoch,visible_from_seq) VALUES('present','group','employee',2,7);
        INSERT INTO conversation_user_state(id,conversation_id,user_id,cleared_through_seq,last_read_seq) VALUES('state','group','employee',2,8);
        INSERT INTO messages(id,conversation_id,seq,sender_id,client_message_id,type,text) VALUES('human','group',8,'employee','client-one','text','Existing text');
        INSERT INTO messages(id,conversation_id,seq,type,system_kind) VALUES('system','group',9,'system','group.members.changed');
        INSERT INTO message_receipts(id,sender_id,client_message_id,content_digest,result) VALUES('receipt','employee','client-one',decode('1234','hex'),'{}');
    `)
	if err != nil {
		t.Fatal(err)
	}
	snapshot := func() string {
		t.Helper()
		var value string
		err := st.Pool.QueryRow(ctx, `SELECT jsonb_build_object(
            'messages',(SELECT jsonb_agg((to_jsonb(m)-'sender_type'-'expert_binding_id'-'mention_expert_id') ORDER BY seq) FROM messages m),
            'epochs',(SELECT jsonb_agg(to_jsonb(e) ORDER BY epoch) FROM member_epochs e),
            'state',(SELECT jsonb_agg(to_jsonb(s)) FROM conversation_user_state s),
            'receipts',(SELECT jsonb_agg(to_jsonb(r)) FROM message_receipts r))::text`).Scan(&value)
		if err != nil {
			t.Fatal(err)
		}
		return value
	}
	before := snapshot()
	if st.CheckShape(ctx) == nil {
		t.Fatal("previous schema accepted by the new binary")
	}
	versions, err := st.Migrate(ctx)
	if err != nil || len(versions) != 5 || versions[0] != 17 || versions[1] != 18 || versions[2] != 19 || versions[3] != 20 || versions[4] != 21 {
		t.Fatal(versions, err)
	}
	if err := st.CheckShape(ctx); err != nil {
		t.Fatal(err)
	}
	if before != snapshot() {
		t.Fatal("upgrade changed legacy messages, receipts, visibility or clear marks")
	}
	var humanType, systemType string
	var enabled bool
	if err := st.Pool.QueryRow(ctx, `SELECT sender_type FROM messages WHERE id='human'`).Scan(&humanType); err != nil {
		t.Fatal(err)
	}
	if err := st.Pool.QueryRow(ctx, `SELECT sender_type FROM messages WHERE id='system'`).Scan(&systemType); err != nil {
		t.Fatal(err)
	}
	if err := st.Pool.QueryRow(ctx, `SELECT experts_enabled FROM group_settings WHERE conversation_id='group'`).Scan(&enabled); err != nil {
		t.Fatal(err)
	}
	if humanType != "member" || systemType != "system" || enabled {
		t.Fatal(humanType, systemType, enabled)
	}
	// Existing writers omit sender_type; later system messages must retain their identity.
	if _, err := st.Pool.Exec(ctx, `INSERT INTO messages(id,conversation_id,seq,type) VALUES('later-system','group',10,'system')`); err != nil {
		t.Fatal(err)
	}
	if err := st.Pool.QueryRow(ctx, `SELECT sender_type FROM messages WHERE id='later-system'`).Scan(&systemType); err != nil || systemType != "system" {
		t.Fatal(systemType, err)
	}
	if _, err := st.Pool.Exec(ctx, `INSERT INTO operation_receipts(id,actor_id,scope,idem_key,content_digest,result) VALUES('expert-receipt','employee','group.expert','invite-one',decode(repeat('ab',32),'hex'),'{}')`); err != nil {
		t.Fatal(err)
	}
}

func TestGroupExpertSchemaRetainsBindingIdentityAndOneDispatch(t *testing.T) {
	ctx := context.Background()
	st := testdb.Open(t, testdb.New(t))
	_, err := st.Pool.Exec(ctx, `
        INSERT INTO users(id,status,identity_version) VALUES('employee','active',1);
        INSERT INTO conversations(id,type,last_seq) VALUES('group','group',3);
        INSERT INTO group_settings(conversation_id,owner_id,name) VALUES('group','employee','Group');
        INSERT INTO group_expert_bindings(binding_id,conversation_id,source_agent_id,source_row_id,generation,scope_revision,joined_seq,state,display_name,invited_by) VALUES('binding-old','group','source',1,1,1,1,'removed','Historical expert','employee'),('binding-new','group','source',1,2,2,3,'active','Current expert','employee');
        INSERT INTO messages(id,conversation_id,seq,sender_id,client_message_id,type,text,expert_binding_id) VALUES('old-answer','group',2,'binding-old','call-original','text','Historical answer','binding-old');
        INSERT INTO messages(id,conversation_id,seq,sender_id,type,text,mention_expert_id) VALUES('question','group',4,'employee','text','Question','binding-new'),('question2','group',5,'employee','text','Next','binding-new');
        INSERT INTO group_expert_calls(call_id,conversation_id,message_id,message_seq,binding_id,binding_generation,caller_id,request_digest,call_state) VALUES('call','group','question',4,'binding-new',2,'employee','digest','dispatched');
    `)
	if err != nil {
		t.Fatal(err)
	}
	var name, senderType string
	if err := st.Pool.QueryRow(ctx, `SELECT b.display_name,m.sender_type FROM messages m JOIN group_expert_bindings b ON b.binding_id=m.expert_binding_id WHERE m.id='old-answer'`).Scan(&name, &senderType); err != nil {
		t.Fatal(err)
	}
	if name != "Historical expert" || senderType != "group_expert" {
		t.Fatal(name, senderType)
	}
	if _, err := st.Pool.Exec(ctx, `INSERT INTO group_expert_calls(call_id,conversation_id,message_id,message_seq,binding_id,binding_generation,caller_id,request_digest,call_state) VALUES('call2','group','question2',5,'binding-new',2,'employee','digest2','dispatched')`); err == nil {
		t.Fatal("two dispatch positions were allowed")
	}
	if _, err := st.Pool.Exec(ctx, `UPDATE group_expert_calls SET call_state='cancel_requested' WHERE call_id='call'`); err != nil {
		t.Fatal(err)
	}
	if _, err := st.Pool.Exec(ctx, `INSERT INTO group_expert_calls(call_id,conversation_id,message_id,message_seq,binding_id,binding_generation,caller_id,request_digest,call_state) VALUES('call2','group','question2',5,'binding-new',2,'employee','digest2','dispatched')`); err == nil {
		t.Fatal("cancellation released the dispatch position before stop acknowledgement")
	}
	if _, err := st.Pool.Exec(ctx, `UPDATE group_expert_calls SET call_state='unknown' WHERE call_id='call'`); err != nil {
		t.Fatal(err)
	}
	if _, err := st.Pool.Exec(ctx, `INSERT INTO group_expert_calls(call_id,conversation_id,message_id,message_seq,binding_id,binding_generation,caller_id,request_digest,call_state) VALUES('call2','group','question2',5,'binding-new',2,'employee','digest2','dispatched')`); err != nil {
		t.Fatal(err)
	}
	rollback := errors.New("rollback admission")
	if err := st.Tx(ctx, func(tx pgx.Tx) error {
		if err := store.EnqueueJob(ctx, tx, "group.expert.register", "register:call2", map[string]string{"call_id": "call2"}, time.Time{}); err != nil {
			return err
		}
		return rollback
	}); !errors.Is(err, rollback) {
		t.Fatal(err)
	}
	var exists bool
	if err := st.Pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM background_jobs WHERE work_key='register:call2')`).Scan(&exists); err != nil || exists {
		t.Fatal("rolled-back registration job survived", exists, err)
	}
}

func TestGroupExpertReliabilityUpgradeStopsLegacyRecurringJobs(t *testing.T) {
	ctx := context.Background()
	st := testdb.Open(t, testdb.New(t))
	_, err := st.Pool.Exec(ctx, `DELETE FROM schema_migrations WHERE version=21;
 ALTER TABLE group_expert_calls DROP COLUMN max_text_chars;
 DROP INDEX group_expert_calls_active;
 DROP INDEX group_expert_recall_target;
 INSERT INTO background_jobs(id,kind,work_key) VALUES('legacy','group.expert.reconcile','register:legacy'),('recurring','group.expert.reconcile','group.expert.reconcile');`)
	if err != nil {
		t.Fatal(err)
	}
	versions, err := st.Migrate(ctx)
	if err != nil || len(versions) != 1 || versions[0] != 21 {
		t.Fatal(versions, err)
	}
	var state, recurring string
	if err = st.Pool.QueryRow(ctx, `SELECT state FROM background_jobs WHERE id='legacy'`).Scan(&state); err != nil {
		t.Fatal(err)
	}
	if err = st.Pool.QueryRow(ctx, `SELECT state FROM background_jobs WHERE id='recurring'`).Scan(&recurring); err != nil {
		t.Fatal(err)
	}
	if state != "skipped" || recurring != "pending" {
		t.Fatal(state, recurring)
	}
	if err = st.CheckShape(ctx); err != nil {
		t.Fatal(err)
	}
}
