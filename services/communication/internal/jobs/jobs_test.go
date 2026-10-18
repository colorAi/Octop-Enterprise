package jobs

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"sync/atomic"
	"testing"
	"time"

	"octop/communication/internal/deps"
	"octop/communication/internal/testdb"
)

func testSupervisor(t *testing.T) *Supervisor {
	t.Helper()
	st := testdb.Open(t, testdb.New(t))
	return New(&deps.Deps{Store: st, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), InstanceID: "worker-instance"})
}

func enqueueTestJob(t *testing.T, s *Supervisor, table string) {
	t.Helper()
	var err error
	if table == "delivery" {
		err = s.EnqueueDelivery(context.Background(), "sync_wakeup", "recipient", "test", nil)
	} else {
		err = s.Enqueue(context.Background(), "sync_wakeup", "test", nil)
	}
	if err != nil {
		t.Fatal(err)
	}
}

func waitStarted(t *testing.T, started <-chan struct{}) {
	t.Helper()
	select {
	case <-started:
	case <-time.After(3 * time.Second):
		t.Fatal("worker did not start")
	}
}

func TestExpiredLeaseCannotOverwriteNewerResult(t *testing.T) {
	for _, table := range []string{"background", "delivery"} {
		for _, tc := range []struct {
			name     string
			old, new error
			want     string
		}{
			{"late_retry", errors.New("late failure"), nil, "succeeded"},
			{"late_success", nil, errors.New("new failure"), "retry_wait"},
			{"late_permanent_failure", ErrPermanent, nil, "succeeded"},
			{"late_skip", ErrSkip, nil, "succeeded"},
		} {
			t.Run(table+"/"+tc.name, func(t *testing.T) {
				first := testSupervisor(t)
				// Even workers within one process must have distinct claim identities.
				second := New(first.d)
				ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
				defer cancel()
				started, release := make(chan struct{}), make(chan struct{})
				var calls atomic.Int32
				first.Register("sync_wakeup", func(ctx context.Context, _ json.RawMessage) error {
					calls.Add(1)
					close(started)
					select {
					case <-release:
						return tc.old
					case <-ctx.Done():
						return ctx.Err()
					}
				})
				second.Register("sync_wakeup", func(context.Context, json.RawMessage) error { calls.Add(1); return tc.new })
				enqueueTestJob(t, first, table)
				done := make(chan error, 1)
				go func() { _, err := first.runOne(ctx, table); done <- err }()
				waitStarted(t, started)
				if _, err := first.d.Store.Pool.Exec(ctx, `UPDATE `+jobTable(table)+` SET lease_until=now()-interval '1 second' WHERE work_key='test'`); err != nil {
					t.Fatal(err)
				}
				if ran, err := second.runOne(ctx, table); err != nil || !ran {
					t.Fatalf("second worker: ran=%v err=%v", ran, err)
				}
				close(release)
				if err := <-done; !errors.Is(err, ErrLeaseLost) {
					t.Fatalf("stale result accepted: %v", err)
				}
				var state string
				if err := first.d.Store.Pool.QueryRow(ctx, `SELECT state FROM `+jobTable(table)+` WHERE work_key='test'`).Scan(&state); err != nil {
					t.Fatal(err)
				}
				if state != tc.want || calls.Load() != 2 {
					t.Fatalf("state=%s calls=%d", state, calls.Load())
				}
			})
		}
	}
}

func TestLeaseRenewalPreventsConcurrentExecution(t *testing.T) {
	for _, table := range []string{"background", "delivery"} {
		t.Run(table, func(t *testing.T) {
			first := testSupervisor(t)
			first.leaseDuration = 600 * time.Millisecond
			second := New(first.d)
			ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
			defer cancel()
			started, release := make(chan struct{}), make(chan struct{})
			first.Register("sync_wakeup", func(ctx context.Context, _ json.RawMessage) error {
				close(started)
				select {
				case <-release:
					return nil
				case <-ctx.Done():
					return ctx.Err()
				}
			})
			second.Register("sync_wakeup", func(context.Context, json.RawMessage) error { return nil })
			enqueueTestJob(t, first, table)
			done := make(chan error, 1)
			go func() { _, err := first.runOne(ctx, table); done <- err }()
			waitStarted(t, started)
			var original time.Time
			if err := first.d.Store.Pool.QueryRow(ctx, `SELECT lease_until FROM `+jobTable(table)+` WHERE work_key='test'`).Scan(&original); err != nil {
				t.Fatal(err)
			}
			for {
				var elapsed, extended bool
				if err := first.d.Store.Pool.QueryRow(ctx, `SELECT clock_timestamp()>$1,lease_until>$1 FROM `+jobTable(table)+` WHERE work_key='test'`, original).Scan(&elapsed, &extended); err != nil {
					t.Fatal(err)
				}
				if elapsed {
					if !extended {
						t.Fatal("lease expired during active handler")
					}
					break
				}
				if ctx.Err() != nil {
					t.Fatal(ctx.Err())
				}
				time.Sleep(20 * time.Millisecond)
			}
			if ran, err := second.runOne(ctx, table); err != nil || ran {
				t.Fatalf("live work reclaimed: ran=%v err=%v", ran, err)
			}
			close(release)
			if err := <-done; err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestLeaseLossCancelsHandler(t *testing.T) {
	for _, table := range []string{"background", "delivery"} {
		t.Run(table, func(t *testing.T) {
			s := testSupervisor(t)
			s.leaseDuration = 300 * time.Millisecond
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			started := make(chan struct{})
			s.Register("sync_wakeup", func(ctx context.Context, _ json.RawMessage) error { close(started); <-ctx.Done(); return ctx.Err() })
			enqueueTestJob(t, s, table)
			done := make(chan error, 1)
			go func() { _, err := s.runOne(ctx, table); done <- err }()
			waitStarted(t, started)
			if _, err := s.d.Store.Pool.Exec(ctx, `UPDATE `+jobTable(table)+` SET lease_token='replacement' WHERE work_key='test'`); err != nil {
				t.Fatal(err)
			}
			select {
			case err := <-done:
				if !errors.Is(err, ErrLeaseLost) {
					t.Fatal(err)
				}
			case <-time.After(2 * time.Second):
				t.Fatal("handler was not cancelled after lease loss")
			}
			var token, state string
			if err := s.d.Store.Pool.QueryRow(ctx, `SELECT lease_token,state FROM `+jobTable(table)+` WHERE work_key='test'`).Scan(&token, &state); err != nil {
				t.Fatal(err)
			}
			if token != "replacement" || state != "leased" {
				t.Fatalf("replacement modified: token=%s state=%s", token, state)
			}
		})
	}
}

func TestRecurringRetriesAndOneShotIdempotency(t *testing.T) {
	s := testSupervisor(t)
	ctx := context.Background()
	s.RegisterRecurring("maintenance", time.Hour, func(context.Context, json.RawMessage) error { return errors.New("transient") })
	if err := s.ensureRecurring(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err := s.runOne(ctx, "background"); err != nil {
		t.Fatal(err)
	}
	var state string
	var attempts int
	if err := s.d.Store.Pool.QueryRow(ctx, `SELECT state,attempts FROM background_jobs WHERE work_key='maintenance'`).Scan(&state, &attempts); err != nil {
		t.Fatal(err)
	}
	if state != "retry_wait" || attempts != 1 {
		t.Fatalf("initial retry: %s %d", state, attempts)
	}
	if _, err := s.d.Store.Pool.Exec(ctx, `UPDATE background_jobs SET attempts=max_attempts-1,next_attempt_at=now() WHERE work_key='maintenance'`); err != nil {
		t.Fatal(err)
	}
	if _, err := s.runOne(ctx, "background"); err != nil {
		t.Fatal(err)
	}
	var later bool
	if err := s.d.Store.Pool.QueryRow(ctx, `SELECT state,attempts,next_attempt_at>now() FROM background_jobs WHERE work_key='maintenance'`).Scan(&state, &attempts, &later); err != nil {
		t.Fatal(err)
	}
	if state != "pending" || attempts != 0 || !later {
		t.Fatalf("next cycle after exhausted retries: %s %d %v", state, attempts, later)
	}
	s.Register("once", func(context.Context, json.RawMessage) error { return nil })
	if err := s.Enqueue(ctx, "once", "once", nil); err != nil {
		t.Fatal(err)
	}
	if _, err := s.runOne(ctx, "background"); err != nil {
		t.Fatal(err)
	}
	if err := s.Enqueue(ctx, "once", "once", nil); err != nil {
		t.Fatal(err)
	}
	if ran, err := s.runOne(ctx, "background"); err != nil || ran {
		t.Fatalf("completed one-shot reran: %v %v", ran, err)
	}
}

func TestRecurringStartupPreservesScheduledAndLeasedWork(t *testing.T) {
	s := testSupervisor(t)
	ctx := context.Background()
	for _, state := range []string{"pending", "retry_wait", "leased"} {
		s.RegisterRecurring(state, time.Hour, func(context.Context, json.RawMessage) error { return nil })
		if _, err := s.d.Store.Pool.Exec(ctx, `INSERT INTO background_jobs(id,kind,work_key,state,attempts,next_attempt_at,lease_until,lease_token,instance) VALUES($1,$1,$1,$1,3,now()+interval '1 hour',now()+interval '1 minute','existing-claim','other')`, state); err != nil {
			t.Fatal(err)
		}
	}
	snapshot := func() string {
		var v string
		if err := s.d.Store.Pool.QueryRow(ctx, `SELECT jsonb_agg(to_jsonb(j) ORDER BY id)::text FROM background_jobs j`).Scan(&v); err != nil {
			t.Fatal(err)
		}
		return v
	}
	before := snapshot()
	if err := s.ensureRecurring(ctx); err != nil {
		t.Fatal(err)
	}
	if before != snapshot() {
		t.Fatal("startup reset existing scheduled/retrying/leased work")
	}
}

func TestStopReleasesBothQueuesOnlyForOwnInstance(t *testing.T) {
	s := testSupervisor(t)
	ctx := context.Background()
	for _, table := range []string{"background_jobs", "delivery_outbox"} {
		var q string
		if table == "background_jobs" {
			q = `INSERT INTO background_jobs(id,kind,work_key,state,instance,lease_token,lease_until) VALUES($1,'test',$1,'leased',$2,'claim',now()+interval '1 minute')`
		} else {
			q = `INSERT INTO delivery_outbox(id,kind,work_key,recipient_user_id,state,instance,lease_token,lease_until) VALUES($1,'sync_wakeup',$1,'recipient','leased',$2,'claim',now()+interval '1 minute')`
		}
		for _, owner := range []string{s.instID, "other"} {
			if _, err := s.d.Store.Pool.Exec(ctx, q, owner, owner); err != nil {
				t.Fatal(err)
			}
		}
	}
	s.Stop(ctx)
	for _, table := range []string{"background_jobs", "delivery_outbox"} {
		var own, other bool
		if err := s.d.Store.Pool.QueryRow(ctx, `SELECT state='pending' AND lease_token='' AND lease_until IS NULL FROM `+table+` WHERE id=$1`, s.instID).Scan(&own); err != nil {
			t.Fatal(err)
		}
		if err := s.d.Store.Pool.QueryRow(ctx, `SELECT state='leased' AND lease_token='claim' FROM `+table+` WHERE id='other'`).Scan(&other); err != nil {
			t.Fatal(err)
		}
		if !own || !other {
			t.Fatalf("incorrect shutdown release in %s", table)
		}
	}
}
