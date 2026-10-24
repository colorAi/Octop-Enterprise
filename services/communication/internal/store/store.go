// Package store provides the PostgreSQL pool, migration runner, transaction
// helpers with bounded serialization retries, fixed lock ordering helpers, and
// sync-event emission. All business state lives in PostgreSQL (never Go maps).
package store

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"hash/fnv"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"octop/communication/internal/idgen"
	"octop/communication/migrations"
)

// Store wraps the connection pool.
type Store struct {
	Pool           *pgxpool.Pool
	writeAdmission sync.RWMutex
	activeWrites   atomic.Int64
}

// Open connects with pool bounds and verifies the server responds.
func Open(ctx context.Context, dsn string, maxConns int32) (*Store, error) {
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, fmt.Errorf("database url: %w", err)
	}
	cfg.MaxConns = maxConns
	cfg.MinConns = 1
	cfg.MaxConnLifetime = 30 * time.Minute
	cfg.MaxConnIdleTime = 5 * time.Minute
	cfg.HealthCheckPeriod = 30 * time.Second
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}
	pingCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if err := pool.Ping(pingCtx); err != nil {
		pool.Close()
		return nil, err
	}
	return &Store{Pool: pool}, nil
}

func (s *Store) Close() { s.Pool.Close() }

// Healthy reports whether a simple query succeeds (for /readyz).
func (s *Store) Healthy(ctx context.Context) bool {
	ctx2, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	var one int
	return s.Pool.QueryRow(ctx2, "SELECT 1").Scan(&one) == nil
}

// ---------------------------------------------------------------- migrations

// Migrate applies embedded SQL files in version order inside transactions.
// It never drops user data and refuses to run if a future version is present.
func (s *Store) Migrate(ctx context.Context) ([]int64, error) {
	if _, err := s.Pool.Exec(ctx, `CREATE TABLE IF NOT EXISTS schema_migrations (
		version bigint PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())`); err != nil {
		return nil, err
	}
	entries, err := migrations.FS.ReadDir(".")
	if err != nil {
		return nil, err
	}
	var maximum int64
	for _, entry := range entries {
		var v int64
		if !entry.IsDir() && len(entry.Name()) > 4 && entry.Name()[len(entry.Name())-4:] == ".sql" {
			if _, e := fmt.Sscanf(entry.Name(), "%04d_", &v); e != nil {
				return nil, e
			}
			if v > maximum {
				maximum = v
			}
		}
	}
	var applied int64
	if err := s.Pool.QueryRow(ctx, `SELECT COALESCE(MAX(version),0) FROM schema_migrations`).Scan(&applied); err != nil {
		return nil, err
	}
	if applied > maximum {
		return nil, fmt.Errorf("database has a newer communication schema")
	}
	var versions []int64
	for _, e := range entries {
		if e.IsDir() || len(e.Name()) < 5 || e.Name()[len(e.Name())-4:] != ".sql" {
			continue
		}
		var v int64
		if _, err := fmt.Sscanf(e.Name(), "%04d_", &v); err != nil {
			return nil, fmt.Errorf("migration %s: bad name", e.Name())
		}
		var exists bool
		if err := s.Pool.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=$1)", v).Scan(&exists); err != nil {
			return nil, err
		}
		if exists {
			continue
		}
		body, err := migrations.FS.ReadFile(e.Name())
		if err != nil {
			return nil, err
		}
		err = pgx.BeginFunc(ctx, s.Pool, func(tx pgx.Tx) error {
			if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(724001)"); err != nil { // global migration lock
				return err
			}
			if _, err := tx.Exec(ctx, "CREATE TABLE IF NOT EXISTS schema_migrations (version bigint PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"); err != nil {
				return err
			}
			var already bool
			if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=$1)`, v).Scan(&already); err != nil {
				return err
			}
			if already {
				return nil
			}
			if _, err := tx.Exec(ctx, string(body)); err != nil {
				return fmt.Errorf("migration %s: %w", e.Name(), err)
			}
			_, err := tx.Exec(ctx, "INSERT INTO schema_migrations(version) VALUES($1)", v)
			return err
		})
		if err != nil {
			return versions, err
		}
		versions = append(versions, v)
	}
	return versions, nil
}

// ---------------------------------------------------------------- schema shape

// shapeChecks are the things a version number cannot express. Baseline
// migrations are edited in place for unreleased features, and the runner applies
// each version exactly once — so a database that was already migrated silently
// keeps the OLD shape while the binary speaks the new one. That failure
// otherwise shows up hundreds of requests later as a random 500.
//
// Each entry states one thing this binary requires of the schema. Checking that
// an object is *gone* (want=false) adds nothing on top of that: whatever
// replaced it is missing from a stale database, so the positive check is the one
// that fires.
var shapeChecks = []struct {
	desc, table, column string
	want                bool
}{
	{desc: "communication identity", table: "users", column: "identity_version", want: true},
	{desc: "account portraits", table: "users", column: "avatar_url", want: true},
	{desc: "company positions", table: "users", column: "job_title_id", want: true},
	{desc: "directory admin flag", table: "users", column: "is_admin", want: true},
	{desc: "resource membership revision", table: "users", column: "resource_revision", want: true},
	{desc: "resource move receipts", table: "resource_department_operations", want: true},
	{desc: "departments", table: "departments", want: true},
	{desc: "file attachments", table: "media_assets", column: "filename", want: true},
	{desc: "group types", table: "group_settings", column: "group_kind", want: true},
	{desc: "group policy version", table: "group_settings", column: "permission_version", want: true},
	{desc: "company group", table: "company_group", want: true},
	{desc: "explicit membership grants", table: "group_member_grants", want: true},
	{desc: "members", table: "member_epochs", want: true},
	{desc: "sync", table: "user_sync_heads", column: "min_seq", want: true},
	{desc: "job lease fencing", table: "background_jobs", column: "lease_token", want: true},
	{desc: "delivery lease fencing", table: "delivery_outbox", column: "lease_token", want: true},
}

// CheckShape verifies the database has the shape this binary was built for.
func (s *Store) CheckShape(ctx context.Context) error {
	var version, count int
	if e := s.Pool.QueryRow(ctx, `SELECT COALESCE(MAX(version),0),count(*) FROM schema_migrations`).Scan(&version, &count); e != nil {
		return e
	}
	if version != 16 || count != 16 {
		return fmt.Errorf("communication schema version mismatch; apply the matching migrations without rebuilding data")
	}

	for _, c := range shapeChecks {
		var found bool
		var err error
		if c.column == "" {
			err = s.Pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM information_schema.tables
				WHERE table_schema=current_schema() AND table_name=$1)`, c.table).Scan(&found)
		} else {
			err = s.Pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM information_schema.columns
				WHERE table_schema=current_schema() AND table_name=$1 AND column_name=$2)`,
				c.table, c.column).Scan(&found)
		}
		if err != nil {
			return fmt.Errorf("check schema shape: %w", err)
		}
		if found == c.want {
			continue
		}
		verb := "缺少"
		if found {
			verb = "仍存在"
		}
		return fmt.Errorf("数据库结构与当前二进制不一致（%s%s）；请检查通信服务迁移与部署版本；禁止自动重建生产数据库", c.desc, verb)
	}
	return nil
}

// ---------------------------------------------------------------- audit

func retryableTxErr(err error) bool {
	var pe *pgconn.PgError
	if errors.As(err, &pe) {
		return pe.Code == "40001" || pe.Code == "40P01" || pe.Code == "41000"
	}
	return false
}

// Tx runs fn inside a read-committed transaction with up to 4 attempts on
// serialization failures/deadlocks. fn is re-run with the SAME inputs; callers
// must keep idempotency decisions inside the transaction.
func (s *Store) Tx(ctx context.Context, fn func(pgx.Tx) error) error {
	return s.TxLevel(ctx, "", fn)
}

// TxLevel additionally sets a transaction isolation level when non-empty.
func (s *Store) TxLevel(ctx context.Context, level string, fn func(pgx.Tx) error) error {
	var last error
	for attempt := 0; attempt < 5; attempt++ {
		tx, err := s.Pool.BeginTx(ctx, pgx.TxOptions{})
		if err != nil {
			return err
		}
		if level != "" {
			if _, err := tx.Exec(ctx, "SET TRANSACTION ISOLATION LEVEL "+level); err != nil {
				_ = tx.Rollback(ctx)
				return err
			}
		}
		// Version one serializes organization changes with conversation mutations.
		// A single ordering prevents department moves racing with sends or membership checks.
		if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(724002)"); err == nil {
			err = fn(tx)
		}
		if err != nil {
			_ = tx.Rollback(ctx)
			if retryableTxErr(err) && attempt < 4 {
				last = err
				time.Sleep(time.Duration(10<<attempt) * time.Millisecond)
				continue
			}
			return err
		}
		if err := tx.Commit(ctx); err != nil {
			if retryableTxErr(err) && attempt < 4 {
				last = err
				time.Sleep(time.Duration(10<<attempt) * time.Millisecond)
				continue
			}
			return err
		}
		return nil
	}
	if last == nil {
		last = errors.New("transaction retries exhausted")
	}
	return last
}

// ---------------------------------------------------------------- locks

// AdvisoryXactLock takes a transaction-scoped advisory lock on an int64 key.
func AdvisoryXactLock(ctx context.Context, tx pgx.Tx, key int64) error {
	_, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", key)
	return err
}

// PairKey hashes an unordered user pair into a stable int64 (locks in sorted order).
func PairKey(a, b string) int64 {
	lo, hi := a, b
	if hi < lo {
		lo, hi = hi, lo
	}
	h := fnv.New64a()
	h.Write([]byte("pair:" + lo + "\x00" + hi))
	v := h.Sum64() &^ (1 << 63) // keep positive for pgint8 advisory namespace
	return int64(v)
}

// LockFriendPair locks the unordered pair for relationship transitions.
func LockFriendPair(ctx context.Context, tx pgx.Tx, a, b string) error {
	return AdvisoryXactLock(ctx, tx, PairKey(a, b))
}

// LockConversation locks the conversation row (seq allocator + membership ops).
// Returns the current last_seq.
func LockConversation(ctx context.Context, tx pgx.Tx, convID string) (lastSeq int64, err error) {
	err = tx.QueryRow(ctx, `SELECT last_seq FROM conversations WHERE id=$1 FOR UPDATE`, convID).Scan(&lastSeq)
	return
}

// LockAssets locks media asset rows in deterministic id order.
func LockAssets(ctx context.Context, tx pgx.Tx, assetIDs []string) ([]string, error) {
	ids := append([]string(nil), assetIDs...)
	sort.Strings(ids)
	for _, id := range ids {
		var got string
		err := tx.QueryRow(ctx, `SELECT id FROM media_assets WHERE id=$1 FOR UPDATE`, id).Scan(&got)
		if err != nil {
			return ids, err
		}
	}
	return ids, nil
}

// LockUserSync heads locks (creating if needed) the given users in sorted order
// and returns their current counters.
func LockUserSync(ctx context.Context, tx pgx.Tx, userIDs []string) (map[string]int64, error) {
	ids := append([]string(nil), userIDs...)
	sort.Strings(ids)
	out := make(map[string]int64, len(ids))
	for _, uid := range ids {
		var c int64
		if _, err := tx.Exec(ctx, `INSERT INTO user_sync_heads(user_id) VALUES($1) ON CONFLICT DO NOTHING`, uid); err != nil {
			return nil, err
		}
		if err := tx.QueryRow(ctx, `SELECT counter FROM user_sync_heads WHERE user_id=$1 FOR UPDATE`, uid).Scan(&c); err != nil {
			return nil, err
		}
		out[uid] = c
	}
	return out, nil
}

// EmitSync appends one sync event for a user; call AFTER LockUserSync in the
// same tx, or let it lock itself when the user is not locked yet.
func EmitSync(ctx context.Context, tx pgx.Tx, userID, typ, objectID string, version int64, payload any) (int64, error) {
	var c int64
	if _, err := tx.Exec(ctx, `INSERT INTO user_sync_heads(user_id) VALUES($1) ON CONFLICT DO NOTHING`, userID); err != nil {
		return 0, err
	}
	if err := tx.QueryRow(ctx, `SELECT counter FROM user_sync_heads WHERE user_id=$1 FOR UPDATE`, userID).Scan(&c); err != nil {
		return 0, err
	}
	c++
	raw := []byte("{}")
	if payload != nil {
		b, err := json.Marshal(payload)
		if err != nil {
			return 0, err
		}
		raw = b
	}
	if _, err := tx.Exec(ctx, `INSERT INTO user_sync_events(user_id, seq, type, object_id, version, payload)
		VALUES($1,$2,$3,$4,$5,$6)`, userID, c, typ, objectID, version, raw); err != nil {
		return 0, err
	}
	if _, err := tx.Exec(ctx, `UPDATE user_sync_heads SET counter=$2, updated_at=now() WHERE user_id=$1`, userID, c); err != nil {
		return 0, err
	}
	if err := EnqueueDelivery(ctx, tx, "sync_wakeup", userID, "sync:"+userID+":"+BigStr(c), map[string]any{"user_id": userID}, time.Time{}); err != nil {
		return 0, err
	}
	return c, nil
}

// NextSeq increments and returns the conversation sequence (after LockConversation).
func NextSeq(ctx context.Context, tx pgx.Tx, convID string) (int64, error) {
	var seq int64
	err := tx.QueryRow(ctx, `UPDATE conversations SET last_seq = last_seq + 1, updated_at = now() WHERE id=$1 RETURNING last_seq`, convID).Scan(&seq)
	return seq, err
}

// EnqueueJob inserts a background job (skips duplicates on work_key).
func EnqueueJob(ctx context.Context, tx pgx.Tx, kind, workKey string, payload any, next time.Time) error {
	raw := []byte("{}")
	if payload != nil {
		b, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		raw = b
	}
	_, err := tx.Exec(ctx, `INSERT INTO background_jobs(id, kind, work_key, payload, next_attempt_at)
		VALUES($1,$2,$3,$4,$5) ON CONFLICT (work_key) DO NOTHING`,
		idgen.New("job"), kind, workKey, raw, nextUTC(next))
	return err
}

// EnqueueDelivery inserts a delivery outbox row (skips duplicates).
func EnqueueDelivery(ctx context.Context, tx pgx.Tx, kind, recipient, workKey string, payload any, next time.Time) error {
	raw := []byte("{}")
	if payload != nil {
		b, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		raw = b
	}
	_, err := tx.Exec(ctx, `INSERT INTO delivery_outbox(id, kind, recipient_user_id, work_key, payload, next_attempt_at)
		VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT (work_key) DO NOTHING`,
		idgen.New("obx"), kind, recipient, workKey, raw, nextUTC(next))
	return err
}

func nextUTC(t time.Time) time.Time {
	if t.IsZero() {
		return time.Now().UTC()
	}
	return t.UTC()
}

// ---------------------------------------------------------------- maintenance flag

// MaintenancePaused reports whether the backup maintenance window blocks writes.
func (s *Store) MaintenancePaused(ctx context.Context) bool {
	var v bool
	_ = s.Pool.QueryRow(ctx, `SELECT COALESCE((value->>'paused')::bool, false) FROM kv_settings WHERE key='maintenance'`).Scan(&v)
	return v
}

// SetMaintenanceFlag toggles the maintenance window (used by CLI backup).
func SetMaintenanceFlag(ctx context.Context, pool *pgxpool.Pool, paused bool) error {
	_, err := pool.Exec(ctx, `INSERT INTO kv_settings(key,value) VALUES('maintenance', jsonb_build_object('paused',$1::bool, 'updated_at', now()))
		ON CONFLICT (key) DO UPDATE SET value=jsonb_build_object('paused',$1::bool, 'updated_at', now()), updated_at=now()`, paused)
	return err
}

// Heartbeat updates this instance's write gauge for the CLI backup window.
func (s *Store) Heartbeat(ctx context.Context, instanceID string, inFlight int, accepting bool) error {
	// Only acknowledge a quiet maintenance window while holding admission
	// exclusively. A request that already passed the gate cannot start later.
	if s.writeAdmission.TryLock() {
		defer s.writeAdmission.Unlock()
		paused, err := s.maintenanceState(ctx)
		if err != nil {
			return err
		}
		accepting = accepting && !paused
	} else {
		inFlight = max(inFlight, max(1, int(s.activeWrites.Load())))
	}
	_, err := s.Pool.Exec(ctx, `INSERT INTO server_heartbeats(instance_id, writes_in_flight, accepting, updated_at)
		VALUES($1,$2,$3, now())
		ON CONFLICT (instance_id) DO UPDATE SET writes_in_flight=$2, accepting=$3, updated_at=now()`,
		instanceID, inFlight, accepting)
	return err
}

// BigStr serializes int64 counters per the 64-bit-as-string contract.
func BigStr(v int64) string { return fmt.Sprintf("%d", v) }

// HashU64 helper for hash-based advisory keys (used by tests/utilities).
func HashU64(s string) int64 {
	b := make([]byte, 8)
	h := fnv.New64a()
	h.Write([]byte(s))
	binary.BigEndian.PutUint64(b, h.Sum64()&^uint64(1<<63))
	return int64(binary.BigEndian.Uint64(b))
}
