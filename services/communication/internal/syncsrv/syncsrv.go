// Package syncsrv exposes the per-user incremental sync stream: cursor-based
// event polling, expirable snapshots, and the retention purge worker.
package syncsrv

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"

	"octop/communication/internal/auth"
	"octop/communication/internal/deps"
	"octop/communication/internal/httpx"
	"octop/communication/internal/idgen"
	"octop/communication/internal/jobs"
)

// Service wires dependencies.
type Service struct{ D *deps.Deps }

// New constructs the service.
func New(d *deps.Deps) *Service { return &Service{D: d} }

// Register mounts sync routes.
func (s *Service) Register(m *http.ServeMux) {
	a := func(h func(http.ResponseWriter, *http.Request)) http.HandlerFunc {
		return auth.Middleware(s.D, h)
	}
	m.HandleFunc("GET /api/app/v1/sync", a(s.handleSync))
	m.HandleFunc("POST /api/app/v1/sync/snapshot", a(s.handleSnapshotCreate))
	m.HandleFunc("GET /api/app/v1/sync/snapshots/{id}", a(s.handleSnapshotGet))
}

// cursorFor signs an opaque cursor for a user at a position.
func (s *Service) cursorFor(user string, seq int64) string {
	return s.D.Cursor.Encode(map[string]any{"u": user, "s": strconv.FormatInt(seq, 10)})
}

func (s *Service) parseCursor(r *http.Request, user string) (int64, error) {
	c := r.URL.Query().Get("cursor")
	if c == "" {
		return 0, nil
	}
	p, ok := s.D.Cursor.Decode(c)
	if !ok {
		return 0, httpx.ErrInvalidRequest.WithMessage("游标无效")
	}
	u, _ := p["u"].(string)
	if u != user {
		return 0, httpx.ErrInvalidRequest.WithMessage("游标不属于当前用户")
	}
	if encoded, ok := p["s"].(string); ok {
		seq, err := strconv.ParseInt(encoded, 10, 64)
		if err != nil || seq < 0 {
			return 0, httpx.ErrInvalidRequest.WithMessage("游标位置无效")
		}
		return seq, nil
	}
	// Accept old small numeric cursors, but do not silently round large ones.
	f, ok := p["s"].(float64)
	if !ok || f < 0 || f > 9007199254740991 || f != float64(int64(f)) {
		return 0, httpx.ErrCursorExpired.WithMessage("旧游标无法精确恢复，请获取快照")
	}
	return int64(f), nil
}

// eventView is the public event shape (seq as string).
type eventView struct {
	Seq      string          `json:"seq"`
	Type     string          `json:"type"`
	ObjectID string          `json:"object_id"`
	Version  string          `json:"version"`
	Payload  json.RawMessage `json:"payload"`
}

// handleSync streams events after the cursor position.
func (s *Service) handleSync(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	limit := httpx.PageParams(r, 200, s.D.Cfg.Limits.SyncPageMax)
	since, err := s.parseCursor(r, a.UserID)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	// The watermark and events must come from one MVCC snapshot: retention
	// may otherwise delete a page between these two reads.
	tx, err := s.D.Store.Pool.BeginTx(r.Context(), pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	defer tx.Rollback(r.Context())
	var head, minSeq int64
	err = tx.QueryRow(r.Context(), `SELECT counter, min_seq FROM user_sync_heads WHERE user_id=$1`, a.UserID).Scan(&head, &minSeq)
	if errors.Is(err, pgx.ErrNoRows) {
		head, minSeq = 0, 0
	} else if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	expired := func() {
		httpx.WriteError(w, r, httpx.ErrCursorExpired.WithMessage(
			"同步游标已过期（min_seq="+store1(minSeq)+" head_seq="+store1(head)+"），请获取快照"))
	}
	// min_seq is one past the greatest purged event. A cursor acknowledging
	// that event (min_seq-1) is valid, even when the retained stream is empty.
	if (minSeq > 0 && since < minSeq-1) || since > head {
		expired()
		return
	}
	rows, err := tx.Query(r.Context(), `SELECT seq, type, object_id, version, payload
		FROM user_sync_events WHERE user_id=$1 AND seq > $2 AND seq <= $4 ORDER BY seq LIMIT $3`, a.UserID, since, limit+1, head)
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	defer rows.Close()
	items := []eventView{}
	previous := since
	for rows.Next() {
		var seq, ver int64
		var typ, obj string
		var payload []byte
		if err := rows.Scan(&seq, &typ, &obj, &ver, &payload); err != nil {
			httpx.WriteErrorOf(w, r, httpx.ErrStorage)
			return
		}
		// Also recover from gaps left by older non-atomic purges. This is the
		// per-user event stream, not permission-filtered conversation history.
		if seq != previous+1 {
			expired()
			return
		}
		previous = seq
		items = append(items, eventView{Seq: strconv.FormatInt(seq, 10), Type: typ, ObjectID: obj,
			Version: strconv.FormatInt(ver, 10), Payload: payload})
	}
	if err := rows.Err(); err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	if len(items) <= limit && previous < head {
		expired()
		return
	}
	if err := tx.Commit(r.Context()); err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	hasMore := len(items) > limit
	if hasMore {
		items = items[:limit]
	}
	next := since
	if len(items) > 0 {
		n, _ := strconv.ParseInt(items[len(items)-1].Seq, 10, 64)
		next = n
	}
	httpx.WriteData(w, r, 200, map[string]any{
		"events":      items,
		"has_more":    hasMore,
		"cursor":      s.cursorFor(a.UserID, next),
		"head_seq":    store1(head),
		"server_time": time.Now().UTC().Format(time.RFC3339),
	})
}

// handleSnapshotCreate materializes all events up to the current head into
// chunked JSON that a client can replay when its cursor expired.
func (s *Service) handleSnapshotCreate(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	sid := idgen.New("snap")
	err := s.D.Store.TxLevel(r.Context(), "REPEATABLE READ", func(tx pgx.Tx) error {
		var head int64
		err := tx.QueryRow(r.Context(), `SELECT counter FROM user_sync_heads WHERE user_id=$1 FOR UPDATE`, a.UserID).Scan(&head)
		if errors.Is(err, pgx.ErrNoRows) {
			head = 0
		} else if err != nil {
			return err
		}
		if _, err := tx.Exec(r.Context(), `INSERT INTO sync_snapshots(id, user_id, until_seq, expires_at, chunk_count)
			VALUES($1,$2,$3, now() + make_interval(secs => $4::int), 0)`,
			sid, a.UserID, head, int32(s.D.Cfg.SnapshotTTL.Seconds())); err != nil {
			return err
		}
		// Snapshots are MATERIALISED FROM CURRENT BUSINESS STATE (never by
		// replaying the event log, which retention may have purged). Each
		// row becomes an idempotent upsert-shaped event; after applying a
		// snapshot, subsequent deltas continue from until_seq.
		type ev struct {
			Seq      string          `json:"seq"`
			Type     string          `json:"type"`
			ObjectID string          `json:"object_id"`
			Version  string          `json:"version"`
			Payload  json.RawMessage `json:"payload"`
		}
		seqStr := strconv.FormatInt(head, 10)
		var evs []ev
		entries, err := snapshotEntries(r.Context(), tx, a.UserID)
		if err != nil {
			return err
		}
		for _, entry := range entries {
			evs = append(evs, ev{Seq: seqStr, Type: entry.Type, ObjectID: entry.ID, Version: "0", Payload: entry.Payload})
		}
		chunks := 0
		for {
			part := evs
			if len(part) > 200 {
				part = part[:200]
			}
			raw, _ := json.Marshal(part)
			if _, err := tx.Exec(r.Context(), `INSERT INTO snapshot_chunks(snapshot_id, chunk_index, payload) VALUES($1,$2,$3)`,
				sid, chunks, string(raw)); err != nil {
				return err
			}
			chunks++
			evs = func() []ev {
				if len(evs) > 200 {
					return evs[200:]
				}
				return nil
			}()
			if len(evs) == 0 {
				break
			}
		}
		_, err = tx.Exec(r.Context(), `UPDATE sync_snapshots SET chunk_count=$2 WHERE id=$1`, sid, chunks)
		if err != nil {
			return err
		}
		return nil
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	var until int64
	var exp time.Time
	var cc int
	_ = s.D.Store.Pool.QueryRow(r.Context(), `SELECT until_seq, expires_at, chunk_count FROM sync_snapshots WHERE id=$1`, sid).
		Scan(&until, &exp, &cc)
	httpx.WriteData(w, r, 201, map[string]any{
		"snapshot_id": sid, "snapshot_type": "state_v1", "replace_state": true, "until_seq": store1(until), "chunk_count": cc,
		"expires_at": exp.UTC().Format(time.RFC3339),
		"cursor":     s.cursorFor(a.UserID, until),
	})
}

func (s *Service) handleSnapshotGet(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	sid := r.PathValue("id")
	idx := 0
	if v := r.URL.Query().Get("chunk"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 0 {
			httpx.WriteError(w, r, httpx.ErrInvalidRequest.WithMessage("chunk 需为非负整数"))
			return
		}
		idx = n
	}
	var owner string
	var until int64
	var exp time.Time
	var cc int
	err := s.D.Store.Pool.QueryRow(r.Context(), `SELECT user_id, until_seq, expires_at, chunk_count FROM sync_snapshots WHERE id=$1`, sid).
		Scan(&owner, &until, &exp, &cc)
	if errors.Is(err, pgx.ErrNoRows) {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	if err != nil || owner != a.UserID {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	if time.Now().After(exp) {
		httpx.WriteError(w, r, httpx.ErrCursorExpired.WithMessage("快照已过期"))
		return
	}
	var payload []byte
	err = s.D.Store.Pool.QueryRow(r.Context(), `SELECT payload FROM snapshot_chunks WHERE snapshot_id=$1 AND chunk_index=$2`, sid, idx).Scan(&payload)
	if errors.Is(err, pgx.ErrNoRows) {
		httpx.WriteError(w, r, httpx.ErrNotFound.WithMessage("分片不存在"))
		return
	}
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	httpx.WriteData(w, r, 200, map[string]any{
		"snapshot_id": sid, "snapshot_type": "state_v1", "replace_state": true, "chunk_index": idx, "chunk_count": cc, "events": json.RawMessage(payload),
		"until_seq": store1(until), "has_more": idx+1 < cc,
	})
}

// ---------------------------------------------------------------- worker

// NewPurgeJob deletes expired events and advances min_seq in the same atomic
// statement, in bounded batches. The supervisor schedules its next run.
func NewPurgeJob(d *deps.Deps) jobs.Handler {
	return func(ctx context.Context, payload json.RawMessage) error {
		var until time.Time
		if err := d.Store.Pool.QueryRow(ctx, `SELECT now() - make_interval(secs => $1::int)`,
			int32(d.Cfg.SyncRetention.Seconds())).Scan(&until); err != nil {
			return err
		}
		const batchSize = 1000
		for {
			var deleted int
			err := d.Store.Pool.QueryRow(ctx, `WITH batch AS (
				SELECT user_id,seq FROM user_sync_events WHERE created_at<$1 ORDER BY user_id,seq LIMIT $2
			), deleted AS (
				DELETE FROM user_sync_events e USING batch b WHERE e.user_id=b.user_id AND e.seq=b.seq RETURNING e.user_id,e.seq
			), advanced AS (
				INSERT INTO user_sync_heads(user_id,min_seq)
				SELECT user_id,max(seq)+1 FROM deleted GROUP BY user_id ORDER BY user_id
				ON CONFLICT(user_id) DO UPDATE SET min_seq=GREATEST(user_sync_heads.min_seq,EXCLUDED.min_seq)
				RETURNING user_id
			) SELECT count(*) FROM deleted`, until, batchSize).Scan(&deleted)
			if err != nil {
				return err
			}
			if deleted < batchSize {
				break
			}
		}
		if _, err := d.Store.Pool.Exec(ctx, `DELETE FROM sync_snapshots WHERE expires_at < now()`); err != nil {
			return err
		}
		return nil
	}
}

func store1(v int64) string { return strconv.FormatInt(v, 10) }
