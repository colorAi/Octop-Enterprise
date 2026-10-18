package organization

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/jackc/pgx/v5"
	"octop/communication/internal/httpx"
)

type directorySettings struct {
	Name string `json:"name"`
}

func (s *Service) directorySettings(w http.ResponseWriter, r *http.Request) {
	var value directorySettings
	e := s.D.Store.Pool.QueryRow(r.Context(), `SELECT COALESCE((SELECT value->>'name' FROM kv_settings WHERE key='directory'),'')`).Scan(&value.Name)
	result(w, r, value, e)
}

func (s *Service) saveDirectorySettings(w http.ResponseWriter, r *http.Request) {
	var value directorySettings
	if !decode(w, r, &value) {
		return
	}
	value.Name = strings.TrimSpace(value.Name)
	if len([]rune(value.Name)) > 80 || strings.ContainsAny(value.Name, "\r\n\t") {
		result(w, r, nil, httpx.ErrInvalidRequest)
		return
	}
	e := s.tx(r, func(tx pgx.Tx) error {
		var before directorySettings
		if e := tx.QueryRow(r.Context(), `SELECT COALESCE((SELECT value->>'name' FROM kv_settings WHERE key='directory'),'')`).Scan(&before.Name); e != nil {
			return e
		}
		body, e := json.Marshal(value)
		if e != nil {
			return e
		}
		if _, e = tx.Exec(r.Context(), `INSERT INTO kv_settings(key,value)VALUES('directory',$1) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=now()`, body); e != nil {
			return e
		}
		if e = audit(r.Context(), tx, r, "directory.settings", "directory", before, value); e != nil {
			return e
		}
		return directoryChanged(r.Context(), tx)
	})
	result(w, r, value, e)
}
