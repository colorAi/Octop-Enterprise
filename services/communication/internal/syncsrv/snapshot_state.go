package syncsrv

import (
	"context"
	"encoding/json"

	"github.com/jackc/pgx/v5"
)

type snapshotEntry struct {
	Type    string
	ID      string
	Payload json.RawMessage
}

// Each query is evaluated in the snapshot's repeatable-read transaction.
// No password, session or provider credential is copied into snapshot data.
func snapshotEntries(ctx context.Context, tx pgx.Tx, user string) ([]snapshotEntry, error) {
	if _, err := tx.Exec(ctx, "SET LOCAL TIME ZONE 'UTC'"); err != nil {
		return nil, err
	}
	queries := []struct{ kind, sql string }{
		{"profile.updated", `SELECT u.id,jsonb_build_object('user_id',u.id,'nickname',u.nickname,'avatar_icon',u.avatar_icon,'avatar_url',u.avatar_url,'avatar_asset_id',u.avatar_asset_id,'job_title_id',u.job_title_id,'job_title',j.name) FROM users u LEFT JOIN job_titles j ON j.id=u.job_title_id WHERE u.id=$1`},
		{"contact.updated", `SELECT u.id,jsonb_build_object('user_id',u.id,'nickname',u.nickname,'avatar_icon',u.avatar_icon,'avatar_url',u.avatar_url,'department_id',d.id,'can_message',u.permissions ? 'messaging','is_admin',u.is_admin,'job_title_id',u.job_title_id,'job_title',j.name)
         FROM users u LEFT JOIN job_titles j ON j.id=u.job_title_id LEFT JOIN department_members dm ON dm.user_id=u.id LEFT JOIN departments d ON d.id=dm.department_id AND d.enabled
         WHERE u.status='active' AND (
            EXISTS(SELECT 1 FROM users self WHERE self.id=$1 AND self.permissions ? 'contacts')
            OR (EXISTS(SELECT 1 FROM users self WHERE self.id=$1 AND self.permissions ? 'messaging') AND (
                EXISTS(SELECT 1 FROM direct_pairs dp WHERE (dp.user_low=$1 AND dp.user_high=u.id) OR (dp.user_high=$1 AND dp.user_low=u.id))
                OR EXISTS(SELECT 1 FROM member_epochs mine JOIN member_epochs other ON other.conversation_id=mine.conversation_id
                    WHERE mine.user_id=$1 AND other.user_id=u.id
                    AND mine.visible_from_seq<=COALESCE(other.visible_through_seq,9223372036854775807)
                    AND other.visible_from_seq<=COALESCE(mine.visible_through_seq,9223372036854775807))
            ))) ORDER BY u.id`},
		{"conversation.created", `SELECT c.id,jsonb_build_object(
			'conversation_id',c.id,'type',c.type,'status',c.status,'last_seq',(CASE WHEN c.type='group' THEN COALESCE((SELECT MAX(COALESCE(me.visible_through_seq,c.last_seq)) FROM member_epochs me WHERE me.conversation_id=c.id AND me.user_id=$1),0) ELSE c.last_seq END)::text,
			'peer_user_id',CASE WHEN dp.user_low=$1 THEN dp.user_high ELSE dp.user_low END,
			'last_read_seq',COALESCE(st.last_read_seq,0)::text,'pinned',COALESCE(st.pinned,false),
			'muted',COALESCE(st.muted,false),'hidden_at_seq',st.hidden_at_seq::text,
			'cleared_through_seq',COALESCE(st.cleared_through_seq,0)::text,
			'archived',c.status<>'active' OR (c.type='group' AND NOT EXISTS
				(SELECT 1 FROM member_epochs me WHERE me.conversation_id=c.id AND me.user_id=$1 AND me.left_at IS NULL)),
			'group',CASE WHEN c.type='group' THEN jsonb_build_object('name',g.name,'group_kind',g.group_kind,
				'avatar_asset_id',g.avatar_asset_id,
				'owner_user_id',CASE WHEN EXISTS (SELECT 1 FROM member_epochs me WHERE me.conversation_id=c.id
					AND me.user_id=$1 AND me.left_at IS NULL) THEN g.owner_id ELSE NULL END,
				'announcement',CASE WHEN EXISTS (SELECT 1 FROM member_epochs me WHERE me.conversation_id=c.id
					AND me.user_id=$1 AND me.left_at IS NULL) THEN g.announcement ELSE NULL END,
				'members_can_invite',g.members_can_invite,'mute_all',g.mute_all) ELSE NULL END,
			'membership_epochs',COALESCE((SELECT jsonb_agg(jsonb_build_object(
				'epoch',me.epoch,'role',me.role,'visible_from_seq',me.visible_from_seq::text,
				'visible_through_seq',me.visible_through_seq::text,'my_nickname',me.my_nickname,
				'joined_at',me.joined_at,'left_at',me.left_at,'mute_until',me.mute_until,
				'leave_reason',me.leave_reason) ORDER BY me.epoch) FROM member_epochs me
				WHERE me.conversation_id=c.id AND me.user_id=$1),'[]'::jsonb))
			FROM conversations c LEFT JOIN direct_pairs dp ON dp.conversation_id=c.id
			LEFT JOIN group_settings g ON g.conversation_id=c.id
			LEFT JOIN conversation_user_state st ON st.conversation_id=c.id AND st.user_id=$1
			WHERE dp.user_low=$1 OR dp.user_high=$1 OR EXISTS
				(SELECT 1 FROM member_epochs me WHERE me.conversation_id=c.id AND me.user_id=$1)
			ORDER BY c.id`},
	}
	entries := make([]snapshotEntry, 0)
	for _, query := range queries {
		rows, err := tx.Query(ctx, query.sql, user)
		if err != nil {
			return nil, err
		}
		for rows.Next() {
			entry := snapshotEntry{Type: query.kind}
			if err := rows.Scan(&entry.ID, &entry.Payload); err != nil {
				rows.Close()
				return nil, err
			}
			entries = append(entries, entry)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return nil, err
		}
	}
	return entries, nil
}
