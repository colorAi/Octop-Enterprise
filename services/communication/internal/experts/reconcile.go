package experts

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	"octop/communication/internal/httpx"
)

// Reconcile only performs bounded local work. The Python service polls durable
// registration/control obligations, so no model request occupies a Go job slot.
func (s *Service) Reconcile(ctx context.Context, payload json.RawMessage) error {
	var item struct {
		Call string `json:"call_id"`
	}
	if len(payload) > 0 {
		if e := json.Unmarshal(payload, &item); e != nil {
			return e
		}
	}
	// Selection takes no mutation locks. Each call has its own transaction, so
	// the next call never acquires authorizations while holding a previous group.
	s.reconcileMu.Lock()
	after := s.reconcileAfter
	if item.Call != "" {
		after = ""
	}
	rows, e := s.D.Store.Pool.Query(ctx, `SELECT call_id FROM group_expert_calls WHERE call_state IN ('pending_admission','queued','dispatched','awaiting_delivery','cancel_requested') AND ($1='' OR call_id=$1) AND call_id>$2 ORDER BY call_id LIMIT 200`, item.Call, after)
	if e != nil {
		s.reconcileMu.Unlock()
		return e
	}
	ids := []string{}
	for rows.Next() {
		var id string
		if e = rows.Scan(&id); e != nil {
			break
		}
		ids = append(ids, id)
	}
	if e == nil {
		e = rows.Err()
	}
	rows.Close()
	if item.Call == "" && e == nil {
		if len(ids) == 200 {
			s.reconcileAfter = ids[len(ids)-1]
		} else {
			s.reconcileAfter = ""
		}
	}
	s.reconcileMu.Unlock()
	if e != nil {
		return e
	}
	var failures []error
	for _, id := range ids {
		e = s.D.Store.Tx(ctx, func(tx pgx.Tx) error {
			c, e := lockCall(ctx, tx, id)
			if e != nil {
				return e
			}
			if terminal(c.State) {
				return nil
			}
			age := time.Duration(c.AgeSeconds * float64(time.Second))
			if c.State == "cancel_requested" {
				if age < 2*time.Minute {
					return nil
				}
				if e = changed(ctx, tx, c, "unknown", c.Reason); e != nil {
					return e
				}
				return advance(ctx, tx, c.Conversation)
			}
			limit := time.Hour // Covers all bounded model and tool rounds.
			if c.State == "pending_admission" {
				limit = 5 * time.Minute
			}
			if c.State == "awaiting_delivery" {
				limit = 10 * time.Minute
			}
			if age > limit {
				return cancel(ctx, tx, c, "group_expert_timed_out")
			}
			e = versionsMatch(ctx, tx, c)
			if e == nil {
				_, e = contextFor(ctx, tx, c, true)
			}
			if e != nil {
				var he *httpx.Error
				if !errors.As(e, &he) {
					return e
				}
				if e = cancel(ctx, tx, c, he.Code); e != nil {
					return e
				}
			}
			return advance(ctx, tx, c.Conversation)
		})
		if e != nil {
			failures = append(failures, e)
			s.D.Log.ErrorContext(ctx, "group expert reconciliation failed", "call_id", id, "error", e)
		}
		if ctx.Err() != nil {
			return errors.Join(append(failures, ctx.Err())...)
		}
	}
	// This only acknowledges facts; it never locks a group after an outbox row.
	e = s.D.Store.Tx(ctx, func(tx pgx.Tx) error {
		// The latched revocation remains as a tombstone, even after confirmation.
		_, e := tx.Exec(ctx, `UPDATE group_expert_outbox o SET acknowledged_at=now(),receipt='{"saved":true}' WHERE kind='recall' AND acknowledged_at IS NULL AND EXISTS(SELECT 1 FROM group_expert_calls c WHERE c.call_id=o.target_id AND c.call_state IN ('cancel_requested','cancelled','completed','failed','unknown','rejected'))`)
		return e
	})
	if item.Call != "" {
		// A single-call job still retries its own failure normally.
		return errors.Join(append(failures, e)...)
	}
	// Individual bad calls remain selected on subsequent passes. They must not
	// exponentially back off the watchdog for every other group. Infrastructure
	// failures in selection/acknowledgment still reach the scheduler.
	return e
}
