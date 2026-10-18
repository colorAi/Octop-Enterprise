// Package groups exposes group metadata and membership views. Message endpoints live in internal/chat and share the same
// epoch visibility rules.
package groups

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/jackc/pgx/v5"

	"octop/communication/internal/auth"
	"octop/communication/internal/deps"
	"octop/communication/internal/httpx"
	"octop/communication/internal/policy"
	"octop/communication/internal/store"
)

// Service wires dependencies.
type Service struct{ D *deps.Deps }

// New constructs the service.
func New(d *deps.Deps) *Service { return &Service{D: d} }

// Register mounts group routes.
func (s *Service) Register(m *http.ServeMux) {
	a := func(h func(http.ResponseWriter, *http.Request)) http.HandlerFunc {
		return auth.Middleware(s.D, h)
	}
	m.HandleFunc("GET /api/app/v1/groups", a(s.handleList))
	m.HandleFunc("POST /api/app/v1/groups", a(s.handleCreate))
	m.HandleFunc("PATCH /api/app/v1/groups/{id}", a(s.handlePatch))
	m.HandleFunc("POST /api/app/v1/groups/{id}/members", a(s.handleMembersChange))
	m.HandleFunc("DELETE /api/app/v1/groups/{id}/members", a(s.handleMembersChange))
	m.HandleFunc("POST /api/app/v1/groups/{id}/leave", a(s.handleMembersChange))
	m.HandleFunc("PUT /api/app/v1/groups/{id}/owner", a(s.handleOwner))
	m.HandleFunc("PATCH /api/app/v1/groups/{id}/members/{user_id}", a(s.handleMemberPatch))
	m.HandleFunc("POST /api/app/v1/groups/{id}/archive", a(s.handleArchive))
	org := func(h http.HandlerFunc) http.HandlerFunc { return auth.Require(s.D, "departments", h) }
	m.HandleFunc("POST /api/app/v1/organization/company-group", org(s.handleCreate))
	m.HandleFunc("GET /api/app/v1/organization/groups", org(s.handleList))
	m.HandleFunc("GET /api/app/v1/organization/groups/{id}", org(s.handleGet))
	m.HandleFunc("PATCH /api/app/v1/organization/groups/{id}", org(s.handlePatch))
	m.HandleFunc("POST /api/app/v1/organization/groups/{id}/members", org(s.handleMembersChange))
	m.HandleFunc("DELETE /api/app/v1/organization/groups/{id}/members", org(s.handleMembersChange))
	m.HandleFunc("PATCH /api/app/v1/organization/groups/{id}/members/{user_id}", org(s.handleMemberPatch))
	m.HandleFunc("PUT /api/app/v1/organization/groups/{id}/owner", org(s.handleOwner))
	m.HandleFunc("POST /api/app/v1/organization/groups/{id}/archive", org(s.handleArchive))
	m.HandleFunc("GET /api/app/v1/groups/{id}", a(s.handleGet))
	m.HandleFunc("GET /api/app/v1/groups/{id}/members", a(s.handleMembers))
	m.HandleFunc("PATCH /api/app/v1/groups/{id}/my-membership", a(s.handleMyMembership))
}

// memberInfo is the caller's membership view of a group conversation.
type memberInfo struct {
	Kind         string
	Active       bool
	Role         string
	OwnerID      string
	Name         string
	Announcement string
	Status       string
	MuteAll      bool
	CanInvite    bool
	MuteUntil    *time.Time
	Epoch        int
	VisibleFrom  int64
}

func (s *Service) membership(ctx context.Context, q queryable, convID, user string) (mi memberInfo, found bool, err error) {
	err = q.QueryRow(ctx, `SELECT c.status, g.owner_id, g.name, g.mute_all, g.members_can_invite, g.group_kind, g.announcement
		FROM conversations c JOIN group_settings g ON g.conversation_id=c.id WHERE c.id=$1`, convID).
		Scan(&mi.Status, &mi.OwnerID, &mi.Name, &mi.MuteAll, &mi.CanInvite, &mi.Kind, &mi.Announcement)
	if errors.Is(err, pgx.ErrNoRows) {
		return mi, false, nil
	}
	if err != nil {
		return mi, false, httpx.ErrStorage
	}
	err = q.QueryRow(ctx, `SELECT role, epoch, visible_from_seq, mute_until FROM member_epochs
		WHERE conversation_id=$1 AND user_id=$2 AND left_at IS NULL`, convID, user).
		Scan(&mi.Role, &mi.Epoch, &mi.VisibleFrom, &mi.MuteUntil)
	if errors.Is(err, pgx.ErrNoRows) {
		return mi, true, nil // group exists, not an active member
	}
	if err != nil {
		return mi, true, httpx.ErrStorage
	}
	mi.Active = true
	return mi, true, nil
}

type queryable interface {
	QueryRow(context.Context, string, ...any) pgx.Row
}

func (s *Service) activeMembers(ctx context.Context, q interface {
	Query(context.Context, string, ...any) (pgx.Rows, error)
}, convID string) ([]string, error) {
	rows, err := q.Query(ctx, `SELECT user_id FROM member_epochs WHERE conversation_id=$1 AND left_at IS NULL ORDER BY user_id`, convID)
	if err != nil {
		return nil, httpx.ErrStorage
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var u string
		if err := rows.Scan(&u); err != nil {
			return nil, httpx.ErrStorage
		}
		out = append(out, u)
	}
	return out, rows.Err()
}

func (s *Service) handleGet(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	mi, found, err := s.membership(r.Context(), s.D.Store.Pool, r.PathValue("id"), a.UserID)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if !found {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	if !mi.Active && !organizationRequest(r) {
		httpx.WriteError(w, r, httpx.ErrForbidden.WithMessage("不是当前群成员"))
		return
	}
	var avatar *string
	var ann string
	err = s.D.Store.Pool.QueryRow(r.Context(), `SELECT announcement, avatar_asset_id FROM group_settings WHERE conversation_id=$1`, r.PathValue("id")).Scan(&ann, &avatar)
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	data := map[string]any{
		"group_kind": mi.Kind, "is_member": mi.Active,
		"conversation_id": r.PathValue("id"), "name": mi.Name, "announcement": ann, "avatar_asset_id": avatar,
		"owner_user_id": mi.OwnerID, "my_role": mi.Role, "status": mi.Status,
		"members_can_invite": mi.CanInvite, "mute_all": mi.MuteAll,
	}
	if organizationRequest(r) {
		members, err := s.memberViews(r.Context(), r.PathValue("id"))
		if err != nil {
			httpx.WriteErrorOf(w, r, err)
			return
		}
		data["members"] = members
	}
	httpx.WriteData(w, r, 200, data)
}

func (s *Service) handleMembers(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	convID := r.PathValue("id")
	mi, found, err := s.membership(r.Context(), s.D.Store.Pool, convID, a.UserID)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if !found || !mi.Active {
		httpx.WriteError(w, r, httpx.ErrForbidden.WithMessage("不是当前群成员"))
		return
	}
	items, err := s.memberViews(r.Context(), convID)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if mi.Role != "owner" && mi.Role != "admin" && !auth.Has(a.Permissions, "departments") {
		for _, item := range items {
			for _, key := range []string{"role", "membership_source", "granted_by", "mute_until", "joined_at"} {
				delete(item, key)
			}
		}
	}
	httpx.WriteData(w, r, 200, map[string]any{"items": items, "count": len(items), "status": mi.Status})
}

func (s *Service) memberViews(ctx context.Context, conv string) ([]map[string]any, error) {
	rows, err := s.D.Store.Pool.Query(ctx, `SELECT me.user_id,me.role,me.joined_at,me.my_nickname,u.nickname,u.avatar_asset_id,u.status,me.mute_until,
        CASE WHEN g.group_kind='company' OR EXISTS(SELECT 1 FROM department_groups dg JOIN departments d ON d.id=dg.department_id AND d.enabled JOIN department_members dm ON dm.department_id=d.id WHERE dg.conversation_id=me.conversation_id AND dm.user_id=me.user_id) THEN 'automatic' ELSE 'invited' END,gr.granted_by,u.avatar_icon,u.avatar_url,u.job_title_id,j.name
        FROM member_epochs me JOIN users u ON u.id=me.user_id LEFT JOIN job_titles j ON j.id=u.job_title_id JOIN group_settings g ON g.conversation_id=me.conversation_id LEFT JOIN group_member_grants gr ON gr.conversation_id=me.conversation_id AND gr.user_id=me.user_id
        WHERE me.conversation_id=$1 AND me.left_at IS NULL ORDER BY (me.role='owner') DESC,me.joined_at,me.user_id`, conv)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := []map[string]any{}
	for rows.Next() {
		var uid, role, nick, myNick, status, source, icon, avatarURL string
		var joined time.Time
		var until *time.Time
		var avatar, grantedBy *string
		var titleID, title *string
		if err := rows.Scan(&uid, &role, &joined, &myNick, &nick, &avatar, &status, &until, &source, &grantedBy, &icon, &avatarURL, &titleID, &title); err != nil {
			return nil, err
		}
		items = append(items, map[string]any{"user_id": uid, "role": role, "nickname": nick, "my_nickname": myNick, "avatar_asset_id": avatar, "avatar_icon": icon, "avatar_url": avatarURL, "job_title_id": titleID, "job_title": title, "joined_at": joined.UTC().Format(time.RFC3339), "archived": status != "active", "mute_until": until, "membership_source": source, "granted_by": grantedBy})
	}
	return items, rows.Err()
}

func (s *Service) handleMyMembership(w http.ResponseWriter, r *http.Request) {
	var req struct {
		MyNickname string `json:"my_nickname"`
	}
	if err := httpx.DecodeJSON(w, r, &req, 1<<10); err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if len([]rune(req.MyNickname)) > 16 {
		httpx.WriteError(w, r, httpx.ErrInvalidRequest)
		return
	}
	a, _ := auth.From(r)
	id := r.PathValue("id")
	err := s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		if !policy.Active(r.Context(), tx, a.UserID) {
			return httpx.ErrAccountDisabled
		}
		tag, e := tx.Exec(r.Context(), `UPDATE member_epochs SET my_nickname=$3 WHERE conversation_id=$1 AND user_id=$2 AND left_at IS NULL`, id, a.UserID, req.MyNickname)
		if e != nil {
			return e
		}
		if tag.RowsAffected() == 0 {
			return httpx.ErrForbidden
		}
		members, e := s.activeMembers(r.Context(), tx, id)
		if e != nil {
			return e
		}
		for _, u := range members {
			if _, e = store.EmitSync(r.Context(), tx, u, "group.members.changed", id, 0, map[string]string{"conversation_id": id}); e != nil {
				return e
			}
		}
		return nil
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	httpx.WriteData(w, r, 200, map[string]string{"my_nickname": req.MyNickname})
}

func (s *Service) wake(userIDs ...string) {
	if s.D.Realtime != nil {
		s.D.Realtime.Wakeup(userIDs...)
	}
}

// Lists membership independently of conversation visibility; management never implies membership.
func (s *Service) handleList(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	kind := r.URL.Query().Get("kind")
	if kind != "" && kind != "company" && kind != "department" && kind != "project" {
		httpx.WriteError(w, r, httpx.ErrInvalidRequest)
		return
	}
	rows, err := s.D.Store.Pool.Query(r.Context(), `SELECT g.conversation_id,g.name,g.group_kind,g.owner_id,g.announcement,g.mute_all,g.members_can_invite,c.status,me.role,
        (SELECT count(*) FROM member_epochs m WHERE m.conversation_id=g.conversation_id AND m.left_at IS NULL)
        FROM group_settings g JOIN conversations c ON c.id=g.conversation_id LEFT JOIN member_epochs me ON me.conversation_id=c.id AND me.user_id=$1 AND me.left_at IS NULL
        WHERE ($2 OR (me.id IS NOT NULL AND c.status='active')) AND ($3='' OR g.group_kind=$3)
        ORDER BY (g.group_kind='company') DESC,c.updated_at DESC,c.id`, a.UserID, organizationRequest(r), kind)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	defer rows.Close()
	items := []map[string]any{}
	for rows.Next() {
		var conv, name, kind, owner, ann, status string
		var muted, invite bool
		var role *string
		var count int
		if err := rows.Scan(&conv, &name, &kind, &owner, &ann, &muted, &invite, &status, &role, &count); err != nil {
			httpx.WriteErrorOf(w, r, err)
			return
		}
		items = append(items, map[string]any{"conversation_id": conv, "name": name, "group_kind": kind, "owner_user_id": owner, "announcement": ann, "mute_all": muted, "members_can_invite": invite, "status": status, "my_role": role, "is_member": role != nil, "member_count": count})
	}
	if rows.Err() != nil {
		httpx.WriteErrorOf(w, r, rows.Err())
		return
	}
	httpx.WriteData(w, r, 200, map[string]any{"items": items})
}
