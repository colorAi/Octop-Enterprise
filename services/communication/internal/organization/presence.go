package organization

import "net/http"

func (s *Service) presence(w http.ResponseWriter, r *http.Request) {
	online := []string{}
	if s.D.Realtime != nil {
		online = s.D.Realtime.OnlineUsers()
	}
	rows, err := s.D.Store.Pool.Query(r.Context(), `SELECT id FROM users WHERE id=ANY($1::text[]) AND status='active' AND NOT is_admin ORDER BY id`, online)
	if err != nil {
		result(w, r, nil, err)
		return
	}
	defer rows.Close()
	users := []string{}
	for rows.Next() {
		var user string
		if err = rows.Scan(&user); err != nil {
			result(w, r, nil, err)
			return
		}
		users = append(users, user)
	}
	result(w, r, map[string]any{"online_user_ids": users}, rows.Err())
}
