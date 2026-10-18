package itest

import (
	"context"
	"net/url"
	"octop/communication/internal/syncsrv"
	"strconv"
	"testing"
	"time"
)

func TestRecurringMaintenanceSchedulesNextRun(t *testing.T) {
	for _, previous := range []string{"", "succeeded", "failed", "skipped"} {
		t.Run("previous_"+previous, func(t *testing.T) {
			h := newH(t)
			ctx := context.Background()
			if previous != "" {
				for _, kind := range []string{"sync.purge", "media.cleanup"} {
					if err := h.App.Sup.Enqueue(ctx, kind, kind, nil); err != nil {
						t.Fatal(err)
					}
					if _, err := h.D.Store.Pool.Exec(ctx, `UPDATE background_jobs SET state=$2 WHERE work_key=$1`, kind, previous); err != nil {
						t.Fatal(err)
					}
				}
			}
			if err := h.App.Start(ctx); err != nil {
				t.Fatal(err)
			}
			defer h.App.Stop()
			for round := 0; round < 2; round++ {
				deadline := time.Now().Add(3 * time.Second)
				for {
					var scheduled int
					if err := h.D.Store.Pool.QueryRow(ctx, `SELECT count(*) FROM background_jobs WHERE kind IN ('sync.purge','media.cleanup') AND state='pending' AND attempts=0 AND next_attempt_at>now() AND finished_at IS NOT NULL`).Scan(&scheduled); err != nil {
						t.Fatal(err)
					}
					if scheduled == 2 {
						break
					}
					if time.Now().After(deadline) {
						t.Fatalf("round %d: only %d maintenance tasks scheduled for their next run", round, scheduled)
					}
					time.Sleep(10 * time.Millisecond)
				}
				if round == 0 {
					if _, err := h.D.Store.Pool.Exec(ctx, `UPDATE background_jobs SET next_attempt_at=now(), finished_at=NULL WHERE kind IN ('sync.purge','media.cleanup')`); err != nil {
						t.Fatal(err)
					}
				}
			}
		})
	}
}

func TestSyncPurgeWatermarkFailureRollsBack(t *testing.T) {
	h := newH(t)
	ctx := context.Background()
	conv := h.direct(alice, bob)
	base := h.ok(bob, "POST", "/sync/snapshot", nil)
	first := h.send(alice, conv, "probe-purge-first", "first")
	h.send(alice, conv, "probe-purge-second", "second")
	if _, err := h.D.Store.Pool.Exec(ctx, `UPDATE user_sync_events SET created_at=now()-interval '48 hours' WHERE object_id=$1`, first["message_id"]); err != nil {
		t.Fatal(err)
	}
	if _, err := h.D.Store.Pool.Exec(ctx, `CREATE FUNCTION probe_fail_watermark() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected watermark failure'; END $$;
	CREATE TRIGGER probe_fail_watermark BEFORE UPDATE OF min_seq ON user_sync_heads FOR EACH ROW EXECUTE FUNCTION probe_fail_watermark()`); err != nil {
		t.Fatal(err)
	}
	err := syncsrv.NewPurgeJob(h.D)(ctx, nil)
	if err == nil {
		t.Fatal("expected injected failure")
	}
	var remaining int
	if e := h.D.Store.Pool.QueryRow(ctx, `SELECT count(*) FROM user_sync_events WHERE user_id=$1 AND object_id=$2`, bob, first["message_id"]).Scan(&remaining); e != nil {
		t.Fatal(e)
	}
	code, response := h.request(bob, "GET", "/sync?cursor="+url.QueryEscape(base["cursor"].(string)), nil)
	if remaining != 1 || code != 200 {
		t.Fatalf("unexpected state: remaining=%d code=%d response=%v", remaining, code, response)
	}
	data := response["data"].(map[string]any)
	events := data["events"].([]any)
	if len(events) != 2 {
		t.Fatalf("expected both events after rollback, got %v", events)
	}

}

func TestSyncFullyPurgedCursorExpires(t *testing.T) {
	h := newH(t)
	ctx := context.Background()
	conv := h.direct(alice, bob)
	base := h.ok(bob, "POST", "/sync/snapshot", nil)
	h.send(alice, conv, "probe-all-purged", "message")
	latest := h.ok(bob, "POST", "/sync/snapshot", nil)
	if _, err := h.D.Store.Pool.Exec(ctx, `UPDATE user_sync_events SET created_at=now()-interval '48 hours'`); err != nil {
		t.Fatal(err)
	}
	if err := syncsrv.NewPurgeJob(h.D)(ctx, nil); err != nil {
		t.Fatal(err)
	}
	code, response := h.request(bob, "GET", "/sync?cursor="+url.QueryEscape(base["cursor"].(string)), nil)
	if code != 410 || response["error"].(map[string]any)["code"] != "sync_cursor_expired" {
		t.Fatalf("expected expired cursor after full purge: code=%d response=%v", code, response)
	}
	data := h.ok(bob, "GET", "/sync?cursor="+url.QueryEscape(latest["cursor"].(string)), nil)
	if len(data["events"].([]any)) != 0 {
		t.Fatal("fully acknowledged stream should be empty")
	}
	h.send(alice, conv, "after-purge", "new message")
	data = h.ok(bob, "GET", "/sync?cursor="+url.QueryEscape(latest["cursor"].(string)), nil)
	if len(data["events"].([]any)) != 1 {
		t.Fatal("new events did not resume from the acknowledged boundary")
	}
}

func TestSyncPurgeBoundaryAndPagination(t *testing.T) {
	h := newH(t)
	ctx := context.Background()
	conv := h.direct(alice, bob)
	old := h.ok(bob, "POST", "/sync/snapshot", nil)
	first := h.send(alice, conv, "boundary-first", "old")
	boundary := h.ok(bob, "POST", "/sync/snapshot", nil)
	second := h.send(alice, conv, "boundary-second", "newer")
	third := h.send(alice, conv, "boundary-third", "newest")
	if _, err := h.D.Store.Pool.Exec(ctx, `UPDATE user_sync_events SET created_at=now()-interval '48 hours' WHERE object_id=$1`, first["message_id"]); err != nil {
		t.Fatal(err)
	}
	if err := syncsrv.NewPurgeJob(h.D)(ctx, nil); err != nil {
		t.Fatal(err)
	}
	if code, _ := h.request(bob, "GET", "/sync?cursor="+url.QueryEscape(old["cursor"].(string)), nil); code != 410 {
		t.Fatalf("old cursor accepted: %d", code)
	}
	page := h.ok(bob, "GET", "/sync?limit=1&cursor="+url.QueryEscape(boundary["cursor"].(string)), nil)
	if len(page["events"].([]any)) != 1 || page["has_more"] != true || page["events"].([]any)[0].(map[string]any)["object_id"] != second["message_id"] {
		t.Fatalf("wrong first retained page: %v", page)
	}
	page = h.ok(bob, "GET", "/sync?limit=1&cursor="+url.QueryEscape(page["cursor"].(string)), nil)
	if len(page["events"].([]any)) != 1 || page["has_more"] != false || page["events"].([]any)[0].(map[string]any)["object_id"] != third["message_id"] {
		t.Fatalf("wrong last retained page: %v", page)
	}
}

func TestSyncDetectsLegacyGapsWithoutWatermark(t *testing.T) {
	for _, gap := range []string{"first", "tail", "all"} {
		t.Run(gap, func(t *testing.T) {
			h := newH(t)
			conv := h.direct(alice, bob)
			base := h.ok(bob, "POST", "/sync/snapshot", nil)
			first := h.send(alice, conv, "gap-first", "first")
			last := h.send(alice, conv, "gap-last", "last")
			id := first["message_id"]
			if gap == "tail" {
				id = last["message_id"]
			}
			q := `DELETE FROM user_sync_events WHERE user_id=$1 AND (object_id=$2 OR $3)`
			if _, err := h.D.Store.Pool.Exec(context.Background(), q, bob, id, gap == "all"); err != nil {
				t.Fatal(err)
			}
			code, _ := h.request(bob, "GET", "/sync?cursor="+url.QueryEscape(base["cursor"].(string)), nil)
			if code != 410 {
				t.Fatalf("legacy %s gap was not rejected: %d", gap, code)
			}
		})
	}
}

func TestSyncReadSeesConsistentStateDuringPurge(t *testing.T) {
	h := newH(t)
	ctx := context.Background()
	conv := h.direct(alice, bob)
	base := h.ok(bob, "POST", "/sync/snapshot", nil)
	message := h.send(alice, conv, "concurrent-purge", "retained at read start")
	tx, err := h.D.Store.Pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `LOCK TABLE user_sync_events IN ACCESS EXCLUSIVE MODE`); err != nil {
		t.Fatal(err)
	}
	type result struct {
		code int
		body map[string]any
	}
	done := make(chan result, 1)
	go func() {
		code, body := h.request(bob, "GET", "/sync?cursor="+url.QueryEscape(base["cursor"].(string)), nil)
		done <- result{code, body}
	}()
	deadline := time.Now().Add(3 * time.Second)
	for {
		var waiting bool
		if err := h.D.Store.Pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE '%FROM user_sync_events WHERE user_id=%')`).Scan(&waiting); err != nil {
			t.Fatal(err)
		}
		if waiting {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("sync did not reach its event read")
		}
		time.Sleep(10 * time.Millisecond)
	}
	// Commit retention exactly between the HTTP reader's watermark and event reads.
	if _, err = tx.Exec(ctx, `DELETE FROM user_sync_events WHERE user_id=$1`, bob); err != nil {
		t.Fatal(err)
	}
	if _, err = tx.Exec(ctx, `UPDATE user_sync_heads SET min_seq=counter+1 WHERE user_id=$1`, bob); err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	select {
	case response := <-done:
		if response.code != 200 {
			t.Fatalf("in-progress snapshot failed: %v", response)
		}
		events := response.body["data"].(map[string]any)["events"].([]any)
		if len(events) != 1 || events[0].(map[string]any)["object_id"] != message["message_id"] {
			t.Fatalf("mixed pre/post-purge state: %v", events)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("sync did not finish")
	}
	if code, _ := h.request(bob, "GET", "/sync?cursor="+url.QueryEscape(base["cursor"].(string)), nil); code != 410 {
		t.Fatalf("fresh read should see the new watermark: %d", code)
	}
}

func TestSyncPurgeBatchesPreserveRecentTail(t *testing.T) {
	h := newH(t)
	ctx := context.Background()
	var base int64
	if err := h.D.Store.Pool.QueryRow(ctx, `SELECT counter FROM user_sync_heads WHERE user_id=$1`, bob).Scan(&base); err != nil {
		t.Fatal(err)
	}
	if _, err := h.D.Store.Pool.Exec(ctx, `INSERT INTO user_sync_events(user_id,seq,type,created_at) SELECT $1,$2::bigint+n,'test',CASE WHEN n<=2500 THEN now()-interval '48 hours' ELSE now() END FROM generate_series(1,2501) n`, bob, base); err != nil {
		t.Fatal(err)
	}
	if _, err := h.D.Store.Pool.Exec(ctx, `UPDATE user_sync_heads SET counter=$2 WHERE user_id=$1`, bob, base+2501); err != nil {
		t.Fatal(err)
	}
	for round := 0; round < 2; round++ {
		if err := syncsrv.NewPurgeJob(h.D)(ctx, nil); err != nil {
			t.Fatal(err)
		}
	}
	var low int64
	var old int
	if err := h.D.Store.Pool.QueryRow(ctx, `SELECT min_seq,(SELECT count(*) FROM user_sync_events WHERE user_id=$1 AND created_at<now()-interval '24 hours') FROM user_sync_heads WHERE user_id=$1`, bob).Scan(&low, &old); err != nil {
		t.Fatal(err)
	}
	if low != base+2501 || old != 0 {
		t.Fatalf("batch cleanup failed: min=%d expired=%d", low, old)
	}
	cursor := h.D.Cursor.Encode(map[string]any{"u": bob, "s": strconv.FormatInt(low-1, 10)})
	page := h.ok(bob, "GET", "/sync?cursor="+url.QueryEscape(cursor), nil)
	if len(page["events"].([]any)) != 1 {
		t.Fatalf("recent tail lost: %v", page)
	}
}
