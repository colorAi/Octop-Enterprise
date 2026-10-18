package itest

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/coder/websocket"
	"image"
	"image/png"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"octop/communication/internal/auth"
	"octop/communication/internal/config"
	"octop/communication/internal/deps"
	"octop/communication/internal/httpx"
	"octop/communication/internal/serverapp"
	"octop/communication/internal/testdb"
	"strings"
	"sync"
	"testing"
	"time"
)

const admin = "octop:installation:1"
const alice = "octop:installation:2"
const bob = "octop:installation:3"
const outsider = "octop:installation:4"

type H struct {
	t           *testing.T
	D           *deps.Deps
	App         *serverapp.App
	Server      *httptest.Server
	Versions    map[string]int64
	Dept, Other string
}

func newH(t *testing.T) *H {
	st := testdb.Open(t, testdb.New(t))
	cfg := config.Config{IdentityKey: strings.Repeat("k", 32), MediaRoot: t.TempDir(), SyncRetention: 24 * time.Hour, SnapshotTTL: time.Hour, RecallWindow: time.Minute, JobPollInterval: 20 * time.Millisecond,
		Limits: config.Limits{MaxTextChars: 8000, MaxImageCaption: 2000, MaxImagesPerMessage: 9, GroupMaxMembers: 500, ListDefault: 30, ListMax: 100, SyncPageMax: 200}, Resource: config.Resource{MediaMaxImageBytes: 10 << 20, MediaMaxPixels: 20000000, WSSMaxConns: 100, WSSMaxPerUser: 4}, Workers: config.Workers{JobTotal: 1, OutboxTotal: 1}}
	d := &deps.Deps{Cfg: cfg, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Store: st, Cursor: httpx.NewCursor("stable-test-key"), InstanceID: "itest"}
	a := serverapp.New(d)
	srv := httptest.NewServer(a.Handler(st))
	t.Cleanup(srv.Close)
	h := &H{t, d, a, srv, map[string]int64{}, "", ""}
	for _, u := range []string{admin, alice, bob, outsider} {
		h.identity(u, "active", 1)
	}
	h.Dept = h.ok(admin, "POST", "/organization/departments", map[string]any{"name": "Engineering"})["department_id"].(string)
	h.Other = h.ok(admin, "POST", "/organization/departments", map[string]any{"name": "Finance"})["department_id"].(string)
	for _, u := range []string{admin, alice, bob} {
		h.assign(u, h.Dept)
	}
	h.assign(outsider, h.Other)
	return h
}
func (h *H) token(u, purpose string, version int64) string {
	perms := []string{"messaging", "contacts"}
	if u == admin {
		perms = append(perms, "departments", "groups_create")
	}
	now := time.Now().Unix()
	return auth.Sign(auth.Claims{Issuer: "octop", Audience: "octop-im", Purpose: purpose, UserID: u, SessionID: "test-session", Issued: now, Expires: now + 30, Version: version, Permissions: perms}, h.D.Cfg.IdentityKey)
}
func (h *H) request(u, method, path string, body any) (int, map[string]any) {
	b, _ := json.Marshal(body)
	req, _ := http.NewRequest(method, h.Server.URL+"/api/app/v1"+path, bytes.NewReader(b))
	req.Header.Set("Authorization", "Bearer "+h.token(u, "request", h.Versions[u]))
	req.Header.Set("Content-Type", "application/json")
	res, e := http.DefaultClient.Do(req)
	if e != nil {
		h.t.Fatal(e)
	}
	defer res.Body.Close()
	var out map[string]any
	if e = json.NewDecoder(res.Body).Decode(&out); e != nil {
		h.t.Fatalf("decode %s: %v", path, e)
	}
	return res.StatusCode, out
}
func (h *H) ok(u, m, p string, b any) map[string]any {
	h.t.Helper()
	code, v := h.request(u, m, p, b)
	if code < 200 || code >= 300 {
		h.t.Fatalf("%s %s: %d %v", m, p, code, v)
	}
	return v["data"].(map[string]any)
}
func (h *H) identity(u, status string, version int64) {
	h.t.Helper()
	perms := []string{"messaging", "contacts"}
	if u == admin {
		perms = append(perms, "departments", "groups_create")
	}
	v := map[string]any{"user_id": u, "version": version, "nickname": u, "avatar_icon": "person", "status": status, "permissions": perms}
	b, _ := json.Marshal(v)
	req, _ := http.NewRequest("PUT", h.Server.URL+"/internal/identities/"+u, bytes.NewReader(b))
	req.Header.Set("Authorization", "Bearer "+h.token(u, "identity", version))
	res, e := http.DefaultClient.Do(req)
	if e != nil {
		h.t.Fatal(e)
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		v, _ := io.ReadAll(res.Body)
		h.t.Fatalf("identity: %d %s", res.StatusCode, v)
	}
	h.Versions[u] = version
}
func (h *H) assign(u, dept string) {
	h.ok(admin, "PUT", "/organization/users/"+u+"/department", map[string]string{"department_id": dept})
}
func (h *H) direct(a, b string) string {
	return h.ok(a, "POST", "/conversations/direct", map[string]string{"peer_user_id": b})["conversation_id"].(string)
}
func (h *H) send(u, conv, id, text string) map[string]any {
	return h.ok(u, "POST", "/conversations/"+conv+"/messages", map[string]string{"client_message_id": id, "text": text})
}
func TestCompanyDirectIdempotencyAndMove(t *testing.T) {
	h := newH(t)
	conv := h.direct(alice, bob)
	first := h.send(alice, conv, "m1", "hello")
	again := h.send(alice, conv, "m1", "hello")
	if first["message_id"] != again["message_id"] || first["seq"] != again["seq"] {
		t.Fatal("idempotency failed")
	}
	cross := h.direct(alice, outsider)
	h.send(outsider, cross, "cross-department", "company colleague")
	if code, _ := h.request(outsider, "GET", "/conversations/"+conv+"/messages", nil); code != 403 && code != 404 {
		t.Fatal("non-member history", code)
	}
	h.assign(bob, h.Other)
	h.send(alice, conv, "m2", "continues after department transfer")
	h.ok(bob, "GET", "/conversations/"+conv+"/messages", nil)
	snap := h.ok(alice, "POST", "/sync/snapshot", nil)
	if _, ok := snap["until_seq"].(string); !ok {
		t.Fatal("snapshot sequence must be decimal string")
	}
	h.ok(alice, "GET", "/sync/snapshots/"+snap["snapshot_id"].(string), nil)
	var invalid int
	if e := h.D.Store.Pool.QueryRow(context.Background(), `SELECT count(*) FROM delivery_outbox WHERE kind<>'sync_wakeup'`).Scan(&invalid); e != nil || invalid != 0 {
		t.Fatal("invalid native push work", e, invalid)
	}
}
func TestGroupEpochsOwnerAndOrganizationAuthority(t *testing.T) {
	h := newH(t)
	conv := h.ok(admin, "POST", "/organization/departments/"+h.Dept+"/group", nil)["conversation_id"].(string)
	repeat := h.ok(admin, "POST", "/organization/departments/"+h.Dept+"/group", nil)
	if repeat["conversation_id"] != conv {
		t.Fatal("duplicate department group")
	}
	first := h.send(alice, conv, "g1", "old-visible")
	h.assign(bob, h.Other)
	gap := h.send(alice, conv, "g2", "gap-hidden")
	h.assign(bob, h.Dept)
	h.send(alice, conv, "g3", "new-visible")
	history := h.ok(bob, "GET", "/conversations/"+conv+"/messages", nil)
	encoded, _ := json.Marshal(history)
	if !bytes.Contains(encoded, []byte(first["message_id"].(string))) || bytes.Contains(encoded, []byte(gap["message_id"].(string))) {
		t.Fatal("membership epoch leak", string(encoded))
	}
	h.assign(admin, h.Other)
	var owner string
	if e := h.D.Store.Pool.QueryRow(context.Background(), `SELECT owner_id FROM group_settings WHERE conversation_id=$1`, conv).Scan(&owner); e != nil || owner == admin {
		t.Fatal("owner not reassigned", e, owner)
	}
	if code, _ := h.request(admin, "GET", "/groups/"+conv+"/members", nil); code != 403 {
		t.Fatal("organization admin read bypass", code)
	}
	h.ok(admin, "PATCH", "/organization/groups/"+conv, map[string]string{"announcement": "notice"})
	h.ok(admin, "PUT", "/organization/groups/"+conv+"/owner", map[string]string{"user_id": alice})
	h.ok(admin, "PATCH", "/organization/groups/"+conv+"/members/"+bob, map[string]string{"role": "admin"})
	h.ok(admin, "PATCH", "/organization/groups/"+conv+"/members/"+bob, map[string]string{"role": "member", "mute_until": time.Now().Add(time.Minute).UTC().Format(time.RFC3339)})
	if code, _ := h.request(bob, "POST", "/conversations/"+conv+"/messages", map[string]string{"client_message_id": "muted", "text": "blocked"}); code != 403 {
		t.Fatal("muted member sent", code)
	}
	h.ok(admin, "PATCH", "/organization/groups/"+conv+"/members/"+bob, map[string]any{"mute_until": nil})
	h.send(bob, conv, "unmuted", "available")

	if code, _ := h.request(alice, "POST", "/groups/"+conv+"/leave", map[string]string{"request_id": "cannot-leave"}); code != 409 {
		t.Fatal("compulsory membership exposed", code)
	}
}
func TestIdentityTombstoneAndCredentialScopes(t *testing.T) {
	h := newH(t)
	h.identity(bob, "disabled", 2)
	if code, _ := h.request(bob, "GET", "/conversations", nil); code != 403 {
		t.Fatal("disabled access", code)
	}
	h.identity(bob, "active", 1)
	if code, _ := h.request(bob, "GET", "/conversations", nil); code == 200 {
		t.Fatal("old revision resurrected account")
	}
	h.identity(bob, "deleted", 3)
	req, _ := http.NewRequest("GET", h.Server.URL+"/api/app/v1/conversations", nil)
	req.Header.Set("Authorization", "Bearer "+h.token(alice, "identity", 1))
	res, e := http.DefaultClient.Do(req)
	if e != nil {
		t.Fatal(e)
	}
	res.Body.Close()
	if res.StatusCode != 401 {
		t.Fatal("wrong credential purpose accepted")
	}
	code, data := h.request(alice, "GET", "/contacts", nil)
	if code < 200 || code >= 300 {
		t.Fatal(data)
	}
	b, _ := json.Marshal(data)
	if !bytes.Contains(b, []byte(outsider)) || bytes.Contains(b, []byte(bob)) {
		t.Fatal("company directory must include other departments and exclude deleted users")
	}
}
func TestConcurrentMoveClosesOldGroupSending(t *testing.T) {
	h := newH(t)
	conv := h.ok(admin, "POST", "/organization/departments/"+h.Dept+"/group", nil)["conversation_id"].(string)
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			h.request(bob, "POST", "/conversations/"+conv+"/messages", map[string]string{"client_message_id": fmt.Sprint("race", i), "text": "racing"})
		}(i)
	}
	h.assign(bob, h.Other)
	wg.Wait()
	if code, _ := h.request(bob, "POST", "/conversations/"+conv+"/messages", map[string]string{"client_message_id": "after-move", "text": "denied"}); code != 403 {
		t.Fatal("send after move", code)
	}
}
func TestRealtimeDurableWakeupAndDisable(t *testing.T) {
	h := newH(t)
	conv := h.direct(alice, bob)
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()
	ws, _, e := websocket.Dial(ctx, strings.Replace(h.Server.URL, "http://", "ws://", 1)+"/api/app/v1/realtime", &websocket.DialOptions{HTTPHeader: http.Header{"Authorization": []string{"Bearer " + h.token(bob, "request", 1)}}})
	if e != nil {
		t.Fatal(e)
	}
	defer ws.CloseNow()
	h.D.Realtime = nil
	h.send(alice, conv, "notify", "hello")
	h.D.Realtime = h.App.Hub
	if e := h.App.Start(context.Background()); e != nil {
		t.Fatal(e)
	}
	t.Cleanup(h.App.Stop)
	_, data, e := ws.Read(ctx)
	for e == nil && bytes.Contains(data, []byte("presence.changed")) {
		_, data, e = ws.Read(ctx)
	}
	if e != nil || !bytes.Contains(data, []byte("sync.available")) {
		t.Fatal("wakeup missing", e, string(data))
	}
	h.identity(bob, "disabled", 2)
	_, _, e = ws.Read(ctx)
	if e == nil {
		t.Fatal("disabled socket stayed open")
	}
}
func TestPrivateImageVisibilityRecallAndClear(t *testing.T) {
	h := newH(t)
	conv := h.direct(alice, bob)
	if e := h.App.Start(context.Background()); e != nil {
		t.Fatal(e)
	}
	t.Cleanup(h.App.Stop)
	var content bytes.Buffer
	png.Encode(&content, image.NewRGBA(image.Rect(0, 0, 8, 8)))
	hash := sha256.Sum256(content.Bytes())
	init := h.ok(alice, "POST", "/media/uploads", map[string]any{"purpose": "chat_image", "mime": "image/png", "size_bytes": content.Len(), "sha256": hex.EncodeToString(hash[:])})
	up, asset := init["upload_id"].(string), init["asset_id"].(string)
	req, _ := http.NewRequest("PUT", h.Server.URL+"/api/app/v1/media/uploads/"+up+"/content", bytes.NewReader(content.Bytes()))
	req.Header.Set("Authorization", "Bearer "+h.token(alice, "request", 1))
	res, e := http.DefaultClient.Do(req)
	if e != nil {
		t.Fatal(e)
	}
	res.Body.Close()
	if res.StatusCode != 200 {
		t.Fatal("upload", res.StatusCode)
	}
	h.ok(alice, "POST", "/media/uploads/"+up+"/complete", nil)
	deadline := time.Now().Add(3 * time.Second)
	for {
		var state string
		h.D.Store.Pool.QueryRow(context.Background(), `SELECT state FROM media_assets WHERE id=$1`, asset).Scan(&state)
		if state == "ready" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("processing failed", state)
		}
		time.Sleep(20 * time.Millisecond)
	}
	sent := h.ok(alice, "POST", "/conversations/"+conv+"/messages", map[string]any{"client_message_id": "image1", "asset_ids": []string{asset}})
	check := func(u string, expected int) {
		req, _ := http.NewRequest("GET", h.Server.URL+"/api/app/v1/media/"+asset, nil)
		req.Header.Set("Authorization", "Bearer "+h.token(u, "request", h.Versions[u]))
		res, e := http.DefaultClient.Do(req)
		if e != nil {
			t.Fatal(e)
		}
		res.Body.Close()
		if res.StatusCode != expected && !(expected == 403 && res.StatusCode == 404) {
			t.Fatal("media access", u, res.StatusCode, expected)
		}
	}
	check(bob, 200)
	check(outsider, 403)
	h.ok(bob, "POST", "/conversations/"+conv+"/clear", nil)
	check(bob, 403)
	h.ok(alice, "POST", "/messages/"+sent["message_id"].(string)+"/recall", nil)
	check(alice, 403)
	check(bob, 403)
}

func TestDepartmentTreeCapacityAndRollback(t *testing.T) {
	h := newH(t)
	child := h.ok(admin, "POST", "/organization/departments", map[string]any{"name": "Child", "parent_id": h.Dept})["department_id"].(string)
	if code, _ := h.request(admin, "PATCH", "/organization/departments/"+h.Dept, map[string]string{"parent_id": child}); code != 400 {
		t.Fatal("department cycle", code)
	}
	h.D.Cfg.Limits.GroupMaxMembers = 2
	if code, _ := h.request(admin, "POST", "/organization/departments/"+h.Dept+"/group", nil); code != 409 {
		t.Fatal("capacity not enforced", code)
	}
	h.D.Cfg.Limits.GroupMaxMembers = 3
	conv := h.ok(admin, "POST", "/organization/departments/"+h.Dept+"/group", nil)["conversation_id"].(string)
	if code, _ := h.request(admin, "PUT", "/organization/users/"+outsider+"/department", map[string]string{"department_id": h.Dept}); code != 409 {
		t.Fatal("group overflow assignment", code)
	}
	var dept string
	h.D.Store.Pool.QueryRow(context.Background(), `SELECT department_id FROM department_members WHERE user_id=$1`, outsider).Scan(&dept)
	if dept != h.Other {
		t.Fatal("failed assignment was not rolled back")
	}
	h.ok(admin, "PATCH", "/organization/departments/"+h.Dept, map[string]bool{"enabled": false})
	if code, _ := h.request(alice, "POST", "/conversations/"+conv+"/messages", map[string]string{"client_message_id": "disabled-dept", "text": "blocked"}); code != 403 {
		t.Fatal("disabled department group", code)
	}
	h.ok(admin, "PATCH", "/organization/departments/"+h.Dept, map[string]bool{"enabled": true})
	h.send(alice, conv, "reenabled", "available")
}
func TestForwardStateEventsAndBoundListCursors(t *testing.T) {
	h := newH(t)
	old := h.direct(alice, bob)
	other := h.direct(alice, admin)
	msg := h.send(alice, old, "source", "visible")
	h.identity(bob, "disabled", 2)
	value := h.ok(alice, "POST", "/messages/"+msg["message_id"].(string)+"/forward", map[string]any{"client_message_id": "forward", "to_conversation_ids": []string{old}})
	if len(value["failed"].([]any)) != 1 {
		t.Fatal("forward bypassed disabled-account policy", value)
	}
	h.ok(alice, "PATCH", "/conversations/"+other+"/state", map[string]bool{"pinned": true})
	list := h.ok(alice, "GET", "/conversations?limit=1", nil)
	cursor := list["next_cursor"].(string)
	if list["items"].([]any)[0].(map[string]any)["conversation_id"] != other {
		t.Fatal("pinned ordering")
	}
	next := h.ok(alice, "GET", "/conversations?limit=1&cursor="+cursor, nil)
	if next["items"].([]any)[0].(map[string]any)["conversation_id"] != old {
		t.Fatal("keyset skipped row")
	}
	if code, _ := h.request(outsider, "GET", "/conversations?limit=1&cursor="+cursor, nil); code != 400 {
		t.Fatal("cross-user cursor accepted", code)
	}
	h.ok(alice, "POST", "/conversations/"+other+"/read", map[string]string{"last_read_seq": "0"})
	var events int
	h.D.Store.Pool.QueryRow(context.Background(), `SELECT count(*) FROM user_sync_events WHERE user_id=$1 AND type='conversation.state.changed'`, alice).Scan(&events)
	if events != 2 {
		t.Fatal("state events missing", events)
	}
}

func TestOrganizationMetadataDoesNotGrantMessageAccess(t *testing.T) {
	h := newH(t)
	h.assign(admin, h.Other)
	group := h.ok(admin, "POST", "/organization/departments/"+h.Dept+"/group", nil)["conversation_id"].(string)
	roster := h.ok(admin, "GET", "/organization/users", nil)["items"].([]any)
	if len(roster) != 4 {
		t.Fatalf("roster: %v", roster)
	}
	for _, raw := range roster {
		item := raw.(map[string]any)
		for _, secret := range []string{"password_hash", "permissions", "identity_version", "session_id"} {
			if _, ok := item[secret]; ok {
				t.Fatalf("unexpected field %s", secret)
			}
		}
	}
	metadata := h.ok(admin, "GET", "/organization/groups/"+group, nil)
	if len(metadata["members"].([]any)) != 2 {
		t.Fatalf("members: %v", metadata)
	}
	for _, path := range []string{"/organization/users", "/organization/groups/" + group} {
		code, _ := h.request(alice, "GET", path, nil)
		if code != 403 {
			t.Fatalf("ordinary user %s returned %d", path, code)
		}
	}
	for _, path := range []string{"/groups/" + group, "/groups/" + group + "/members", "/conversations/" + group + "/messages"} {
		code, _ := h.request(admin, "GET", path, nil)
		if code != 403 && code != 404 {
			t.Fatalf("nonmember administrator %s returned %d", path, code)
		}
	}
	h.identity(bob, "disabled", 2)
	roster = h.ok(admin, "GET", "/organization/users", nil)["items"].([]any)
	for _, raw := range roster {
		item := raw.(map[string]any)
		if item["user_id"] == bob && (item["status"] != "disabled" || item["can_message"] != false) {
			t.Fatalf("disabled mirror: %v", item)
		}
	}
}

func TestCompanyDirectoryEmptyHierarchyCountsAndName(t *testing.T) {
	h := newH(t)
	child := h.ok(admin, "POST", "/organization/departments", map[string]any{"name": "Empty child", "parent_id": h.Dept})["department_id"].(string)
	departments := h.ok(alice, "GET", "/departments", nil)["items"].([]any)
	foundEmpty := false
	for _, item := range departments {
		dept := item.(map[string]any)
		if dept["department_id"] == child {
			foundEmpty = dept["parent_id"] == h.Dept && dept["member_count"] == float64(0)
		}
		if dept["department_id"] == h.Other && dept["member_count"] != float64(1) {
			t.Fatal("department count", dept)
		}
	}
	if len(departments) != 3 || !foundEmpty {
		t.Fatal("empty company departments must be visible", departments)
	}
	contacts := h.ok(alice, "GET", "/contacts", nil)["items"].([]any)
	if len(contacts) != 4 {
		t.Fatal("company-wide roster", contacts)
	}
	snapshot := h.ok(alice, "POST", "/sync/snapshot", nil)
	chunk := h.ok(alice, "GET", "/sync/snapshots/"+snapshot["snapshot_id"].(string), nil)
	encoded, _ := json.Marshal(chunk)
	if !bytes.Contains(encoded, []byte(outsider)) {
		t.Fatal("company directory snapshot omits another department")
	}
	if h.ok(alice, "GET", "/directory-settings", nil)["name"] != "" {
		t.Fatal("default directory name")
	}
	if code, _ := h.request(alice, "PUT", "/organization/directory-settings", map[string]string{"name": "unauthorized"}); code != 403 {
		t.Fatal("ordinary account renamed directory", code)
	}
	h.ok(admin, "PUT", "/organization/directory-settings", map[string]string{"name": "  Example Company  "})
	if h.ok(alice, "GET", "/directory-settings", nil)["name"] != "Example Company" {
		t.Fatal("company name not shared")
	}
	if code, _ := h.request(admin, "PUT", "/organization/directory-settings", map[string]string{"name": strings.Repeat("a", 81)}); code != 400 {
		t.Fatal("invalid company name", code)
	}
	h.ok(admin, "PUT", "/organization/directory-settings", map[string]string{"name": ""})
	if h.ok(alice, "GET", "/directory-settings", nil)["name"] != "" {
		t.Fatal("clear name did not restore default")
	}
	h.ok(admin, "PATCH", "/organization/departments/"+child, map[string]bool{"enabled": false})
	if len(h.ok(alice, "GET", "/departments", nil)["items"].([]any)) != 2 {
		t.Fatal("disabled department listed")
	}
}
func TestCompanyMessagingUnassignedPermissionAndHistoryBoundary(t *testing.T) {
	h := newH(t)
	h.ok(admin, "PUT", "/organization/users/"+outsider+"/department", map[string]any{"department_id": nil})
	conv := h.direct(alice, outsider)
	h.send(alice, conv, "unassigned", "colleague without a department")
	if code, _ := h.request(bob, "GET", "/conversations/"+conv+"/messages", nil); code != 403 && code != 404 {
		t.Fatal("company policy exposed third-party history", code)
	}
	if _, err := h.D.Store.Pool.Exec(context.Background(), `UPDATE users SET permissions='["contacts"]' WHERE id=$1`, outsider); err != nil {
		t.Fatal(err)
	}
	if code, _ := h.request(alice, "POST", "/conversations/"+conv+"/messages", map[string]string{"client_message_id": "removed-permission", "text": "blocked"}); code != 403 {
		t.Fatal("messaging permission removal ignored", code)
	}
}
