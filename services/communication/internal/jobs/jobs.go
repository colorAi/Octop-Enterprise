// Package jobs implements the persistent job and delivery-outbox workers:
// PostgreSQL-backed leases, bounded concurrency, exponential backoff, and
// crash-reclaimable execution (state lives only in the database).
package jobs

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/jackc/pgx/v5"

	"octop/communication/internal/deps"
	"octop/communication/internal/idgen"
	"octop/communication/internal/observability"
	"octop/communication/internal/store"
)

// ErrPermanent ends the attempt without retrying. Recurring jobs wait until
// their next scheduled run; one-shot jobs become failed.
var ErrPermanent = errors.New("permanent job failure")

// ErrSkip marks a job as skipped (work became irrelevant).
var ErrSkip = errors.New("job skipped")

// ErrLeaseLost prevents an expired/replaced worker from publishing a result.
var ErrLeaseLost = errors.New("job lease lost")

// Handler processes one job payload. It must honor cancellation and tolerate
// re-execution after a crash between its side effects and the queue acknowledgement.
type Handler func(ctx context.Context, payload json.RawMessage) error

type registration struct {
	handler  Handler
	interval time.Duration
}

type lease struct{ id, token string }

// Supervisor claims and runs jobs from background_jobs and delivery_outbox.
type Supervisor struct {
	d             *deps.Deps
	log           *slog.Logger
	regs          map[string]registration
	instID        string
	wg            sync.WaitGroup
	cancel        context.CancelFunc
	running       bool
	leaseDuration time.Duration
}

// New returns a supervisor for the given deps.
func New(d *deps.Deps) *Supervisor {
	return &Supervisor{d: d, log: d.Log.With("component", "jobs"), regs: map[string]registration{}, instID: d.InstanceID, leaseDuration: 60 * time.Second}
}

// Register attaches a handler for a job kind (before Start).
func (s *Supervisor) Register(kind string, h Handler) {
	if _, dup := s.regs[kind]; dup {
		panic("jobs: duplicate kind " + kind)
	}
	s.regs[kind] = registration{handler: h}
}

// RegisterRecurring schedules one durable job per kind. The worker atomically
// releases its lease and schedules the next run, including after terminal errors.
func (s *Supervisor) RegisterRecurring(kind string, interval time.Duration, h Handler) {
	if interval <= 0 {
		panic("jobs: recurring interval must be positive")
	}
	s.Register(kind, h)
	s.regs[kind] = registration{handler: h, interval: interval}
}

func (s *Supervisor) ensureRecurring(ctx context.Context) error {
	for kind, reg := range s.regs {
		if reg.interval == 0 {
			continue
		}
		// Repair terminal rows left by the former self-enqueue implementation.
		// Live leases and already scheduled/retrying work are never reset.
		_, err := s.d.Store.Pool.Exec(ctx, `INSERT INTO background_jobs(id,kind,work_key)
			VALUES($1,$2,$2) ON CONFLICT(work_key) DO UPDATE
			SET state='pending',attempts=0,next_attempt_at=now(),lease_until=NULL,
			lease_token='',last_error='',finished_at=NULL,updated_at=now()
			WHERE background_jobs.kind=EXCLUDED.kind AND background_jobs.state IN ('succeeded','failed','skipped')`, idgen.New("job"), kind)
		if err != nil {
			return err
		}
	}
	return nil
}

// Enqueue adds a background job (idempotent by work_key).
func (s *Supervisor) Enqueue(ctx context.Context, kind, workKey string, payload any) error {
	return s.EnqueueAt(ctx, kind, workKey, payload, time.Time{})
}

// EnqueueAt schedules a job at a specific time.
func (s *Supervisor) EnqueueAt(ctx context.Context, kind, workKey string, payload any, at time.Time) error {
	raw := []byte("{}")
	if payload != nil {
		b, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		raw = b
	}
	if at.IsZero() {
		at = time.Now()
	}
	_, err := s.d.Store.Pool.Exec(ctx, `INSERT INTO background_jobs(id, kind, work_key, payload, next_attempt_at)
		VALUES($1,$2,$3,$4,$5) ON CONFLICT (work_key) DO NOTHING`,
		idgen.New("job"), kind, workKey, raw, at.UTC())
	return err
}

// EnqueueDelivery adds a delivery outbox row (idempotent by work_key).
func (s *Supervisor) EnqueueDelivery(ctx context.Context, kind, recipient, workKey string, payload any) error {
	raw := []byte("{}")
	if payload != nil {
		b, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		raw = b
	}
	_, err := s.d.Store.Pool.Exec(ctx, `INSERT INTO delivery_outbox(id, kind, recipient_user_id, work_key, payload)
		VALUES($1,$2,$3,$4,$5) ON CONFLICT (work_key) DO NOTHING`,
		idgen.New("obx"), kind, recipient, workKey, raw)
	return err
}

// Start launches the worker pools.
func (s *Supervisor) Start(ctx context.Context) error {
	if err := s.ensureRecurring(ctx); err != nil {
		return err
	}
	ctx, s.cancel = context.WithCancel(ctx)
	s.running = true
	n := s.d.Cfg.Workers.JobTotal
	for i := 0; i < n; i++ {
		s.wg.Add(1)
		go s.loop(ctx, "background", i)
	}
	m := s.d.Cfg.Workers.OutboxTotal
	for i := 0; i < m; i++ {
		s.wg.Add(1)
		go s.loop(ctx, "delivery", i)
	}
	return nil
}

// Stop drains workers and releases leases.
func (s *Supervisor) Stop(ctx context.Context) {
	if s.cancel != nil {
		s.cancel()
	}
	done := make(chan struct{})
	go func() { s.wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-ctx.Done():
	}
	cleanup, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	for _, table := range []string{"background_jobs", "delivery_outbox"} {
		_, err := s.d.Store.Pool.Exec(cleanup, `UPDATE `+table+` SET state='pending', lease_until=NULL, lease_token='' WHERE state='leased' AND instance=$1`, s.instID)
		if err != nil {
			s.log.Error("job lease release failed", "table", table, "err", err)
		}
	}
}

func (s *Supervisor) loop(ctx context.Context, table string, n int) {
	defer s.wg.Done()
	tick := time.NewTicker(s.d.Cfg.JobPollInterval)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
			ran, err := s.runOne(ctx, table)
			if err != nil && !errors.Is(err, context.Canceled) {
				s.log.Warn("job poll error", "table", table, "err", err)
			}
			if ran {
				tick.Reset(50 * time.Millisecond) // keep draining when busy
			} else {
				tick.Reset(s.d.Cfg.JobPollInterval)
			}
		}
	}
}

func (s *Supervisor) runOne(ctx context.Context, table string) (bool, error) {
	releaseWrite, err := s.d.Store.BeginWrite(ctx)
	if errors.Is(err, store.ErrMaintenance) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	defer releaseWrite()
	var (
		claim = lease{token: idgen.New("lease")}
		kindS string
		raw   []byte
	)
	q := `WITH cte AS (
		SELECT id FROM background_jobs
		WHERE (state IN ('pending','retry_wait') AND next_attempt_at <= now())
		   OR (state='leased' AND lease_until < now())
		ORDER BY next_attempt_at
		LIMIT 1 FOR UPDATE SKIP LOCKED)
	 UPDATE background_jobs j SET state='leased', attempts=j.attempts+1, lease_until=now()+$2::interval,
	        instance=$1, lease_token=$3, updated_at=now()
	 FROM cte WHERE j.id=cte.id
	 RETURNING j.id, j.kind, j.payload`
	if table == "delivery" {
		// One concurrent item per recipient keeps user-visible ordering sane.
		q = `WITH cte AS (
			SELECT o.id FROM delivery_outbox o
			WHERE ((o.state IN ('pending','retry_wait') AND o.next_attempt_at <= now())
			    OR (o.state='leased' AND o.lease_until < now()))
			  AND o.recipient_user_id NOT IN (
				SELECT recipient_user_id FROM delivery_outbox WHERE state='leased' AND lease_until >= now())
			ORDER BY o.next_attempt_at LIMIT 1 FOR UPDATE SKIP LOCKED)
		 UPDATE delivery_outbox o SET state='leased', attempts=o.attempts+1, lease_until=now()+$2::interval,
		        instance=$1, lease_token=$3, updated_at=now()
		 FROM cte WHERE o.id=cte.id
		 RETURNING o.id, o.kind, o.payload`
	}
	err = s.d.Store.Pool.QueryRow(ctx, q, s.instID, fmt.Sprintf("%f seconds", s.leaseDuration.Seconds()), claim.token).
		Scan(&claim.id, &kindS, &raw)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	reg, ok := s.regs[kindS]
	if !ok {
		return true, s.finish(ctx, table, claim, "failed", "no handler for kind "+kindS, 0)
	}
	hctx, cancel := context.WithTimeout(ctx, 120*time.Second)
	defer cancel()
	renewed := make(chan struct{})
	go func() { defer close(renewed); s.keepLease(hctx, cancel, table, claim) }()
	err = reg.handler(hctx, raw)
	cancel()
	<-renewed
	switch {
	case err == nil:
		return true, s.finish(ctx, table, claim, "succeeded", "", reg.interval)
	case errors.Is(err, ErrSkip):
		return true, s.finish(ctx, table, claim, "skipped", truncate(err.Error(), 400), reg.interval)
	case errors.Is(err, ErrPermanent):
		return true, s.finish(ctx, table, claim, "failed", truncate(err.Error(), 400), reg.interval)
	default:
		return true, s.retry(ctx, table, claim, truncate(err.Error(), 400), reg.interval)
	}
}

func jobTable(table string) string {
	if table == "delivery" {
		return "delivery_outbox"
	}
	return "background_jobs"
}

func (s *Supervisor) keepLease(ctx context.Context, cancel context.CancelFunc, table string, claim lease) {
	ticker := time.NewTicker(s.leaseDuration / 3)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if err := s.renew(ctx, table, claim); err != nil {
				if ctx.Err() == nil {
					s.log.Warn("job lease renewal failed", "id", claim.id, "err", err)
				}
				cancel()
				return
			}
		}
	}
}

func (s *Supervisor) renew(ctx context.Context, table string, claim lease) error {
	ctx, cancel := context.WithTimeout(ctx, min(5*time.Second, s.leaseDuration/3))
	defer cancel()
	result, err := s.d.Store.Pool.Exec(ctx, `UPDATE `+jobTable(table)+` SET lease_until=clock_timestamp()+$3::interval
		WHERE id=$1 AND lease_token=$2 AND state='leased' AND lease_until>clock_timestamp()`, claim.id, claim.token, fmt.Sprintf("%f seconds", s.leaseDuration.Seconds()))
	if err != nil {
		return err
	}
	if result.RowsAffected() == 0 {
		return ErrLeaseLost
	}
	return nil
}

func (s *Supervisor) finish(ctx context.Context, table string, claim lease, state, e string, interval time.Duration) error {
	if table == "delivery" {
		interval = 0
	}
	q := `UPDATE ` + jobTable(table) + ` SET
		state=CASE WHEN $5::double precision>0 THEN 'pending' ELSE $3 END,
		attempts=CASE WHEN $5>0 THEN 0 ELSE attempts END,
		next_attempt_at=CASE WHEN $5>0 THEN now()+make_interval(secs=>$5) ELSE next_attempt_at END,
		last_error=$4,finished_at=now(),lease_until=NULL,lease_token='',updated_at=now()
		WHERE id=$1 AND lease_token=$2 AND state='leased' AND lease_until>clock_timestamp()`
	result, err := s.d.Store.Pool.Exec(ctx, q, claim.id, claim.token, state, e, interval.Seconds())
	if err != nil {
		return err
	}
	if result.RowsAffected() == 0 {
		return ErrLeaseLost
	}
	if state == "failed" {
		observability.Inc("outbox_retries", 1)
	}
	return nil
}

func (s *Supervisor) retry(ctx context.Context, table string, claim lease, e string, interval time.Duration) error {
	if table == "delivery" {
		interval = 0
	}
	q := `UPDATE ` + jobTable(table) + ` SET
		state=CASE WHEN attempts<max_attempts THEN 'retry_wait' WHEN $4::double precision>0 THEN 'pending' ELSE 'failed' END,
		next_attempt_at=now()+make_interval(secs=>CASE WHEN attempts>=max_attempts AND $4>0 THEN $4 ELSE LEAST(3600,2^LEAST(attempts,12)) END),
		finished_at=CASE WHEN attempts>=max_attempts THEN now() ELSE NULL END,
		attempts=CASE WHEN attempts>=max_attempts AND $4>0 THEN 0 ELSE attempts END,
		last_error=$3,lease_until=NULL,lease_token='',updated_at=now()
		WHERE id=$1 AND lease_token=$2 AND state='leased' AND lease_until>clock_timestamp()`
	result, err := s.d.Store.Pool.Exec(ctx, q, claim.id, claim.token, e, interval.Seconds())
	if err != nil {
		return err
	}
	if result.RowsAffected() == 0 {
		return ErrLeaseLost
	}
	observability.Inc("outbox_retries", 1)
	return nil
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n] + "…"
	}
	return s
}

// Backlog reports pending/failed job counts for diagnostics.
func (s *Supervisor) Backlog(ctx context.Context) (map[string]int64, error) {
	out := map[string]int64{}
	var n int64
	if err := s.d.Store.Pool.QueryRow(ctx, `SELECT count(*) FROM background_jobs WHERE state IN ('pending','retry_wait')`).Scan(&n); err != nil {
		return nil, err
	}
	out["jobs_pending"] = n
	if err := s.d.Store.Pool.QueryRow(ctx, `SELECT count(*) FROM delivery_outbox WHERE state IN ('pending','retry_wait','leased')`).Scan(&n); err != nil {
		return nil, err
	}
	out["outbox_pending"] = n
	if err := s.d.Store.Pool.QueryRow(ctx, `SELECT count(*) FROM background_jobs WHERE state='failed'`).Scan(&n); err != nil {
		return nil, err
	}
	out["jobs_failed"] = n
	return out, nil
}
