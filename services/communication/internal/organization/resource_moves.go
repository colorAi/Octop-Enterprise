package organization

import (
	"errors"
	"github.com/jackc/pgx/v5"
	"net/http"
	"octop/communication/internal/auth"
	"octop/communication/internal/httpx"
)

// Atomically confirm a committed move or cancel its operation ID. A request
// delayed in the network cannot commit after the control plane unblocks use.
func (s *Service) resolveResourceMove(w http.ResponseWriter, r *http.Request, c auth.Claims) {
	if c.UserID != r.PathValue("id") {
		result(w, r, nil, httpx.ErrForbidden)
		return
	}
	var body struct {
		Operation string `json:"operation_id"`
		Revision  int64  `json:"expected_revision"`
	}
	if !decode(w, r, &body) {
		return
	}
	if body.Operation == "" || len(body.Operation) > 128 || body.Revision < 0 {
		result(w, r, nil, httpx.ErrInvalidRequest)
		return
	}
	state := "cancelled"
	err := s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		var revision int64
		if err := tx.QueryRow(r.Context(), `SELECT resource_revision FROM users WHERE id=$1 FOR UPDATE`, c.UserID).Scan(&revision); err != nil {
			return err
		}
		var user string
		var expected int64
		err := tx.QueryRow(r.Context(), `SELECT status,user_id,expected_revision FROM resource_department_operations WHERE operation_id=$1`, body.Operation).Scan(&state, &user, &expected)
		if err == nil {
			if user != c.UserID || expected != body.Revision {
				return httpx.ErrInvalidState
			}
			return nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return err
		}
		_, err = tx.Exec(r.Context(), `INSERT INTO resource_department_operations(operation_id,user_id,expected_revision,status) VALUES($1,$2,$3,'cancelled')`, body.Operation, c.UserID, body.Revision)
		return err
	})
	result(w, r, map[string]any{"status": state}, err)
}
