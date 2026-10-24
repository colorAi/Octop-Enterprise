package organization

import (
	"net/http"
	"octop/communication/internal/auth"
	"octop/communication/internal/httpx"
	"strconv"
	"strings"
)

// Display-only batch reads use a separate internal credential. Neither public
// request credentials nor the single-user execution credential can read a batch.
func (s *Service) resourceMemberships(w http.ResponseWriter, r *http.Request, c auth.Claims) {
	parts := strings.Split(c.UserID, ":")
	if len(parts) != 3 || parts[0] != "octop" || parts[1] == "" {
		result(w, r, nil, httpx.ErrForbidden)
		return
	}
	prefix := parts[0] + ":" + parts[1] + ":"
	var body struct {
		UserIDs []string `json:"user_ids"`
	}
	if !decode(w, r, &body) {
		return
	}
	if len(body.UserIDs) == 0 || len(body.UserIDs) > 200 {
		result(w, r, nil, httpx.ErrInvalidRequest)
		return
	}
	seen := make(map[string]bool, len(body.UserIDs))
	for _, id := range body.UserIDs {
		if !strings.HasPrefix(id, prefix) {
			result(w, r, nil, httpx.ErrForbidden)
			return
		}
		n, err := strconv.ParseInt(strings.TrimPrefix(id, prefix), 10, 64)
		if err != nil || n <= 0 || id != prefix+strconv.FormatInt(n, 10) || seen[id] {
			result(w, r, nil, httpx.ErrInvalidRequest)
			return
		}
		seen[id] = true
	}
	rows, err := s.D.Store.Pool.Query(r.Context(), `
		SELECT requested.id,u.id IS NOT NULL,dm.department_id,
		       COALESCE(u.resource_revision,0),COALESCE(u.status='active' AND COALESCE(d.enabled,true),false)
		FROM unnest($1::text[]) WITH ORDINALITY AS requested(id,position)
		LEFT JOIN users u ON u.id=requested.id AND u.status<>'deleted'
		LEFT JOIN department_members dm ON dm.user_id=u.id
		LEFT JOIN departments d ON d.id=dm.department_id
		ORDER BY requested.position`, body.UserIDs)
	if err != nil {
		result(w, r, nil, err)
		return
	}
	defer rows.Close()
	items := make([]map[string]any, 0, len(body.UserIDs))
	for rows.Next() {
		var id string
		var found, enabled bool
		var department *string
		var revision int64
		if err := rows.Scan(&id, &found, &department, &revision, &enabled); err != nil {
			result(w, r, nil, err)
			return
		}
		items = append(items, map[string]any{"user_id": id, "found": found, "department_id": department, "revision": revision, "enabled": enabled})
	}
	result(w, r, map[string]any{"items": items}, rows.Err())
}
