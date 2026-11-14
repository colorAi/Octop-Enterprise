package itest

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"octop/communication/internal/experts"
	"octop/communication/internal/store"
)

func TestGroupExpertPerCallJobsFinishOnce(t *testing.T) {
	h, conv, _ := expertSetup(t)
	binding := inviteExpert(h, conv, 0)["binding_id"].(string)
	call := askExpert(h, conv, binding, "one-shot-jobs", "")["expert_call"].(map[string]any)["call_id"].(string)
	admitExpert(h, call)
	if err := h.App.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(h.App.Stop)
	deadline := time.Now().Add(3 * time.Second)
	for {
		var total, pending int
		err := h.D.Store.Pool.QueryRow(context.Background(), `SELECT count(*),count(*) FILTER(WHERE state NOT IN ('succeeded','skipped')) FROM background_jobs WHERE work_key IN ($1,$2)`, "register:"+call, "control:"+call+":1").Scan(&total, &pending)
		if err != nil {
			t.Fatal(err)
		}
		if total == 2 && pending == 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("per-call jobs did not finish: total=%d pending=%d", total, pending)
		}
		time.Sleep(20 * time.Millisecond)
	}
	var recurring int
	if err := h.D.Store.Pool.QueryRow(context.Background(), `SELECT count(*) FROM background_jobs WHERE kind='group.expert.reconcile' AND work_key<>'group.expert.reconcile'`).Scan(&recurring); err != nil || recurring != 0 {
		t.Fatalf("legacy recurring rows=%d err=%v", recurring, err)
	}
}

func TestGroupExpertReconcileReleasesGroupBeforeNextAuthorization(t *testing.T) {
	h, conv, _ := expertSetup(t)
	b0 := inviteExpert(h, conv, 0)["binding_id"].(string)
	b1 := inviteExpert(h, conv, 1)["binding_id"].(string)
	first := askExpert(h, conv, b0, "lock-first", "")["expert_call"].(map[string]any)["call_id"].(string)
	second := askExpert(h, conv, b1, "lock-second", "")["expert_call"].(map[string]any)["call_id"].(string)
	admitExpert(h, first)
	admitExpert(h, second)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	tx, err := h.D.Store.Pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(context.Background())
	var pid int
	if err = tx.QueryRow(ctx, `SELECT pg_backend_pid()`).Scan(&pid); err != nil {
		t.Fatal(err)
	}
	if _, err = tx.Exec(ctx, `SET LOCAL deadlock_timeout='100ms'`); err != nil {
		t.Fatal(err)
	}
	if _, err = tx.Exec(ctx, `UPDATE group_expert_authorizations SET revision=revision+1 WHERE domain_key='source:source-1:11'`); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- experts.New(h.D).Reconcile(ctx, nil) }()
	blocked := false
	for deadline := time.Now().Add(2 * time.Second); time.Now().Before(deadline); {
		if err = h.D.Store.Pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE $1=ANY(pg_blocking_pids(pid)))`, pid).Scan(&blocked); err != nil {
			t.Fatal(err)
		}
		if blocked {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if !blocked {
		t.Fatal("reconcile did not reach the second authorization")
	}
	if _, err = store.LockConversation(ctx, tx, conv); err != nil {
		t.Fatalf("reconcile retained the earlier group lock: %v", err)
	}
	tx.Rollback(context.Background())
	if err = <-done; err != nil {
		t.Fatal(err)
	}
}

func TestGroupExpertReconcileIsolatesCallFailureWithoutRecurringBackoff(t *testing.T) {
	h, conv, _ := expertSetup(t)
	binding := inviteExpert(h, conv, 0)["binding_id"].(string)
	bad := askExpert(h, conv, binding, "bad-watchdog-call", "")["expert_call"].(map[string]any)["call_id"].(string)
	good := askExpert(h, conv, binding, "good-watchdog-call", "")["expert_call"].(map[string]any)["call_id"].(string)
	admitExpert(h, bad)
	admitExpert(h, good)
	ctx := context.Background()
	if _, err := h.D.Store.Pool.Exec(ctx, `UPDATE group_expert_calls SET updated_at=now()-interval '2 hours'`); err != nil {
		t.Fatal(err)
	}
	// Inject a persistent row-specific database error. Other calls in the same
	// pass must still commit, and the periodic watchdog must retain its cadence.
	_, err := h.D.Store.Pool.Exec(ctx, fmt.Sprintf(`CREATE FUNCTION fail_one_expert_call() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.call_id='%s' THEN RAISE EXCEPTION 'test call failure'; END IF; RETURN NEW; END; $$;
CREATE TRIGGER fail_one_expert_call BEFORE UPDATE ON group_expert_calls FOR EACH ROW EXECUTE FUNCTION fail_one_expert_call()`, bad))
	if err != nil {
		t.Fatal(err)
	}
	service := experts.New(h.D)
	if err = service.Reconcile(ctx, nil); err != nil {
		t.Fatalf("one call backed off the entire recurring job: %v", err)
	}
	state := expertOK(h, admin, "GET", "/calls/"+good, nil)["call"].(map[string]any)["call_state"]
	if state != "cancelled" {
		t.Fatalf("healthy call did not advance: %v", state)
	}
	payload, _ := json.Marshal(map[string]string{"call_id": bad})
	if err = service.Reconcile(ctx, payload); err == nil {
		t.Fatal("the single-call job must still report its own retryable failure")
	}
}

func TestGroupExpertListingReadsButDoesNotRenewRuntimeProof(t *testing.T) {
	h, conv, _ := expertSetup(t)
	ctx := context.Background()
	body := map[string]any{"actor_id": admin, "organization": true}
	inspect := func() bool {
		return expertOK(h, admin, "POST", "/groups/"+conv+"/inspect", body)["ready"].(bool)
	}
	if !inspect() {
		t.Fatal("fresh runtime proof was not visible")
	}
	if _, err := h.D.Store.Pool.Exec(ctx, `UPDATE group_expert_runtime SET valid_until=now()-interval '1 second'`); err != nil {
		t.Fatal(err)
	}
	if inspect() || inspect() {
		t.Fatal("listing renewed an expired runtime proof")
	}
	expertOK(h, admin, "POST", "/runtime", map[string]any{"protocol": 2})
	if !inspect() {
		t.Fatal("heartbeat did not restore readiness")
	}
}

func TestGroupExpertPermanentResultFailureAdvancesQueue(t *testing.T) {
	h, conv, _ := expertSetup(t)
	binding := inviteExpert(h, conv, 0)["binding_id"].(string)
	first := askExpert(h, conv, binding, "bad-result", "")["expert_call"].(map[string]any)["call_id"].(string)
	second := askExpert(h, conv, binding, "next-result", "")["expert_call"].(map[string]any)["call_id"].(string)
	admitExpert(h, first)
	admitExpert(h, second)
	payload := expertResult(h, first, "completed")
	var sealed map[string]any
	if err := json.Unmarshal([]byte(payload["result_wire"].(string)), &sealed); err != nil {
		t.Fatal(err)
	}
	sealed["result"].(map[string]any)["text"] = strings.Repeat("x", 8001)
	payload = sealExpertResult(t, sealed)
	expertOK(h, admin, "POST", "/calls/"+first+"/delivery", map[string]any{"run_id": "run-" + first, "result_digest": payload["result_digest"]})
	receipt := expertOK(h, admin, "POST", "/calls/"+first+"/result", payload)
	if receipt["call_state"] != "failed" || receipt["reason_code"] != "group_expert_result_invalid" || receipt["message_id"] != nil {
		t.Fatal(receipt)
	}
	retry := expertOK(h, admin, "POST", "/calls/"+first+"/result", payload)
	if fmt.Sprint(retry) != fmt.Sprint(receipt) {
		t.Fatal("failure receipt changed on retry")
	}
	if state := expertOK(h, admin, "GET", "/calls/"+second, nil)["call"].(map[string]any)["call_state"]; state != "dispatched" {
		t.Fatalf("queue stuck: %v", state)
	}
}

func TestGroupExpertTextBudgetStaysFixedAfterConfigChange(t *testing.T) {
	h, conv, _ := expertSetup(t)
	binding := inviteExpert(h, conv, 0)["binding_id"].(string)
	call := askExpert(h, conv, binding, "fixed-budget", "")["expert_call"].(map[string]any)["call_id"].(string)
	admitExpert(h, call)
	h.D.Cfg.Limits.MaxTextChars = 5
	if maximum := expertOK(h, admin, "GET", "/calls/"+call, nil)["identity"].(map[string]any)["max_text_chars"]; maximum != float64(8000) {
		t.Fatal(maximum)
	}
	if receipt := expertOK(h, admin, "POST", "/calls/"+call+"/result", expertResult(h, call, "completed")); receipt["call_state"] != "completed" {
		t.Fatal(receipt)
	}
}

func TestGroupExpertReadsDoNotWaitForConversationLock(t *testing.T) {
	h, conv, _ := expertSetup(t)
	binding := inviteExpert(h, conv, 0)["binding_id"].(string)
	call := askExpert(h, conv, binding, "unlocked-read", "")["expert_call"].(map[string]any)["call_id"].(string)
	admitExpert(h, call)
	ctx := context.Background()
	tx, err := h.D.Store.Pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if _, err = store.LockConversation(ctx, tx, conv); err != nil {
		t.Fatal(err)
	}
	done := make(chan bool, 1)
	go func() {
		ok := true
		for _, path := range []string{"/calls/" + call, "/calls/" + call + "/context", "/calls/" + call + "/receipt"} {
			code, _ := expertInternal(h, admin, "GET", path, nil)
			ok = ok && code == 200
		}
		code, _ := expertInternal(h, admin, "POST", "/groups/"+conv+"/inspect", map[string]any{"actor_id": admin, "organization": true})
		ok = ok && code == 200
		code, _ = h.request(alice, "GET", "/groups/"+conv+"/expert-calls/"+call, nil)
		ok = ok && code == 200
		done <- ok
	}()
	select {
	case ok := <-done:
		if !ok {
			t.Fatal("read rejected")
		}
	case <-time.After(time.Second):
		tx.Rollback(ctx)
		t.Fatal("read took the group mutation lock")
	}
}

func TestGroupExpertWatchdogAndRevocationReason(t *testing.T) {
	h, conv, _ := expertSetup(t)
	binding := inviteExpert(h, conv, 0)["binding_id"].(string)
	first := askExpert(h, conv, binding, "admission-lost", "")["expert_call"].(map[string]any)["call_id"].(string)
	second := askExpert(h, conv, binding, "waiting-call", "")["expert_call"].(map[string]any)["call_id"].(string)
	admitExpert(h, second)
	ctx := context.Background()
	service := experts.New(h.D)
	if _, err := h.D.Store.Pool.Exec(ctx, `UPDATE group_expert_calls SET updated_at=now()-interval '6 minutes' WHERE call_id=$1`, first); err != nil {
		t.Fatal(err)
	}
	if err := service.Reconcile(ctx, nil); err != nil {
		t.Fatal(err)
	}
	if state := expertOK(h, admin, "GET", "/calls/"+second, nil)["call"].(map[string]any)["call_state"]; state != "dispatched" {
		t.Fatal(state)
	}
	h.identity(alice, "disabled", 2)
	if err := service.Reconcile(ctx, nil); err != nil {
		t.Fatal(err)
	}
	meta := expertOK(h, admin, "GET", "/calls/"+second, nil)["call"].(map[string]any)
	if meta["reason_code"] != "group_expert_send_not_allowed" || meta["call_state"] != "cancel_requested" {
		t.Fatal(meta)
	}
	if _, err := h.D.Store.Pool.Exec(ctx, `UPDATE group_expert_calls SET updated_at=now()-interval '3 minutes' WHERE call_id=$1`, second); err != nil {
		t.Fatal(err)
	}
	if err := service.Reconcile(ctx, nil); err != nil {
		t.Fatal(err)
	}
	if state := expertOK(h, admin, "GET", "/calls/"+second, nil)["call"].(map[string]any)["call_state"]; state != "unknown" {
		t.Fatal(state)
	}
}

func sealExpertResult(t *testing.T, value map[string]any) map[string]any {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(raw)
	return map[string]any{"result_wire": string(raw), "result_digest": hex.EncodeToString(sum[:])}
}
