package store_test

import (
	"context"
	"errors"
	"testing"

	"octop/communication/internal/store"
	"octop/communication/internal/testdb"
)

func TestMaintenanceAcknowledgementWaitsForWriters(t *testing.T) {
	st := testdb.Open(t, testdb.New(t))
	ctx := context.Background()
	release, err := st.BeginWrite(ctx)
	if err != nil {
		t.Fatal(err)
	}
	released := false
	defer func() {
		if !released {
			release()
		}
	}()
	if err := store.SetMaintenanceFlag(ctx, st.Pool, true); err != nil {
		t.Fatal(err)
	}
	if err := st.Heartbeat(ctx, "admission-review", 0, true); err != nil {
		t.Fatal(err)
	}
	var writers int
	var accepting bool
	if err := st.Pool.QueryRow(ctx, "SELECT writes_in_flight,accepting FROM server_heartbeats WHERE instance_id='admission-review'").Scan(&writers, &accepting); err != nil {
		t.Fatal(err)
	}
	if writers == 0 {
		t.Fatal("heartbeat acknowledged quiescence while an admitted writer is active")
	}
	release()
	released = true
	if err := st.Heartbeat(ctx, "admission-review", 0, true); err != nil {
		t.Fatal(err)
	}
	if err := st.Pool.QueryRow(ctx, "SELECT writes_in_flight,accepting FROM server_heartbeats WHERE instance_id='admission-review'").Scan(&writers, &accepting); err != nil {
		t.Fatal(err)
	}
	if writers != 0 || accepting {
		t.Fatal("drained maintenance window was not acknowledged")
	}
	if _, err := st.BeginWrite(ctx); !errors.Is(err, store.ErrMaintenance) {
		t.Fatalf("paused write admitted: %v", err)
	}
	if err := store.SetMaintenanceFlag(ctx, st.Pool, false); err != nil {
		t.Fatal(err)
	}
	next, err := st.BeginWrite(ctx)
	if err != nil {
		t.Fatal(err)
	}
	next()
}
