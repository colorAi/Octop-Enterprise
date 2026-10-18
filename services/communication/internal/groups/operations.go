package groups

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"octop/communication/internal/auth"
	"octop/communication/internal/httpx"
	"octop/communication/internal/idgen"
	"octop/communication/internal/policy"
	"octop/communication/internal/store"
)

type mutation struct {
	RequestID        string          `json:"request_id"`
	Name             *string         `json:"name,omitempty"`
	Announcement     *string         `json:"announcement,omitempty"`
	MuteAll          *bool           `json:"mute_all,omitempty"`
	MembersCanInvite *bool           `json:"members_can_invite,omitempty"`
	UserIDs          []string        `json:"user_ids,omitempty"`
	UserID           string          `json:"user_id,omitempty"`
	Role             *string         `json:"role,omitempty"`
	MuteUntil        json.RawMessage `json:"mute_until,omitempty"`
}

func decodeMutation(w http.ResponseWriter, r *http.Request) (mutation, bool) {
	var v mutation
	if err := httpx.DecodeJSON(w, r, &v, 64<<10); err != nil {
		httpx.WriteErrorOf(w, r, err)
		return v, false
	}
	return v, true
}
func organizationRequest(r *http.Request) bool {
	return strings.HasPrefix(r.URL.Path, "/api/app/v1/organization/")
}

// operate shares the established organization -> conversation -> sync lock order.
// Receipts include the full path and input so reusing an ID for another operation fails.
func (s *Service) operate(w http.ResponseWriter, r *http.Request, v mutation, create bool, fn func(pgx.Tx, auth.Actor, memberInfo) (map[string]any, error)) {
	a, _ := auth.From(r)
	legacy := organizationRequest(r)
	if v.RequestID == "" && legacy {
		v.RequestID = idgen.New("op")
	}
	if strings.TrimSpace(v.RequestID) == "" || len(v.RequestID) > 128 {
		httpx.WriteError(w, r, httpx.ErrInvalidRequest)
		return
	}
	body, _ := json.Marshal(v)
	digest := sha256.Sum256(append([]byte(r.Method+":"+r.URL.Path+":"), body...))
	scope := "group.mutate"
	if create {
		scope = "group.create"
	}
	var out map[string]any
	err := s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		var valid bool
		perm := "messaging"
		if legacy {
			perm = "departments"
		}
		if e := tx.QueryRow(r.Context(), `SELECT status='active' AND identity_version=$2 AND permissions ? $3 FROM users WHERE id=$1`, a.UserID, a.Version, perm).Scan(&valid); e != nil {
			return e
		}
		if !valid {
			return httpx.ErrForbidden
		}
		if create && !legacy && !auth.Has(a.Permissions, "groups_create") {
			return httpx.ErrForbidden
		}
		var previous []byte
		e := tx.QueryRow(r.Context(), `SELECT content_digest,result FROM operation_receipts WHERE actor_id=$1 AND scope=$2 AND idem_key=$3`, a.UserID, scope, v.RequestID).Scan(&previous, &out)
		if e == nil {
			if !bytes.Equal(previous, digest[:]) {
				return httpx.ErrIdemConflict
			}
			return nil
		}
		if !errors.Is(e, pgx.ErrNoRows) {
			return e
		}
		var mi memberInfo
		if !create {
			if _, e = store.LockConversation(r.Context(), tx, r.PathValue("id")); e != nil {
				if errors.Is(e, pgx.ErrNoRows) {
					return httpx.ErrNotFound
				}
				return e
			}
			var found bool
			mi, found, e = s.membership(r.Context(), tx, r.PathValue("id"), a.UserID)
			if e != nil {
				return e
			}
			if !found {
				return httpx.ErrNotFound
			}
			if !legacy && !mi.Active {
				return httpx.ErrForbidden
			}
			if mi.Status != "active" {
				return httpx.ErrInvalidState
			}
		}
		out, e = fn(tx, a, mi)
		if e != nil {
			return e
		}
		result, e := json.Marshal(out)
		if e != nil {
			return e
		}
		_, e = tx.Exec(r.Context(), `INSERT INTO operation_receipts(id,actor_id,scope,idem_key,content_digest,result) VALUES($1,$2,$3,$4,$5,$6)`, idgen.New("receipt"), a.UserID, scope, v.RequestID, digest[:], result)
		return e
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	httpx.WriteData(w, r, 200, out)
}

func audit(ctx context.Context, tx pgx.Tx, actor, action, object, requestID string, before, after any) error {
	b, e := json.Marshal(before)
	if e != nil {
		return e
	}
	v, e := json.Marshal(after)
	if e != nil {
		return e
	}
	_, e = tx.Exec(ctx, `INSERT INTO organization_audit(id,actor_id,action,object_id,request_id,before_data,after_data) VALUES($1,$2,$3,$4,$5,$6,$7)`, idgen.New("audit"), actor, action, object, requestID, b, v)
	return e
}

func (s *Service) changed(ctx context.Context, tx pgx.Tx, conv, kind string) error {
	if _, e := tx.Exec(ctx, `UPDATE group_settings SET permission_version=permission_version+1,updated_at=now() WHERE conversation_id=$1`, conv); e != nil {
		return e
	}
	seq, e := store.NextSeq(ctx, tx, conv)
	if e != nil {
		return e
	}
	msg := idgen.New("msg")
	if _, e = tx.Exec(ctx, `INSERT INTO messages(id,conversation_id,seq,type,text,system_kind) VALUES($1,$2,$3,'system','',$4)`, msg, conv, seq, kind); e != nil {
		return e
	}
	members, e := s.activeMembers(ctx, tx, conv)
	if e != nil {
		return e
	}
	for _, u := range members {
		if _, e = store.EmitSync(ctx, tx, u, "group.update", conv, seq, map[string]string{"conversation_id": conv}); e != nil {
			return e
		}
		if _, e = store.EmitSync(ctx, tx, u, "message.new", msg, seq, map[string]string{"conversation_id": conv, "message_id": msg, "seq": store.BigStr(seq)}); e != nil {
			return e
		}
	}
	return nil
}

func (s *Service) handleCreate(w http.ResponseWriter, r *http.Request) {
	v, ok := decodeMutation(w, r)
	if !ok {
		return
	}
	company := organizationRequest(r)
	s.operate(w, r, v, true, func(tx pgx.Tx, a auth.Actor, _ memberInfo) (map[string]any, error) {
		ctx := r.Context()
		kind := "project"
		name := ""
		if company {
			kind = "company"
			name = "公司总群"
			var existing string
			e := tx.QueryRow(ctx, `SELECT conversation_id FROM company_group WHERE singleton`).Scan(&existing)
			if e == nil {
				return map[string]any{"conversation_id": existing}, nil
			}
			if !errors.Is(e, pgx.ErrNoRows) {
				return nil, e
			}
		}
		if v.Name != nil {
			name = strings.TrimSpace(*v.Name)
		}
		if name == "" || len([]rune(name)) > 100 {
			return nil, httpx.ErrInvalidRequest
		}
		members := map[string]bool{a.UserID: true}
		for _, u := range v.UserIDs {
			members[u] = true
		}
		if !company {
			if len(members) > s.D.Cfg.Limits.GroupMaxMembers {
				return nil, httpx.ErrGroupFull
			}
			for u := range members {
				if !policy.Active(ctx, tx, u) {
					return nil, httpx.ErrInvalidRequest
				}
			}
		} else {
			people, e := ids(ctx, tx, `SELECT id FROM users WHERE status='active' AND permissions ? 'messaging' ORDER BY id`)
			if e != nil {
				return nil, e
			}
			if len(people) == 0 {
				return nil, httpx.ErrInvalidState
			}
			if len(people) > s.D.Cfg.Limits.GroupMaxMembers {
				return nil, httpx.ErrGroupFull
			}
			if !policy.Active(ctx, tx, a.UserID) {
				a.UserID = people[0]
			}
		}
		conv := idgen.New("conv")
		actor, _ := auth.From(r)
		if _, e := tx.Exec(ctx, `INSERT INTO conversations(id,type,created_by) VALUES($1,'group',$2)`, conv, actor.UserID); e != nil {
			return nil, e
		}
		if _, e := tx.Exec(ctx, `INSERT INTO group_settings(conversation_id,owner_id,name,group_kind) VALUES($1,$2,$3,$4)`, conv, a.UserID, name, kind); e != nil {
			return nil, e
		}
		if company {
			if _, e := tx.Exec(ctx, `INSERT INTO company_group(singleton,conversation_id) VALUES(true,$1)`, conv); e != nil {
				return nil, e
			}
		} else {
			for u := range members {
				if _, e := tx.Exec(ctx, `INSERT INTO group_member_grants(conversation_id,user_id,granted_by) VALUES($1,$2,$3)`, conv, u, a.UserID); e != nil {
					return nil, e
				}
			}
		}
		if e := s.Reconcile(ctx, tx, conv); e != nil {
			return nil, e
		}
		if e := audit(ctx, tx, actor.UserID, "group.create", conv, v.RequestID, nil, map[string]any{"name": name, "group_kind": kind, "user_ids": v.UserIDs}); e != nil {
			return nil, e
		}
		return map[string]any{"conversation_id": conv}, nil
	})
}

func manager(r *http.Request, mi memberInfo) bool {
	return organizationRequest(r) || mi.Role == "owner" || mi.Role == "admin"
}
func owner(r *http.Request, mi memberInfo) bool { return organizationRequest(r) || mi.Role == "owner" }
func (s *Service) handlePatch(w http.ResponseWriter, r *http.Request) {
	v, ok := decodeMutation(w, r)
	if !ok {
		return
	}
	s.operate(w, r, v, false, func(tx pgx.Tx, a auth.Actor, mi memberInfo) (map[string]any, error) {
		if !manager(r, mi) {
			return nil, httpx.ErrForbidden
		}
		if v.Name != nil && (strings.TrimSpace(*v.Name) == "" || len([]rune(*v.Name)) > 100) || v.Announcement != nil && len([]rune(*v.Announcement)) > 500 {
			return nil, httpx.ErrInvalidRequest
		}
		if v.MembersCanInvite != nil && mi.Kind != "project" {
			return nil, httpx.ErrInvalidState
		}
		conv := r.PathValue("id")
		if _, e := tx.Exec(r.Context(), `UPDATE group_settings SET name=COALESCE($2,name),announcement=COALESCE($3,announcement),mute_all=COALESCE($4,mute_all),members_can_invite=COALESCE($5,members_can_invite) WHERE conversation_id=$1`, conv, v.Name, v.Announcement, v.MuteAll, v.MembersCanInvite); e != nil {
			return nil, e
		}
		if e := s.changed(r.Context(), tx, conv, "group.settings.changed"); e != nil {
			return nil, e
		}
		return map[string]any{"updated": true}, audit(r.Context(), tx, a.UserID, "group.update", conv, v.RequestID, mi, v)
	})
}
func automatic(ctx context.Context, tx pgx.Tx, conv, user string) (bool, error) {
	var yes bool
	e := tx.QueryRow(ctx, `SELECT g.group_kind='company' OR EXISTS(SELECT 1 FROM department_groups dg JOIN departments d ON d.id=dg.department_id AND d.enabled JOIN department_members dm ON dm.department_id=d.id WHERE dg.conversation_id=g.conversation_id AND dm.user_id=$2) FROM group_settings g WHERE g.conversation_id=$1`, conv, user).Scan(&yes)
	return yes, e
}
func (s *Service) handleMembersChange(w http.ResponseWriter, r *http.Request) {
	v, ok := decodeMutation(w, r)
	if !ok {
		return
	}
	remove := r.Method == "DELETE"
	leave := strings.HasSuffix(r.URL.Path, "/leave")
	if !leave && (len(v.UserIDs) == 0 || len(v.UserIDs) > s.D.Cfg.Limits.GroupMaxMembers) {
		httpx.WriteError(w, r, httpx.ErrInvalidRequest)
		return
	}
	s.operate(w, r, v, false, func(tx pgx.Tx, a auth.Actor, mi memberInfo) (map[string]any, error) {
		conv := r.PathValue("id")
		ctx := r.Context()
		users := v.UserIDs
		if leave {
			if mi.Kind != "project" || mi.Role == "owner" {
				return nil, httpx.ErrInvalidState
			}
			users = []string{a.UserID}
			remove = true
		} else if !manager(r, mi) && (remove || mi.Kind != "project" || !mi.CanInvite) {
			return nil, httpx.ErrForbidden
		}
		for _, u := range users {
			if remove {
				auto, e := automatic(ctx, tx, conv, u)
				if e != nil {
					return nil, e
				}
				if auto {
					return nil, httpx.ErrInvalidState
				}
				if u == mi.OwnerID {
					return nil, httpx.ErrInvalidState
				}
				var role string
				e = tx.QueryRow(ctx, `SELECT role FROM member_epochs WHERE conversation_id=$1 AND user_id=$2 AND left_at IS NULL`, conv, u).Scan(&role)
				if e != nil && !errors.Is(e, pgx.ErrNoRows) {
					return nil, e
				}
				if !leave && role == "admin" && !owner(r, mi) {
					return nil, httpx.ErrForbidden
				}
				if _, e = tx.Exec(ctx, `DELETE FROM group_member_grants WHERE conversation_id=$1 AND user_id=$2`, conv, u); e != nil {
					return nil, e
				}
			} else {
				if !policy.Active(ctx, tx, u) {
					return nil, httpx.ErrInvalidRequest
				}
				if _, e := tx.Exec(ctx, `INSERT INTO group_member_grants(conversation_id,user_id,granted_by) VALUES($1,$2,$3) ON CONFLICT DO NOTHING`, conv, u, a.UserID); e != nil {
					return nil, e
				}
			}
		}
		if e := s.Reconcile(ctx, tx, conv); e != nil {
			return nil, e
		}
		return map[string]any{"updated": true}, audit(ctx, tx, a.UserID, "group.members", conv, v.RequestID, nil, map[string]any{"user_ids": users, "removed": remove})
	})
}
func (s *Service) handleOwner(w http.ResponseWriter, r *http.Request) {
	v, ok := decodeMutation(w, r)
	if !ok {
		return
	}
	s.operate(w, r, v, false, func(tx pgx.Tx, a auth.Actor, mi memberInfo) (map[string]any, error) {
		if !owner(r, mi) {
			return nil, httpx.ErrForbidden
		}
		conv := r.PathValue("id")
		ctx := r.Context()
		var eligible bool
		if e := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM member_epochs me JOIN users u ON u.id=me.user_id WHERE me.conversation_id=$1 AND me.user_id=$2 AND me.left_at IS NULL AND u.status='active' AND u.permissions ? 'messaging')`, conv, v.UserID).Scan(&eligible); e != nil {
			return nil, e
		}
		if !eligible {
			return nil, httpx.ErrInvalidRequest
		}
		if mi.OwnerID == v.UserID {
			return map[string]any{"updated": true}, nil
		}
		if _, e := tx.Exec(ctx, `UPDATE member_epochs SET role=CASE WHEN user_id=$2 THEN 'owner' WHEN role='owner' THEN 'member' ELSE role END WHERE conversation_id=$1 AND left_at IS NULL`, conv, v.UserID); e != nil {
			return nil, e
		}
		if _, e := tx.Exec(ctx, `UPDATE group_settings SET owner_id=$2 WHERE conversation_id=$1`, conv, v.UserID); e != nil {
			return nil, e
		}
		if e := s.changed(ctx, tx, conv, "group.owner.changed"); e != nil {
			return nil, e
		}
		return map[string]any{"updated": true}, audit(ctx, tx, a.UserID, "group.owner", conv, v.RequestID, mi.OwnerID, v.UserID)
	})
}
func (s *Service) handleMemberPatch(w http.ResponseWriter, r *http.Request) {
	v, ok := decodeMutation(w, r)
	if !ok {
		return
	}
	if v.Role == nil && v.MuteUntil == nil || v.Role != nil && *v.Role != "admin" && *v.Role != "member" {
		httpx.WriteError(w, r, httpx.ErrInvalidRequest)
		return
	}
	s.operate(w, r, v, false, func(tx pgx.Tx, a auth.Actor, mi memberInfo) (map[string]any, error) {
		if !manager(r, mi) {
			return nil, httpx.ErrForbidden
		}
		conv, u := r.PathValue("id"), r.PathValue("user_id")
		ctx := r.Context()
		var role string
		var until *time.Time
		if e := tx.QueryRow(ctx, `SELECT role,mute_until FROM member_epochs WHERE conversation_id=$1 AND user_id=$2 AND left_at IS NULL`, conv, u).Scan(&role, &until); e != nil {
			if errors.Is(e, pgx.ErrNoRows) {
				return nil, httpx.ErrNotFound
			}
			return nil, e
		}
		before := map[string]any{"role": role, "mute_until": until}
		if role == "owner" {
			return nil, httpx.ErrInvalidState
		}
		if (v.Role != nil || role == "admin") && !owner(r, mi) {
			return nil, httpx.ErrForbidden
		}
		if v.Role != nil {
			role = *v.Role
		}
		if v.MuteUntil != nil {
			if json.Unmarshal(v.MuteUntil, &until) != nil || until != nil && until.Before(time.Now()) {
				return nil, httpx.ErrInvalidRequest
			}
		}
		if _, e := tx.Exec(ctx, `UPDATE member_epochs SET role=$3,mute_until=$4 WHERE conversation_id=$1 AND user_id=$2 AND left_at IS NULL`, conv, u, role, until); e != nil {
			return nil, e
		}
		if e := s.changed(ctx, tx, conv, "group.member.changed"); e != nil {
			return nil, e
		}
		return map[string]any{"updated": true}, audit(ctx, tx, a.UserID, "group.member", conv+":"+u, v.RequestID, before, map[string]any{"role": role, "mute_until": until})
	})
}
func (s *Service) handleArchive(w http.ResponseWriter, r *http.Request) {
	v, ok := decodeMutation(w, r)
	if !ok {
		return
	}
	s.operate(w, r, v, false, func(tx pgx.Tx, a auth.Actor, mi memberInfo) (map[string]any, error) {
		if mi.Kind != "project" {
			return nil, httpx.ErrInvalidState
		}
		if !owner(r, mi) {
			return nil, httpx.ErrForbidden
		}
		conv := r.PathValue("id")
		ctx := r.Context()
		if e := s.changed(ctx, tx, conv, "group.archived"); e != nil {
			return nil, e
		}
		if _, e := tx.Exec(ctx, `UPDATE conversations SET status='archived',updated_at=now() WHERE id=$1`, conv); e != nil {
			return nil, e
		}
		return map[string]any{"updated": true}, audit(ctx, tx, a.UserID, "group.archive", conv, v.RequestID, "active", "archived")
	})
}
