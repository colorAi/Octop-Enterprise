package itest

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"octop/communication/internal/auth"
	"octop/communication/internal/experts"
	"octop/communication/internal/store"
	"octop/communication/internal/testdb"
)

// This opt-in test exercises the real Python repository/runner/transport and
// the Go HTTP/message transaction together, with an in-process controlled model.
func TestGroupExpertPythonRoundTrip(t *testing.T) {
	if os.Getenv("OCTOP_GROUP_EXPERT_E2E") != "1" {
		t.Skip("set OCTOP_GROUP_EXPERT_E2E=1 to run the Python/Go acceptance test")
	}
	h := newH(t)
	conv := project(h, "cross-service-experts", alice, bob)
	if err := h.App.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(h.App.Stop)
	dsn := testdb.New(t)
	root, err := filepath.Abs("../../../..")
	if err != nil {
		t.Fatal(err)
	}
	input, _ := json.Marshal(map[string]any{"url": h.Server.URL, "dsn": dsn, "key": h.D.Cfg.IdentityKey, "group": conv, "department": h.Dept, "token": h.token(alice, "request", 1)})
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "uv", "run", "--no-sync", "python", "-m", "tests.integration.group_expert_roundtrip")
	cmd.Dir = root
	cmd.Stdin = bytes.NewReader(input)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("Python/Go roundtrip: %v\n%s", err, out)
	}
	t.Log(string(out))
}

func expertInternal(h *H, actor, method, path string, body any) (int, map[string]any) {
	h.t.Helper()
	raw := []byte{}
	if body != nil {
		raw, _ = json.Marshal(body)
	}
	sum := sha256.Sum256(raw)
	now := time.Now().Unix()
	path = "/internal/group-experts/v1" + path
	token := auth.Sign(auth.Claims{Issuer: "octop", Audience: "octop-im", Purpose: "group-experts-v1", UserID: actor, SessionID: "service", Issued: now, Expires: now + 15, Method: method, Path: path, BodyDigest: hex.EncodeToString(sum[:])}, h.D.Cfg.IdentityKey)
	req, _ := http.NewRequest(method, h.Server.URL+path, bytes.NewReader(raw))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	res, e := http.DefaultClient.Do(req)
	if e != nil {
		h.t.Fatal(e)
	}
	defer res.Body.Close()
	var out map[string]any
	if e = json.NewDecoder(res.Body).Decode(&out); e != nil {
		h.t.Fatal(e)
	}
	return res.StatusCode, out
}
func expertOK(h *H, actor, method, path string, body any) map[string]any {
	h.t.Helper()
	code, out := expertInternal(h, actor, method, path, body)
	if code != 200 {
		h.t.Fatalf("%s %s: %d %v", method, path, code, out)
	}
	return out["data"].(map[string]any)
}
func expertSetup(t *testing.T) (*H, string, []map[string]any) {
	h := newH(t)
	conv := project(h, "expert-project", alice, bob)
	expertOK(h, admin, "POST", "/runtime", map[string]any{"protocol": 2})
	sources := []map[string]any{}
	for i := 0; i < 4; i++ {
		domain := fmt.Sprintf("source:source-%d:%d", i, i+10)
		sources = append(sources, map[string]any{"source_agent_id": fmt.Sprintf("source-%d", i), "source_row_id": i + 10, "display_name": fmt.Sprintf("Expert %d", i), "avatar": map[string]any{}, "authorization_versions": map[string]int{domain: 1, "provider:generation": 1}})
		expertOK(h, admin, "PUT", "/authorizations/"+domain, map[string]any{"revision": 1})
	}
	for _, domain := range []string{"provider:generation", "user:1", "user:2", "user:3"} {
		expertOK(h, admin, "PUT", "/authorizations/"+domain, map[string]any{"revision": 1})
	}
	expertOK(h, admin, "PUT", "/groups/"+conv+"/scope", map[string]any{"revision": 1, "sources": sources, "operation_id": "scope-one"})
	expertOK(h, admin, "PUT", "/groups/"+conv+"/enabled", map[string]any{"request_id": "enable", "actor_id": admin, "organization": true, "enabled": true})
	return h, conv, sources
}
func inviteExpert(h *H, conv string, i int) map[string]any {
	return expertOK(h, admin, "POST", "/groups/"+conv+"/bindings", map[string]any{"request_id": fmt.Sprintf("invite-%d", i), "actor_id": admin, "revision": 1, "source_agent_id": fmt.Sprintf("source-%d", i), "source_row_id": i + 10})["binding"].(map[string]any)
}
func askExpert(h *H, conv, binding, key, quote string) map[string]any {
	h.t.Helper()
	return h.ok(alice, "POST", "/conversations/"+conv+"/messages", map[string]any{"client_message_id": key, "text": "Please summarize", "mention_expert_id": binding, "reply_to_message_id": quote})
}
func admitExpert(h *H, call string) {
	expertOK(h, admin, "POST", "/calls/"+call+"/admission", map[string]any{"admission_state": "accepted", "run_id": "run-" + call})
}
func expertResult(h *H, call, state string) map[string]any {
	value := map[string]any{"call_id": call, "execution_id": "run-" + call, "execution_state": state, "result": nil}
	if state == "completed" {
		value["result"] = map[string]any{"text": "One immutable answer", "truncated": false, "incomplete": false}
	}
	raw, _ := json.Marshal(value)
	sum := sha256.Sum256(raw)
	return map[string]any{"result_wire": string(raw), "result_digest": hex.EncodeToString(sum[:])}
}

func TestGroupExpertInvitationLimitsAndImmutableHistory(t *testing.T) {
	h, conv, _ := expertSetup(t)
	b := inviteExpert(h, conv, 0)
	first := b["binding_id"].(string)
	if inviteExpert(h, conv, 0)["binding_id"] != first {
		t.Fatal("duplicate invitation")
	}
	inviteExpert(h, conv, 1)
	inviteExpert(h, conv, 2)
	if code, _ := expertInternal(h, admin, "POST", "/groups/"+conv+"/bindings", map[string]any{"request_id": "fourth", "actor_id": admin, "revision": 1, "source_agent_id": "source-3", "source_row_id": 13}); code != 409 {
		t.Fatal(code)
	}
	if code, _ := expertInternal(h, alice, "POST", "/groups/"+conv+"/bindings", map[string]any{"request_id": "employee", "actor_id": alice, "revision": 1, "source_agent_id": "source-3", "source_row_id": 13}); code != 403 {
		t.Fatal(code)
	}
	remove := map[string]any{"request_id": "remove", "actor_id": admin, "generation": b["generation"]}
	expertOK(h, admin, "DELETE", "/groups/"+conv+"/bindings/"+first, remove)
	second := expertOK(h, admin, "POST", "/groups/"+conv+"/bindings", map[string]any{"request_id": "reinvite", "actor_id": admin, "revision": 1, "source_agent_id": "source-0", "source_row_id": 10})["binding"].(map[string]any)
	if second["binding_id"] == first || second["generation"] == b["generation"] {
		t.Fatal(second)
	}
	expertOK(h, admin, "DELETE", "/groups/"+conv+"/bindings/"+first, remove)
	info := expertOK(h, alice, "POST", "/groups/"+conv+"/inspect", map[string]any{"actor_id": alice})
	active := 0
	for _, raw := range info["items"].([]any) {
		if raw.(map[string]any)["state"] == "active" {
			active++
		}
	}
	if active != 3 {
		t.Fatal(info)
	}
}
func TestGroupExpertQueueDispatchAndIdempotentReply(t *testing.T) {
	h, conv, _ := expertSetup(t)
	binding := inviteExpert(h, conv, 0)["binding_id"].(string)
	calls := []string{}
	for i := 0; i < 5; i++ {
		msg := askExpert(h, conv, binding, fmt.Sprint(i), "")
		call := msg["expert_call"].(map[string]any)
		if i == 4 {
			if call["call_state"] != "rejected" {
				t.Fatal(call)
			}
		} else {
			calls = append(calls, call["call_id"].(string))
		}
	}
	admitExpert(h, calls[1])
	if expertOK(h, admin, "GET", "/calls/"+calls[1], nil)["call"].(map[string]any)["call_state"] != "queued" {
		t.Fatal("skipped unconfirmed head")
	}
	admitExpert(h, calls[0])
	input := expertOK(h, admin, "GET", "/calls/"+calls[0]+"/context", nil)
	if input["question"] != "Please summarize" || input["quote"] != "" || input["dispatched"] != true {
		t.Fatal(input)
	}
	payload := expertResult(h, calls[0], "completed")
	receipt := expertOK(h, admin, "POST", "/calls/"+calls[0]+"/result", payload)
	again := expertOK(h, admin, "POST", "/calls/"+calls[0]+"/result", payload)
	if receipt["message_id"] != again["message_id"] {
		t.Fatal("duplicate reply")
	}
	if expertOK(h, admin, "GET", "/calls/"+calls[1], nil)["call"].(map[string]any)["call_state"] != "dispatched" {
		t.Fatal("queue not advanced")
	}
	messages := h.ok(alice, "GET", "/conversations/"+conv+"/messages", nil)["items"].([]any)
	answers := 0
	for _, raw := range messages {
		m := raw.(map[string]any)
		if m["sender_type"] == "group_expert" {
			answers++
			if m["expert"].(map[string]any)["binding_id"] != binding || m["reply_to_message_id"] == nil {
				t.Fatal(m)
			}
		}
	}
	if answers != 1 {
		t.Fatal(answers)
	}
}

func TestGroupExpertForeignMentionMetadataStaysInItsGroup(t *testing.T) {
	h, first, _ := expertSetup(t)
	binding := inviteExpert(h, first, 0)
	second := project(h, "foreign-expert-binding", outsider)
	if code, _ := h.request(outsider, "GET", "/conversations/"+first+"/messages", nil); code < 400 {
		t.Fatalf("outsider can read the first group: HTTP %d", code)
	}
	expertOK(h, admin, "PUT", "/groups/"+second+"/enabled", map[string]any{"request_id": "enable-second", "actor_id": admin, "organization": true, "enabled": true})
	body := map[string]any{
		"client_message_id": "foreign-binding-question", "text": "Group message",
		"mention_expert_id": binding["binding_id"],
	}
	path := "/conversations/" + second + "/messages"
	assertRejected := func(row map[string]any) {
		t.Helper()
		call := row["expert_call"].(map[string]any)
		if call["call_state"] != "rejected" || call["reason_code"] != "group_expert_removed" || call["expert_name"] != "" {
			t.Fatalf("unexpected expert metadata: %v", call)
		}
	}
	message := h.ok(outsider, "POST", path, body)
	assertRejected(message)
	id := message["message_id"].(string)
	assertRejected(h.ok(outsider, "GET", path+"/"+id, nil))
	assertRejected(h.ok(outsider, "GET", path+"?limit=1", nil)["items"].([]any)[0].(map[string]any))

	// A receipt already persisted by the previous binary must also be safe
	// when the same send is replayed after an upgrade.
	legacy := message["expert_call"].(map[string]any)
	legacy["expert_name"] = binding["display_name"]
	raw, err := json.Marshal(message)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := h.D.Store.Pool.Exec(context.Background(), `UPDATE message_receipts SET result=$3 WHERE sender_id=$1 AND client_message_id=$2`, outsider, body["client_message_id"], raw); err != nil {
		t.Fatal(err)
	}
	replay := h.ok(outsider, "POST", path, body)
	if replay["replayed"] != true || replay["message_id"] != id {
		t.Fatal(replay)
	}
	assertRejected(replay)
}

func TestGroupExpertSyncRedactsInvisibleQuestionsWithoutLosingCursor(t *testing.T) {
	h, conv, _ := expertSetup(t)
	binding := inviteExpert(h, conv, 0)["binding_id"].(string)
	question := askExpert(h, conv, binding, "question-before-visibility-change", "")
	call := question["expert_call"].(map[string]any)["call_id"].(string)
	id := question["message_id"].(string)
	admitExpert(h, call)
	beforeClear := h.ok(alice, "POST", "/sync/snapshot", nil)["cursor"].(string)
	// Record a real state update while the question is still visible to Alice.
	payload := expertResult(h, call, "completed")
	expertOK(h, admin, "POST", "/calls/"+call+"/delivery", map[string]any{
		"run_id": "run-" + call, "result_digest": payload["result_digest"],
	})
	h.ok(alice, "POST", "/conversations/"+conv+"/clear", nil)
	h.ok(admin, "POST", "/groups/"+conv+"/members", map[string]any{"request_id": "join-after-question", "user_ids": []string{outsider}})
	newMember := h.ok(outsider, "POST", "/sync/snapshot", nil)["cursor"].(string)
	if code, _ := h.request(outsider, "GET", "/conversations/"+conv+"/messages/"+id, nil); code != 404 {
		t.Fatalf("pre-join message returned HTTP %d", code)
	}
	// A personal clear does not cancel the call or prevent new members from
	// seeing the newly posted reply.
	result := expertOK(h, admin, "POST", "/calls/"+call+"/result", payload)
	if result["call_state"] != "completed" {
		t.Fatal(result)
	}
	for _, user := range []string{alice, outsider} {
		var count int
		err := h.D.Store.Pool.QueryRow(context.Background(), `SELECT count(*) FROM user_sync_events WHERE user_id=$1 AND type='group.expert.call' AND object_id=$2`, user, id).Scan(&count)
		if want := map[string]int{alice: 3, outsider: 0}[user]; err != nil || count != want {
			t.Fatalf("unexpected durable expert events for %s: %d, %v", user, count, err)
		}
	}
	page := h.ok(alice, "GET", "/sync?limit=1&cursor="+url.QueryEscape(beforeClear), nil)
	redacted := page["events"].([]any)[0].(map[string]any)
	if redacted["type"] != "sync.redacted" || redacted["object_id"] != "" || redacted["version"] != "0" || len(redacted["payload"].(map[string]any)) != 0 || page["has_more"] != true {
		t.Fatalf("unexpected redacted event/page: %v", page)
	}
	next := h.ok(alice, "GET", "/sync?limit=1&cursor="+url.QueryEscape(page["cursor"].(string)), nil)
	if next["events"].([]any)[0].(map[string]any)["type"] != "conversation.cleared" {
		t.Fatalf("clear did not follow the redacted event: %v", next)
	}

	// Simulate a notification retained from an older server that fanned out
	// to current members without checking their history boundary.
	if err := h.D.Store.Tx(context.Background(), func(tx pgx.Tx) error {
		_, err := store.EmitSync(context.Background(), tx, outsider, "group.expert.call", id, 1, map[string]any{"conversation_id": conv, "message_id": id, "seq": question["seq"]})
		return err
	}); err != nil {
		t.Fatal(err)
	}
	page = h.ok(outsider, "GET", "/sync?cursor="+url.QueryEscape(newMember), nil)
	events := page["events"].([]any)
	if len(events) != 2 || events[0].(map[string]any)["object_id"] != result["message_id"] || events[1].(map[string]any)["type"] != "sync.redacted" || events[1].(map[string]any)["object_id"] != "" {
		t.Fatalf("new member should receive the reply and a redacted legacy event: %v", page)
	}
}
func TestGroupExpertQuoteVisibilityClearAndJoin(t *testing.T) {
	for _, change := range []string{"clear", "join", "recall"} {
		t.Run(change, func(t *testing.T) {
			h, conv, _ := expertSetup(t)
			binding := inviteExpert(h, conv, 0)["binding_id"].(string)
			quote := h.send(alice, conv, "quote", "Visible quote")["message_id"].(string)
			msg := askExpert(h, conv, binding, "question", quote)
			call := msg["expert_call"].(map[string]any)["call_id"].(string)
			admitExpert(h, call)
			if change == "clear" {
				h.ok(alice, "POST", "/conversations/"+conv+"/clear", nil)
			} else if change == "join" {
				h.ok(admin, "POST", "/groups/"+conv+"/members", map[string]any{"request_id": "join-new", "user_ids": []string{outsider}})
			} else {
				h.ok(alice, "POST", "/messages/"+quote+"/recall", nil)
			}
			receipt := expertOK(h, admin, "POST", "/calls/"+call+"/result", expertResult(h, call, "completed"))
			expected := "cancelled"
			if change == "clear" {
				expected = "completed"
			}
			if receipt["call_state"] != expected {
				t.Fatal(receipt)
			}
		})
	}
}
func TestGroupExpertCancellationAndAuthorizationCannotRevive(t *testing.T) {
	h, conv, _ := expertSetup(t)
	b := inviteExpert(h, conv, 0)["binding_id"].(string)
	m := askExpert(h, conv, b, "question", "")
	call := m["expert_call"].(map[string]any)["call_id"].(string)
	admitExpert(h, call)
	expertOK(h, admin, "PUT", "/authorizations/user:2", map[string]any{"revision": 2})
	state := expertOK(h, admin, "GET", "/calls/"+call, nil)["call"].(map[string]any)["call_state"]
	if state != "cancel_requested" {
		t.Fatal(state)
	}
	expertOK(h, admin, "PUT", "/authorizations/user:2", map[string]any{"revision": 1})
	receipt := expertOK(h, admin, "POST", "/calls/"+call+"/result", expertResult(h, call, "completed"))
	if receipt["call_state"] != "cancelled" || receipt["delivery_state"] != "unpublished" {
		t.Fatal(receipt)
	}
	if e := experts.New(h.D).Reconcile(context.Background(), nil); e != nil {
		t.Fatal(e)
	}
	// A post-commit retry preserves the stored conclusion even after revocation.
	expertOK(h, admin, "POST", "/calls/"+call+"/result", expertResult(h, call, "completed"))
}

func TestGroupExpertCancelWaitsForStopAndUnknownReleasesOnlyGroupSlot(t *testing.T) {
	h, conv, _ := expertSetup(t)
	b := inviteExpert(h, conv, 0)
	first := askExpert(h, conv, b["binding_id"].(string), "first", "")["expert_call"].(map[string]any)["call_id"].(string)
	second := askExpert(h, conv, b["binding_id"].(string), "second", "")["expert_call"].(map[string]any)["call_id"].(string)
	admitExpert(h, first)
	admitExpert(h, second)
	// Recalling just the first input preserves the next waiting call.
	meta := expertOK(h, admin, "GET", "/calls/"+first, nil)["call"].(map[string]any)
	h.ok(alice, "POST", "/messages/"+meta["message_id"].(string)+"/recall", nil)
	if err := experts.New(h.D).Reconcile(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
	var op, digest string
	if err := h.D.Store.Pool.QueryRow(context.Background(), `SELECT operation_id,request_digest FROM group_expert_outbox WHERE kind='cancel' AND target_id=$1`, first).Scan(&op, &digest); err != nil {
		t.Fatal(err)
	}
	expertOK(h, admin, "POST", "/pending/"+op+"/ack", map[string]any{"request_digest": digest, "receipt": map[string]any{"saved": true, "stopped": false, "execution_state": "cancel_requested"}})
	if expertOK(h, admin, "GET", "/calls/"+second, nil)["call"].(map[string]any)["call_state"] != "queued" {
		t.Fatal("cancel request released group occupancy prematurely")
	}
	expertOK(h, admin, "POST", "/pending/"+op+"/ack", map[string]any{"request_digest": digest, "receipt": map[string]any{"saved": true, "stopped": false, "execution_state": "unknown"}})
	if expertOK(h, admin, "GET", "/calls/"+second, nil)["call"].(map[string]any)["call_state"] != "dispatched" {
		t.Fatal("unknown did not advance group queue")
	}
	result := expertOK(h, admin, "POST", "/calls/"+first+"/result", expertResult(h, first, "completed"))
	if result["call_state"] != "unknown" || result["delivery_state"] != "unpublished" {
		t.Fatal(result)
	}
}

func TestGroupExpertSourceScopeHighwaterAndServiceBinding(t *testing.T) {
	h, conv, sources := expertSetup(t)
	expertOK(h, admin, "PUT", "/authorizations/scope:"+conv, map[string]any{"revision": 5})
	expertOK(h, admin, "PUT", "/groups/"+conv+"/scope", map[string]any{"revision": 1, "sources": sources})
	if code, _ := expertInternal(h, admin, "POST", "/groups/"+conv+"/bindings", map[string]any{"actor_id": admin, "request_id": "stale-invite", "revision": 1, "source_agent_id": "source-0", "source_row_id": 10}); code != 409 {
		t.Fatal(code)
	}
	path := "/internal/group-experts/v1/runtime"
	raw := []byte(`{"protocol":2}`)
	sum := sha256.Sum256(raw)
	now := time.Now().Unix()
	token := auth.Sign(auth.Claims{Issuer: "octop", Audience: "octop-im", Purpose: "group-experts-v1", UserID: admin, SessionID: "test", Issued: now, Expires: now + 15, Method: "POST", Path: path, BodyDigest: hex.EncodeToString(sum[:])}, h.D.Cfg.IdentityKey)
	for _, tc := range []struct{ token, body string }{{token, `{"protocol":0}`}, {h.token(admin, "request", 1), string(raw)}} {
		r, _ := http.NewRequest("POST", h.Server.URL+path, bytes.NewBufferString(tc.body))
		r.Header.Set("Authorization", "Bearer "+tc.token)
		response, err := http.DefaultClient.Do(r)
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != 401 {
			t.Fatal("unbound or user credential accepted", response.StatusCode)
		}
	}
}

func TestGroupExpertBindingRevocationOutOfOrderNeverRevivesOldGeneration(t *testing.T) {
	h, conv, sources := expertSetup(t)
	old := inviteExpert(h, conv, 0)
	inviteExpert(h, conv, 1)
	call := askExpert(h, conv, old["binding_id"].(string), "before-revoke", "")["expert_call"].(map[string]any)["call_id"].(string)
	// Regrant projection overtakes both removals; each source still has its own cutoff.
	expertOK(h, admin, "PUT", "/groups/"+conv+"/scope", map[string]any{"operation_id": "scope-three", "revision": 3, "sources": sources})
	if code, response := expertInternal(h, admin, "POST", "/groups/"+conv+"/bindings", map[string]any{"request_id": "stale-invitation", "actor_id": admin, "revision": 1, "source_agent_id": "source-0", "source_row_id": 10}); code != 409 || response["error"].(map[string]any)["code"] != "group_expert_scope_changed" {
		t.Fatal(code, response)
	}
	notice := func(source string, row, revision int) map[string]any {
		return map[string]any{"operation_id": fmt.Sprintf("revoke-%s-%d", source, revision), "revision": revision, "revocations": []map[string]any{{"source_agent_id": source, "source_row_id": row, "before_revision": revision}}}
	}
	expertOK(h, admin, "PUT", "/authorizations/bindings:"+conv, notice("source-1", 11, 3))
	expertOK(h, admin, "PUT", "/authorizations/bindings:"+conv, notice("source-0", 10, 2))
	fresh := expertOK(h, admin, "POST", "/groups/"+conv+"/bindings", map[string]any{"request_id": "fresh-after-regrant", "actor_id": admin, "revision": 3, "source_agent_id": "source-0", "source_row_id": 10})["binding"].(map[string]any)
	if fresh["binding_id"] == old["binding_id"] || fresh["generation"] != float64(2) {
		t.Fatal(fresh)
	}
	expertOK(h, admin, "PUT", "/authorizations/bindings:"+conv, notice("source-0", 10, 2))
	expertOK(h, admin, "PUT", "/groups/"+conv+"/scope", map[string]any{"operation_id": "late-scope-two", "revision": 2, "sources": []any{}})
	info := expertOK(h, alice, "POST", "/groups/"+conv+"/inspect", map[string]any{"actor_id": alice})
	for _, raw := range info["items"].([]any) {
		b := raw.(map[string]any)
		if b["binding_id"] == fresh["binding_id"] && b["state"] != "active" {
			t.Fatal(b)
		}
		if b["binding_id"] == old["binding_id"] && b["state"] != "invalidated" {
			t.Fatal(b)
		}
	}
	if expertOK(h, admin, "GET", "/calls/"+call, nil)["call"].(map[string]any)["call_state"] != "cancelled" {
		t.Fatal("revoked call revived")
	}
	next := askExpert(h, conv, fresh["binding_id"].(string), "new-generation", "")["expert_call"].(map[string]any)["call_id"].(string)
	admitExpert(h, next)
	input := expertOK(h, admin, "GET", "/calls/"+next+"/context", nil)
	if input["identity"].(map[string]any)["binding_scope_revision"] != float64(3) {
		t.Fatal(input)
	}
}

func TestGroupExpertAwaitingDeliveryPinsResultWithoutPublishingText(t *testing.T) {
	h, conv, _ := expertSetup(t)
	binding := inviteExpert(h, conv, 0)["binding_id"].(string)
	call := askExpert(h, conv, binding, "pending-result", "")["expert_call"].(map[string]any)["call_id"].(string)
	admitExpert(h, call)
	result := expertResult(h, call, "completed")
	notice := map[string]any{"run_id": "run-" + call, "result_digest": result["result_digest"]}
	for i := 0; i < 2; i++ {
		if expertOK(h, admin, "POST", "/calls/"+call+"/delivery", notice)["call_state"] != "awaiting_delivery" {
			t.Fatal("missing delivery state")
		}
	}
	metadata := expertOK(h, admin, "GET", "/calls/"+call, nil)
	if metadata["call"].(map[string]any)["receipt"] != nil {
		t.Fatal("announcing generation must not publish a reply")
	}
	bad := map[string]any{"run_id": "run-" + call, "result_digest": fmt.Sprintf("%064d", 0)}
	if code, _ := expertInternal(h, admin, "POST", "/calls/"+call+"/delivery", bad); code != 409 {
		t.Fatal(code)
	}
	receipt := expertOK(h, admin, "POST", "/calls/"+call+"/result", result)
	if receipt["call_state"] != "completed" || receipt["message_id"] == nil {
		t.Fatal(receipt)
	}
	if expertOK(h, admin, "POST", "/calls/"+call+"/delivery", notice)["call_state"] != "completed" {
		t.Fatal("late delivery notice revived call")
	}
}
