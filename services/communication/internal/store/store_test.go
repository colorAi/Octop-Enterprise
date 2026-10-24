package store_test

import (
	"context"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"

	"octop/communication/internal/store"
	"octop/communication/internal/testdb"
)

func TestMigrateIdempotentAndConstraints(t *testing.T) {
	dsn := testdb.New(t)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	st := testdb.Open(t, dsn)

	if err := st.CheckShape(ctx); err != nil {
		t.Fatalf("current schema check: %v", err)
	}

	// testdb.New already migrated; a second run must be a no-op.
	done, err := st.Migrate(ctx)
	if err != nil {
		t.Fatalf("second migrate: %v", err)
	}
	if len(done) != 0 {
		t.Fatalf("expected no re-applied migrations, got %d", len(done))
	}

	var version int64
	if err := st.Pool.QueryRow(ctx, "SELECT max(version) FROM schema_migrations").Scan(&version); err != nil {
		t.Fatal(err)
	}
	if version != 16 {
		t.Fatalf("expected current migration version 16, got %d", version)
	}

	if _, err := st.Pool.Exec(ctx, `INSERT INTO conversations(id,type) VALUES('c1','private'),('c2','private'),('c3','private')`); err != nil {
		t.Fatal(err)
	}
	if _, err := st.Pool.Exec(ctx, `INSERT INTO direct_pairs(conversation_id,user_low,user_high)VALUES('c1','u1','u2')`); err != nil {
		t.Fatal(err)
	}
	if _, err := st.Pool.Exec(ctx, `INSERT INTO direct_pairs(conversation_id,user_low,user_high)VALUES('c2','u1','u2')`); err == nil {
		t.Fatal("expected unique direct pair")
	}
	if _, err := st.Pool.Exec(ctx, `INSERT INTO direct_pairs(conversation_id,user_low,user_high)VALUES('c3','u2','u1')`); err == nil {
		t.Fatal("expected canonical pair ordering")
	}

}

func TestEmitSyncSequence(t *testing.T) {
	dsn := testdb.New(t)
	ctx := context.Background()
	st := testdb.Open(t, dsn)
	if _, err := st.Pool.Exec(ctx, `INSERT INTO users(id,status,identity_version) VALUES('u1','active',1)`); err != nil {
		t.Fatal(err)
	}
	err := st.Tx(ctx, func(tx pgx.Tx) error {
		for i := 0; i < 3; i++ {
			if _, err := store.EmitSync(ctx, tx, "u1", "test.event", "obj", int64(i), map[string]any{"i": i}); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	var c int64
	if err := st.Pool.QueryRow(ctx, `SELECT counter FROM user_sync_heads WHERE user_id='u1'`).Scan(&c); err != nil {
		t.Fatal(err)
	}
	var queued int
	if err := st.Pool.QueryRow(ctx, `SELECT count(*) FROM delivery_outbox WHERE kind='sync_wakeup'`).Scan(&queued); err != nil || queued != 3 {
		t.Fatal("wakeup not atomically enqueued", err, queued)
	}
	if c != 3 {
		t.Fatalf("counter=%d want 3", c)
	}
}

func TestRollbackLeavesNoEvents(t *testing.T) {
	dsn := testdb.New(t)
	ctx := context.Background()
	st := testdb.Open(t, dsn)
	if _, err := st.Pool.Exec(ctx, `INSERT INTO users(id,status,identity_version) VALUES('u1','active',1)`); err != nil {
		t.Fatal(err)
	}
	want := errBoom
	_ = want
	err := st.Tx(ctx, func(tx pgx.Tx) error {
		if _, err := store.EmitSync(ctx, tx, "u1", "test.event", "obj", 1, nil); err != nil {
			return err
		}
		return errBoom
	})
	if err == nil {
		t.Fatal("expected rollback error")
	}
	var c int64
	if err := st.Pool.QueryRow(ctx, `SELECT counter FROM user_sync_heads WHERE user_id='u1'`).Scan(&c); err != nil {
		// head row never committed either
		c = 0
	}
	if c != 0 {
		t.Fatalf("counter after rollback=%d want 0", c)
	}
	var n int
	if err := st.Pool.QueryRow(ctx, `SELECT count(*) FROM user_sync_events`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	var queued int
	if e := st.Pool.QueryRow(ctx, `SELECT count(*) FROM delivery_outbox`).Scan(&queued); e != nil || queued != 0 {
		t.Fatal("rolled-back wakeup persisted", e, queued)
	}
	if n != 0 {
		t.Fatalf("events after rollback=%d want 0", n)
	}
}

type boomErr struct{}

func (boomErr) Error() string { return "boom" }

var errBoom = boomErr{}
