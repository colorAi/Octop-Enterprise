package groups

import (
	"context"
	"github.com/jackc/pgx/v5"
	"octop/communication/internal/httpx"
	"octop/communication/internal/idgen"
	"octop/communication/internal/store"
	"sort"
)

func ids(ctx context.Context, tx pgx.Tx, sql string, args ...any) ([]string, error) {
	rows, e := tx.Query(ctx, sql, args...)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []string{}
	for rows.Next() {
		var id string
		if e = rows.Scan(&id); e != nil {
			return nil, e
		}
		out = append(out, id)
	}
	return out, rows.Err()
}

// ReconcileAll runs in conversation order for identity and organization changes.
func (s *Service) ReconcileAll(ctx context.Context, tx pgx.Tx) error {
	conversations, err := ids(ctx, tx, `SELECT conversation_id FROM group_settings ORDER BY conversation_id`)
	if err != nil {
		return err
	}
	for _, conv := range conversations {
		if err := s.Reconcile(ctx, tx, conv); err != nil {
			return err
		}
	}
	return nil
}

// Reconcile applies automatic membership plus explicit grants under the existing organization lock.
// The caller owns the transaction; epochs, events and audit always commit together.
func (s *Service) Reconcile(ctx context.Context, tx pgx.Tx, conv string) error {
	var status, kind string
	if e := tx.QueryRow(ctx, `SELECT c.status,g.group_kind FROM conversations c JOIN group_settings g ON g.conversation_id=c.id WHERE c.id=$1`, conv).Scan(&status, &kind); e != nil {
		return e
	}
	if status == "dissolved" {
		return nil
	}
	// Revoked accounts never silently regain explicit/project grants on reactivation.
	if _, e := tx.Exec(ctx, `DELETE FROM group_member_grants gr USING users u WHERE gr.user_id=u.id AND gr.conversation_id=$1 AND (u.status<>'active' OR NOT (u.permissions ? 'messaging'))`, conv); e != nil {
		return e
	}
	want, e := ids(ctx, tx, `SELECT u.id FROM users u WHERE u.status='active' AND u.permissions ? 'messaging' AND ($3='active' OR EXISTS(SELECT 1 FROM member_epochs me WHERE me.conversation_id=$1 AND me.user_id=u.id AND me.left_at IS NULL)) AND (
        $2='company' OR EXISTS(SELECT 1 FROM department_groups dg JOIN departments d ON d.id=dg.department_id AND d.enabled JOIN department_members dm ON dm.department_id=d.id WHERE dg.conversation_id=$1 AND dm.user_id=u.id)
        OR EXISTS(SELECT 1 FROM group_member_grants gr WHERE gr.conversation_id=$1 AND gr.user_id=u.id)) ORDER BY u.id`, conv, kind, status)
	if e != nil {
		return e
	}
	if len(want) > s.D.Cfg.Limits.GroupMaxMembers {
		return httpx.ErrGroupFull
	}
	seq, e := store.LockConversation(ctx, tx, conv)
	if e != nil {
		return e
	}
	active, e := ids(ctx, tx, `SELECT user_id FROM member_epochs WHERE conversation_id=$1 AND left_at IS NULL ORDER BY user_id`, conv)
	if e != nil {
		return e
	}
	current, target := map[string]bool{}, map[string]bool{}
	for _, u := range active {
		current[u] = true
	}
	for _, u := range want {
		target[u] = true
	}
	var owner string
	if e = tx.QueryRow(ctx, `SELECT owner_id FROM group_settings WHERE conversation_id=$1`, conv).Scan(&owner); e != nil {
		return e
	}
	changed := false
	for u := range current {
		if !target[u] {
			changed = true
		}
	}
	for u := range target {
		if !current[u] {
			changed = true
		}
	}
	if !changed {
		return nil
	}
	if !target[owner] && len(want) > 0 {
		owner = want[0]
	}
	for _, u := range active {
		if !target[u] {
			if _, e = tx.Exec(ctx, `UPDATE member_epochs SET left_at=now(),visible_through_seq=$3,leave_reason='organization' WHERE conversation_id=$1 AND user_id=$2 AND left_at IS NULL`, conv, u, seq); e != nil {
				return e
			}
		}
	}
	next, e := store.NextSeq(ctx, tx, conv)
	if e != nil {
		return e
	}
	msg := idgen.New("msg")
	if _, e = tx.Exec(ctx, `INSERT INTO messages(id,conversation_id,seq,type,text,system_kind)VALUES($1,$2,$3,'system','','group.members.changed')`, msg, conv, next); e != nil {
		return e
	}
	for _, u := range want {
		if !current[u] {
			if _, e = tx.Exec(ctx, `INSERT INTO member_epochs(id,conversation_id,user_id,epoch,visible_from_seq) SELECT $1,$2,$3,COALESCE(MAX(epoch),0)+1,$4 FROM member_epochs WHERE conversation_id=$2 AND user_id=$3`, idgen.New("epoch"), conv, u, next); e != nil {
				return e
			}
		}
		if _, e = tx.Exec(ctx, `INSERT INTO conversation_user_state(id,conversation_id,user_id)VALUES($1,$2,$3) ON CONFLICT(conversation_id,user_id) DO NOTHING`, idgen.New("state"), conv, u); e != nil {
			return e
		}
	}
	if _, e = tx.Exec(ctx, `UPDATE member_epochs SET role=CASE WHEN user_id=$2 THEN 'owner' WHEN role='owner' THEN 'member' ELSE role END WHERE conversation_id=$1 AND left_at IS NULL`, conv, owner); e != nil {
		return e
	}
	if _, e = tx.Exec(ctx, `UPDATE group_settings SET owner_id=$2,mute_all=CASE WHEN $3 THEN true WHEN $4 THEN false ELSE mute_all END,permission_version=permission_version+1,updated_at=now() WHERE conversation_id=$1`, conv, owner, len(want) == 0, len(active) == 0); e != nil {
		return e
	}
	union := map[string]bool{}
	for _, u := range active {
		union[u] = true
	}
	for _, u := range want {
		union[u] = true
	}
	users := []string{}
	for u := range union {
		users = append(users, u)
	}
	sort.Strings(users)
	for _, u := range users {
		if _, e = store.EmitSync(ctx, tx, u, "group.members.changed", conv, next, map[string]any{"conversation_id": conv, "active": target[u]}); e != nil {
			return e
		}
		if target[u] {
			if _, e = store.EmitSync(ctx, tx, u, "message.new", msg, next, map[string]string{"conversation_id": conv, "message_id": msg, "seq": store.BigStr(next)}); e != nil {
				return e
			}
		}
	}
	return audit(ctx, tx, "system:organization", "group.members.reconcile", conv, "", active, want)
}
