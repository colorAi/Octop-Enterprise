package store

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
)

var ErrMaintenance = errors.New("write admission paused for backup")

func (s *Store) maintenanceState(ctx context.Context) (bool, error) {
	var paused bool
	err := s.Pool.QueryRow(ctx, "SELECT COALESCE((value->>'paused')::bool,false) FROM kv_settings WHERE key='maintenance'").Scan(&paused)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	return paused, err
}

// BeginWrite covers the whole HTTP mutation or worker, including file IO.
// It does not reserve a database connection while the handler runs.
func (s *Store) BeginWrite(ctx context.Context) (func(), error) {
	s.writeAdmission.RLock()
	s.activeWrites.Add(1)
	release := func() {
		s.activeWrites.Add(-1)
		s.writeAdmission.RUnlock()
	}
	paused, err := s.maintenanceState(ctx)
	if err != nil || paused {
		release()
		if err != nil {
			return nil, err
		}
		return nil, ErrMaintenance
	}
	return release, nil
}
