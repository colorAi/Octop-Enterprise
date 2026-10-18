package organization

import (
	"net/http"
	"strings"
	"unicode"

	"github.com/jackc/pgx/v5"
	"octop/communication/internal/httpx"
	"octop/communication/internal/idgen"
)

type jobTitle struct {
	ID      string `json:"job_title_id"`
	Name    string `json:"name"`
	Builtin bool   `json:"builtin"`
}

func (s *Service) jobTitles(w http.ResponseWriter, r *http.Request) {
	rows, err := s.D.Store.Pool.Query(r.Context(), `SELECT id,name,builtin FROM job_titles ORDER BY sort_order,created_at,id`)
	if err != nil {
		result(w, r, nil, err)
		return
	}
	defer rows.Close()
	items := []jobTitle{}
	for rows.Next() {
		var item jobTitle
		if err = rows.Scan(&item.ID, &item.Name, &item.Builtin); err != nil {
			result(w, r, nil, err)
			return
		}
		items = append(items, item)
	}
	result(w, r, map[string]any{"items": items}, rows.Err())
}

func (s *Service) createJobTitle(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Name string `json:"name"`
	}
	if !decode(w, r, &body) {
		return
	}
	body.Name = strings.TrimSpace(body.Name)
	if body.Name == "" || len([]rune(body.Name)) > 40 || strings.IndexFunc(body.Name, unicode.IsControl) >= 0 {
		result(w, r, nil, httpx.ErrInvalidRequest)
		return
	}
	var item jobTitle
	err := s.tx(r, func(tx pgx.Tx) error {
		// Reuse an existing name, including concurrent or retried submissions.
		if err := tx.QueryRow(r.Context(), `INSERT INTO job_titles(id,name) VALUES($1,$2)
            ON CONFLICT (lower(name)) DO UPDATE SET name=job_titles.name
            RETURNING id,name,builtin`, idgen.New("job"), body.Name).Scan(&item.ID, &item.Name, &item.Builtin); err != nil {
			return err
		}
		return audit(r.Context(), tx, r, "job_title.create", item.ID, nil, item)
	})
	result(w, r, item, err)
}

func (s *Service) assignJobTitle(w http.ResponseWriter, r *http.Request) {
	var body struct {
		ID *string `json:"job_title_id"`
	}
	if !decode(w, r, &body) {
		return
	}
	user := r.PathValue("id")
	err := s.tx(r, func(tx pgx.Tx) error {
		var before *string
		if err := tx.QueryRow(r.Context(), `SELECT job_title_id FROM users WHERE id=$1 AND status<>'deleted' FOR UPDATE`, user).Scan(&before); err != nil {
			return httpx.ErrNotFound
		}
		if body.ID != nil {
			var exists bool
			if err := tx.QueryRow(r.Context(), `SELECT EXISTS(SELECT 1 FROM job_titles WHERE id=$1)`, *body.ID).Scan(&exists); err != nil {
				return err
			}
			if !exists {
				return httpx.ErrInvalidRequest
			}
		}
		if _, err := tx.Exec(r.Context(), `UPDATE users SET job_title_id=$2,updated_at=now() WHERE id=$1`, user, body.ID); err != nil {
			return err
		}
		if err := audit(r.Context(), tx, r, "member.position", user, before, body.ID); err != nil {
			return err
		}
		return directoryChanged(r.Context(), tx)
	})
	result(w, r, map[string]bool{"updated": true}, err)
}
