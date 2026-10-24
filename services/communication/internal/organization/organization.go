// Package organization owns departments and compulsory group membership.
package organization

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/jackc/pgx/v5"
	"net/http"
	"octop/communication/internal/auth"
	"octop/communication/internal/deps"
	"octop/communication/internal/groups"
	"octop/communication/internal/httpx"
	"octop/communication/internal/idgen"
	"octop/communication/internal/store"
	"strings"
)

type Service struct{ D *deps.Deps }

func New(d *deps.Deps) *Service { return &Service{d} }
func (s *Service) Register(m *http.ServeMux) {
	m.HandleFunc("GET /api/app/v1/directory-settings", auth.Require(s.D, "contacts", s.directorySettings))
	m.HandleFunc("GET /api/app/v1/organization/directory-settings", auth.Require(s.D, "departments", s.directorySettings))
	m.HandleFunc("PUT /api/app/v1/organization/directory-settings", auth.Require(s.D, "departments", s.saveDirectorySettings))
	m.HandleFunc("GET /api/app/v1/organization/job-titles", auth.Require(s.D, "departments", s.jobTitles))
	m.HandleFunc("POST /api/app/v1/organization/job-titles", auth.Require(s.D, "departments", s.createJobTitle))
	m.HandleFunc("PUT /api/app/v1/organization/users/{id}/job-title", auth.Require(s.D, "departments", s.assignJobTitle))
	m.HandleFunc("GET /api/app/v1/organization/users", auth.Require(s.D, "departments", s.users))
	m.HandleFunc("PUT /internal/identities/{id}", auth.Credential(s.D, "identity", s.identity))
	m.HandleFunc("GET /internal/resource-membership/{id}", auth.Credential(s.D, "resource-policy", s.resourceMembership))
	m.HandleFunc("POST /internal/resource-memberships", auth.Credential(s.D, "resource-directory", s.resourceMemberships))
	m.HandleFunc("POST /internal/resource-membership/{id}/resolve", auth.Credential(s.D, "resource-policy", s.resolveResourceMove))
	m.HandleFunc("GET /api/app/v1/contacts", auth.Require(s.D, "contacts", s.contacts))
	m.HandleFunc("GET /api/app/v1/presence", auth.Require(s.D, "contacts", s.presence))
	m.HandleFunc("GET /api/app/v1/departments", auth.Require(s.D, "contacts", s.departments))
	m.HandleFunc("GET /api/app/v1/organization/departments", auth.Require(s.D, "departments", s.departments))
	m.HandleFunc("POST /api/app/v1/organization/departments", auth.Require(s.D, "departments", s.create))
	m.HandleFunc("PATCH /api/app/v1/organization/departments/{id}", auth.Require(s.D, "departments", s.patch))
	m.HandleFunc("PUT /api/app/v1/organization/users/{id}/department", auth.Require(s.D, "departments", s.assign))
	m.HandleFunc("POST /api/app/v1/organization/departments/{id}/group", auth.Require(s.D, "departments", s.group))
}
func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	e := httpx.DecodeJSON(w, r, v, 16<<10)
	if e != nil {
		httpx.WriteErrorOf(w, r, e)
	}
	return e == nil
}
func result(w http.ResponseWriter, r *http.Request, data any, e error) {
	if e != nil {
		httpx.WriteErrorOf(w, r, e)
	} else {
		httpx.WriteData(w, r, 200, data)
	}
}
func (s *Service) tx(r *http.Request, fn func(pgx.Tx) error) error {
	a, _ := auth.From(r)
	return s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		var ok bool
		if e := tx.QueryRow(r.Context(), `SELECT status='active' AND identity_version=$2 AND permissions ? 'departments' FROM users WHERE id=$1`, a.UserID, a.Version).Scan(&ok); e != nil || !ok {
			return httpx.ErrForbidden
		}
		return fn(tx)
	})
}
func audit(ctx context.Context, tx pgx.Tx, r *http.Request, action, id string, before, after any) error {
	a, _ := auth.From(r)
	b, e := json.Marshal(before)
	if e != nil {
		return e
	}
	v, e := json.Marshal(after)
	if e != nil {
		return e
	}
	_, e = tx.Exec(ctx, `INSERT INTO organization_audit(id,actor_id,action,object_id,before_data,after_data,request_id)VALUES($1,$2,$3,$4,$5,$6,$7)`, idgen.New("audit"), a.UserID, action, id, b, v, httpx.RequestID(r.Context()))
	return e
}
func (s *Service) identity(w http.ResponseWriter, r *http.Request, c auth.Claims) {
	var v struct {
		UserID      string   `json:"user_id"`
		Version     int64    `json:"version"`
		Nickname    string   `json:"nickname"`
		AvatarIcon  string   `json:"avatar_icon"`
		AvatarURL   *string  `json:"avatar_url"`
		Status      string   `json:"status"`
		Permissions []string `json:"permissions"`
		IsAdmin     *bool    `json:"is_admin"`
	}
	if !decode(w, r, &v) {
		return
	}
	if v.UserID != r.PathValue("id") || v.UserID != c.UserID || v.Version != c.Version || v.Version < 1 || (v.Status != "active" && v.Status != "disabled" && v.Status != "deleted") {
		result(w, r, nil, httpx.ErrInvalidRequest)
		return
	}
	applied := false
	e := s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		var old string
		var version int64
		e := tx.QueryRow(r.Context(), `SELECT status,identity_version FROM users WHERE id=$1 FOR UPDATE`, v.UserID).Scan(&old, &version)
		if e != nil && !errors.Is(e, pgx.ErrNoRows) {
			return e
		}
		if version > v.Version {
			return nil
		}
		if version == v.Version {
			// Reconciliation backfills fields added after the identity revision was
			// captured. Older bridges omit avatar_url and leave portraits intact.
			tag, err := tx.Exec(r.Context(), `UPDATE users SET is_admin=COALESCE($2,is_admin),avatar_url=COALESCE($4,avatar_url),permissions=CASE WHEN $3 AND NOT (permissions ? 'groups_create') THEN permissions || '["groups_create"]'::jsonb ELSE permissions END
                WHERE id=$1 AND (($2::boolean IS NOT NULL AND is_admin<>$2) OR ($3 AND NOT (permissions ? 'groups_create')) OR ($4::text IS NOT NULL AND avatar_url<>$4))`, v.UserID, v.IsAdmin, v.IsAdmin != nil && *v.IsAdmin && auth.Has(v.Permissions, "groups_create"), v.AvatarURL)
			if err != nil {
				return err
			}
			if tag.RowsAffected() == 0 {
				return nil
			}
			if _, err := store.EmitSync(r.Context(), tx, v.UserID, "profile.updated", v.UserID, v.Version, map[string]string{"user_id": v.UserID}); err != nil {
				return err
			}
			return directoryChanged(r.Context(), tx)
		}
		if old == "deleted" && v.Status != "deleted" {
			return httpx.ErrInvalidState
		}
		p, e := json.Marshal(v.Permissions)
		if e != nil {
			return e
		}
		if _, e = tx.Exec(r.Context(), `INSERT INTO users(id,nickname,avatar_icon,status,identity_version,permissions,is_admin,avatar_url)VALUES($1,$2,$3,$4,$5,$6,COALESCE($7,false),COALESCE($8,'')) ON CONFLICT(id) DO UPDATE SET nickname=excluded.nickname,avatar_icon=excluded.avatar_icon,status=excluded.status,identity_version=excluded.identity_version,permissions=excluded.permissions,is_admin=COALESCE($7,users.is_admin),avatar_url=COALESCE($8,users.avatar_url),updated_at=now()`, v.UserID, v.Nickname, v.AvatarIcon, v.Status, v.Version, p, v.IsAdmin, v.AvatarURL); e != nil {
			return e
		}
		if e = groups.New(s.D).ReconcileAll(r.Context(), tx); e != nil {
			return e
		}
		if _, e = store.EmitSync(r.Context(), tx, v.UserID, "profile.updated", v.UserID, v.Version, map[string]string{"user_id": v.UserID}); e != nil {
			return e
		}
		if e = directoryChanged(r.Context(), tx); e != nil {
			return e
		}
		applied = true
		return nil
	})
	if e == nil && applied && s.D.Realtime != nil {
		s.D.Realtime.Kick(v.UserID)
	}
	result(w, r, map[string]any{"applied": applied, "version": store.BigStr(v.Version)}, e)
}
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
func directoryChanged(ctx context.Context, tx pgx.Tx) error {
	users, e := ids(ctx, tx, `SELECT id FROM users WHERE status='active' AND (permissions ? 'contacts' OR permissions ? 'messaging') ORDER BY id`)
	if e != nil {
		return e
	}
	for _, u := range users {
		if _, e = store.EmitSync(ctx, tx, u, "contacts.changed", "", 0, map[string]bool{"replace": true}); e != nil {
			return e
		}
	}
	return nil
}
func (s *Service) departments(w http.ResponseWriter, r *http.Request) {
	admin := strings.HasPrefix(r.URL.Path, "/api/app/v1/organization/")
	rows, e := s.D.Store.Pool.Query(r.Context(), `SELECT d.id,d.name,d.parent_id,d.sort_order,d.enabled,g.conversation_id,
 (SELECT count(*) FROM department_members dm JOIN users u ON u.id=dm.user_id AND u.status='active' AND NOT u.is_admin WHERE dm.department_id=d.id)
 FROM departments d LEFT JOIN department_groups g ON g.department_id=d.id WHERE $1 OR d.enabled ORDER BY d.sort_order,d.id`, admin)
	if e != nil {
		result(w, r, nil, e)
		return
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		var id, name string
		var parent, conv *string
		var order int
		var count int
		var enabled bool
		if e = rows.Scan(&id, &name, &parent, &order, &enabled, &conv, &count); e != nil {
			result(w, r, nil, e)
			return
		}
		out = append(out, map[string]any{"department_id": id, "name": name, "parent_id": parent, "sort_order": order, "enabled": enabled, "conversation_id": conv, "member_count": count})
	}
	result(w, r, map[string]any{"items": out}, rows.Err())
}
func (s *Service) contacts(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query().Get("q")
	if len([]rune(q)) > 100 {
		result(w, r, nil, httpx.ErrInvalidRequest)
		return
	}
	rows, e := s.D.Store.Pool.Query(r.Context(), `SELECT u.id,u.nickname,u.avatar_icon,d.id,u.permissions ? 'messaging',u.is_admin,u.job_title_id,j.name,u.avatar_url FROM users u LEFT JOIN job_titles j ON j.id=u.job_title_id LEFT JOIN department_members dm ON dm.user_id=u.id LEFT JOIN departments d ON d.id=dm.department_id AND d.enabled WHERE u.status='active' AND strpos(lower(u.nickname),lower($1))>0 ORDER BY u.nickname,u.id`, q)
	if e != nil {
		result(w, r, nil, e)
		return
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		var id, name, icon, avatarURL string
		var dept *string
		var canMessage, isAdmin bool
		var titleID, title *string
		if e = rows.Scan(&id, &name, &icon, &dept, &canMessage, &isAdmin, &titleID, &title, &avatarURL); e != nil {
			result(w, r, nil, e)
			return
		}
		out = append(out, map[string]any{"user_id": id, "nickname": name, "avatar_icon": icon, "avatar_url": avatarURL, "department_id": dept, "can_message": canMessage, "is_admin": isAdmin, "job_title_id": titleID, "job_title": title})
	}
	result(w, r, map[string]any{"items": out}, rows.Err())
}

type Department struct {
	Name    string  `json:"name"`
	Parent  *string `json:"parent_id"`
	Order   int     `json:"sort_order"`
	Enabled bool    `json:"enabled"`
}

func valid(ctx context.Context, tx pgx.Tx, id string, d Department) error {
	if strings.TrimSpace(d.Name) == "" || len([]rune(d.Name)) > 100 {
		return httpx.ErrInvalidRequest
	}
	if d.Parent != nil {
		var bad bool
		e := tx.QueryRow(ctx, `WITH RECURSIVE a AS(SELECT id,parent_id FROM departments WHERE id=$1 UNION ALL SELECT d.id,d.parent_id FROM departments d JOIN a ON d.id=a.parent_id) SELECT NOT EXISTS(SELECT 1 FROM departments WHERE id=$1) OR EXISTS(SELECT 1 FROM a WHERE id=$2)`, *d.Parent, id).Scan(&bad)
		if e != nil {
			return e
		}
		if bad {
			return httpx.ErrInvalidRequest
		}
	}
	return nil
}
func (s *Service) create(w http.ResponseWriter, r *http.Request) {
	v := Department{Enabled: true}
	if !decode(w, r, &v) {
		return
	}
	id := idgen.New("dept")
	e := s.tx(r, func(tx pgx.Tx) error {
		if e := valid(r.Context(), tx, id, v); e != nil {
			return e
		}
		if _, e := tx.Exec(r.Context(), `INSERT INTO departments(id,name,parent_id,sort_order,enabled)VALUES($1,$2,$3,$4,$5)`, id, v.Name, v.Parent, v.Order, v.Enabled); e != nil {
			return e
		}
		if e := directoryChanged(r.Context(), tx); e != nil {
			return e
		}
		return audit(r.Context(), tx, r, "department.create", id, nil, v)
	})
	result(w, r, map[string]string{"department_id": id}, e)
}
func (s *Service) patch(w http.ResponseWriter, r *http.Request) {
	var v struct {
		Name    *string         `json:"name"`
		Parent  json.RawMessage `json:"parent_id"`
		Order   *int            `json:"sort_order"`
		Enabled *bool           `json:"enabled"`
	}
	if !decode(w, r, &v) {
		return
	}
	id := r.PathValue("id")
	e := s.tx(r, func(tx pgx.Tx) error {
		var old Department
		e := tx.QueryRow(r.Context(), `SELECT name,parent_id,sort_order,enabled FROM departments WHERE id=$1`, id).Scan(&old.Name, &old.Parent, &old.Order, &old.Enabled)
		if errors.Is(e, pgx.ErrNoRows) {
			return httpx.ErrNotFound
		}
		if e != nil {
			return e
		}
		next := old
		if v.Name != nil {
			next.Name = *v.Name
		}
		if v.Parent != nil {
			if json.Unmarshal(v.Parent, &next.Parent) != nil {
				return httpx.ErrInvalidRequest
			}
		}
		if v.Order != nil {
			next.Order = *v.Order
		}
		if v.Enabled != nil {
			next.Enabled = *v.Enabled
		}
		if e = valid(r.Context(), tx, id, next); e != nil {
			return e
		}
		if _, e = tx.Exec(r.Context(), `UPDATE departments SET name=$2,parent_id=$3,sort_order=$4,enabled=$5 WHERE id=$1`, id, next.Name, next.Parent, next.Order, next.Enabled); e != nil {
			return e
		}
		if old.Enabled != next.Enabled {
			if _, e = tx.Exec(r.Context(), `UPDATE users SET resource_revision=resource_revision+1 WHERE id IN (SELECT user_id FROM department_members WHERE department_id=$1)`, id); e != nil {
				return e
			}
		}
		if e = groups.New(s.D).ReconcileAll(r.Context(), tx); e != nil {
			return e
		}
		if e = directoryChanged(r.Context(), tx); e != nil {
			return e
		}
		return audit(r.Context(), tx, r, "department.update", id, old, next)
	})
	result(w, r, map[string]bool{"updated": true}, e)
}
func (s *Service) assign(w http.ResponseWriter, r *http.Request) {
	var v struct {
		Department *string `json:"department_id"`
		Revision   *int64  `json:"expected_revision"`
		Operation  string  `json:"operation_id"`
	}
	if !decode(w, r, &v) {
		return
	}
	u := r.PathValue("id")
	e := s.tx(r, func(tx pgx.Tx) error {
		var status string
		var revision int64
		if e := tx.QueryRow(r.Context(), `SELECT status,resource_revision FROM users WHERE id=$1 FOR UPDATE`, u).Scan(&status, &revision); e != nil || status == "deleted" {
			return httpx.ErrNotFound
		}
		if v.Operation != "" {
			if len(v.Operation) > 128 || v.Revision == nil {
				return httpx.ErrInvalidRequest
			}
			var opStatus, opUser string
			var opRevision int64
			var opDepartment *string
			err := tx.QueryRow(r.Context(), `SELECT status,user_id,expected_revision,department_id FROM resource_department_operations WHERE operation_id=$1`, v.Operation).Scan(&opStatus, &opUser, &opRevision, &opDepartment)
			if err == nil {
				sameDepartment := (opDepartment == nil && v.Department == nil) || (opDepartment != nil && v.Department != nil && *opDepartment == *v.Department)
				if opStatus == "complete" && opUser == u && opRevision == *v.Revision && sameDepartment {
					return nil
				}
				return httpx.ErrInvalidState
			}
			if !errors.Is(err, pgx.ErrNoRows) {
				return err
			}
		}
		if v.Revision != nil && *v.Revision != revision {
			return httpx.ErrInvalidState.WithCode("organization_revision_conflict")
		}
		var old *string
		e := tx.QueryRow(r.Context(), `SELECT department_id FROM department_members WHERE user_id=$1`, u).Scan(&old)
		if e != nil && !errors.Is(e, pgx.ErrNoRows) {
			return e
		}
		if v.Department != nil {
			var enabled bool
			if e = tx.QueryRow(r.Context(), `SELECT enabled FROM departments WHERE id=$1`, *v.Department).Scan(&enabled); e != nil || !enabled {
				return httpx.ErrInvalidRequest
			}
		}
		if _, e = tx.Exec(r.Context(), `DELETE FROM department_members WHERE user_id=$1`, u); e != nil {
			return e
		}
		if v.Department != nil {
			if _, e = tx.Exec(r.Context(), `INSERT INTO department_members(department_id,user_id)VALUES($1,$2)`, *v.Department, u); e != nil {
				return e
			}
		}
		if _, e = tx.Exec(r.Context(), `UPDATE users SET resource_revision=resource_revision+1 WHERE id=$1`, u); e != nil {
			return e
		}
		if v.Operation != "" {
			if _, e = tx.Exec(r.Context(), `INSERT INTO resource_department_operations(operation_id,user_id,expected_revision,department_id,status) VALUES($1,$2,$3,$4,'complete')`, v.Operation, u, *v.Revision, v.Department); e != nil {
				return e
			}
		}
		if e = groups.New(s.D).ReconcileAll(r.Context(), tx); e != nil {
			return e
		}
		if e = directoryChanged(r.Context(), tx); e != nil {
			return e
		}
		return audit(r.Context(), tx, r, "member.assign", u, old, v.Department)
	})
	result(w, r, map[string]bool{"updated": true}, e)
}
func desired(ctx context.Context, tx pgx.Tx, dept string) ([]string, error) {
	return ids(ctx, tx, `SELECT u.id FROM department_members dm JOIN users u ON u.id=dm.user_id JOIN departments d ON d.id=dm.department_id WHERE dm.department_id=$1 AND d.enabled AND u.status='active' AND u.permissions ? 'messaging' ORDER BY u.id`, dept)
}
func (s *Service) group(w http.ResponseWriter, r *http.Request) {
	dept := r.PathValue("id")
	var conv string
	e := s.tx(r, func(tx pgx.Tx) error {
		var name string
		var enabled bool
		if e := tx.QueryRow(r.Context(), `SELECT name,enabled FROM departments WHERE id=$1`, dept).Scan(&name, &enabled); e != nil || !enabled {
			return httpx.ErrNotFound
		}
		e := tx.QueryRow(r.Context(), `SELECT conversation_id FROM department_groups WHERE department_id=$1`, dept).Scan(&conv)
		if e == nil {
			return nil
		}
		if !errors.Is(e, pgx.ErrNoRows) {
			return e
		}
		members, e := desired(r.Context(), tx, dept)
		if e != nil {
			return e
		}
		if len(members) == 0 {
			return httpx.ErrInvalidState
		}
		if len(members) > s.D.Cfg.Limits.GroupMaxMembers {
			return httpx.ErrGroupFull
		}
		conv = idgen.New("conv")
		a, _ := auth.From(r)
		if _, e = tx.Exec(r.Context(), `INSERT INTO conversations(id,type,created_by)VALUES($1,'group',$2)`, conv, a.UserID); e != nil {
			return e
		}
		if _, e = tx.Exec(r.Context(), `INSERT INTO group_settings(conversation_id,owner_id,name,group_kind)VALUES($1,$2,$3,'department')`, conv, members[0], name); e != nil {
			return e
		}
		if _, e = tx.Exec(r.Context(), `INSERT INTO department_groups(department_id,conversation_id)VALUES($1,$2)`, dept, conv); e != nil {
			return e
		}
		if e = groups.New(s.D).Reconcile(r.Context(), tx, conv); e != nil {
			return e
		}
		if e = directoryChanged(r.Context(), tx); e != nil {
			return e
		}
		return audit(r.Context(), tx, r, "department.group", dept, nil, map[string]string{"conversation_id": conv})
	})
	result(w, r, map[string]string{"conversation_id": conv}, e)
}

// users exposes organization administration metadata, never passwords or sessions.
func (s *Service) users(w http.ResponseWriter, r *http.Request) {
	rows, e := s.D.Store.Pool.Query(r.Context(), `SELECT u.id,u.nickname,u.avatar_icon,u.status,u.permissions ? 'messaging',dm.department_id,u.is_admin,u.job_title_id,j.name,u.avatar_url,u.resource_revision FROM users u LEFT JOIN job_titles j ON j.id=u.job_title_id LEFT JOIN department_members dm ON dm.user_id=u.id WHERE u.status<>'deleted' ORDER BY u.nickname,u.id`)
	if e != nil {
		result(w, r, nil, e)
		return
	}
	defer rows.Close()
	items := []map[string]any{}
	for rows.Next() {
		var id, name, icon, status, avatarURL string
		var canMessage, isAdmin bool
		var dept, titleID, title *string
		var revision int64
		if e = rows.Scan(&id, &name, &icon, &status, &canMessage, &dept, &isAdmin, &titleID, &title, &avatarURL, &revision); e != nil {
			result(w, r, nil, e)
			return
		}
		items = append(items, map[string]any{"user_id": id, "nickname": name, "avatar_icon": icon, "avatar_url": avatarURL, "status": status, "can_message": canMessage && status == "active", "department_id": dept, "is_admin": isAdmin, "job_title_id": titleID, "job_title": title, "resource_revision": revision})
	}
	result(w, r, map[string]any{"items": items}, rows.Err())
}

// Only the control plane can sign resource-policy credentials. This endpoint
// exposes one authoritative membership revision without a user/session bypass.
func (s *Service) resourceMembership(w http.ResponseWriter, r *http.Request, c auth.Claims) {
	if c.UserID != r.PathValue("id") {
		result(w, r, nil, httpx.ErrForbidden)
		return
	}
	var revision int64
	var status string
	var department *string
	var enabled bool
	err := s.D.Store.Pool.QueryRow(r.Context(), `SELECT u.resource_revision,u.status,dm.department_id,COALESCE(d.enabled,true) FROM users u LEFT JOIN department_members dm ON dm.user_id=u.id LEFT JOIN departments d ON d.id=dm.department_id WHERE u.id=$1 AND u.status<>'deleted'`, c.UserID).Scan(&revision, &status, &department, &enabled)
	if errors.Is(err, pgx.ErrNoRows) {
		err = httpx.ErrNotFound
	}
	result(w, r, map[string]any{"department_id": department, "revision": revision, "enabled": enabled && status == "active"}, err)
}
