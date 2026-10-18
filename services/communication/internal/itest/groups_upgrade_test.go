package itest

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"sync"
	"testing"
	"time"
)

func project(h *H, key string, members ...string) string {
	return h.ok(admin, "POST", "/groups", map[string]any{"request_id": key, "name": "Launch", "user_ids": members})["conversation_id"].(string)
}
func op(key string) map[string]any { return map[string]any{"request_id": key} }

func TestGroupMemberManagementMetadataVisibility(t *testing.T) {
	h := newH(t)
	conv := project(h, "metadata", alice, bob)
	read := func(user string, privileged bool) {
		t.Helper()
		items := h.ok(user, "GET", "/groups/"+conv+"/members", nil)["items"].([]any)
		if len(items) != 3 {
			t.Fatal("roster unavailable", items)
		}
		for _, raw := range items {
			item := raw.(map[string]any)
			if item["nickname"] == "" || item["user_id"] == nil {
				t.Fatal("name hidden", item)
			}
			for _, key := range []string{"role", "membership_source", "granted_by", "mute_until", "joined_at"} {
				if _, present := item[key]; present != privileged {
					t.Fatalf("metadata %s visible=%v for %s", key, present, user)
				}
			}
		}
	}
	read(bob, false)
	read(admin, true)
	h.ok(admin, "PATCH", "/groups/"+conv+"/members/"+alice, map[string]any{"request_id": "promote", "role": "admin"})
	read(alice, true)
	h.ok(admin, "PUT", "/groups/"+conv+"/owner", map[string]any{"request_id": "transfer", "user_id": alice})
	read(alice, true)
	read(admin, true) // Company management retains metadata after transferring ownership.
	read(bob, false)
	if code, _ := h.request(outsider, "GET", "/groups/"+conv+"/members", nil); code != 403 {
		t.Fatal("nonmember roster", code)
	}
}

func groupCount(t *testing.T, h *H, conv string) int {
	t.Helper()
	var count int
	if e := h.D.Store.Pool.QueryRow(context.Background(), `SELECT count(*) FROM member_epochs WHERE conversation_id=$1 AND left_at IS NULL`, conv).Scan(&count); e != nil {
		t.Fatal(e)
	}
	return count
}
func TestCompanyGroupConcurrentSingletonAndAutomaticMembership(t *testing.T) {
	h := newH(t)
	dept := h.ok(admin, "POST", "/organization/departments/"+h.Dept+"/group", nil)["conversation_id"].(string)
	old := h.send(alice, dept, "old", "department private history")["message_id"].(string)
	var wg sync.WaitGroup
	ids := make(chan string, 8)
	failures := make(chan int, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			code, res := h.request(admin, "POST", "/organization/company-group", op(fmt.Sprint(i)))
			if code != 200 {
				failures <- code
				return
			}
			ids <- res["data"].(map[string]any)["conversation_id"].(string)
		}(i)
	}
	wg.Wait()
	close(ids)
	close(failures)
	for code := range failures {
		t.Fatal("company creation", code)
	}
	var conv string
	for id := range ids {
		if conv != "" && conv != id {
			t.Fatal("multiple company groups")
		}
		conv = id
	}
	if conv == dept || groupCount(t, h, conv) != 4 {
		t.Fatal("company membership", conv)
	}
	history, _ := json.Marshal(h.ok(outsider, "GET", "/conversations/"+conv+"/messages", nil))
	if bytes.Contains(history, []byte(old)) {
		t.Fatal("department history copied")
	}
	h.identity(bob, "disabled", 2)
	if groupCount(t, h, conv) != 3 {
		t.Fatal("disabled member remained")
	}
	hidden := h.send(alice, conv, "gap", "disabled gap")["message_id"].(string)
	h.identity(bob, "active", 3)
	if groupCount(t, h, conv) != 4 {
		t.Fatal("reactivated member absent")
	}
	history, _ = json.Marshal(h.ok(bob, "GET", "/conversations/"+conv+"/messages", nil))
	if bytes.Contains(history, []byte(hidden)) {
		t.Fatal("disabled interval visible")
	}
	h.ok(bob, "POST", "/conversations/"+conv+"/hide", nil)
	list := h.ok(bob, "GET", "/groups?kind=company", nil)["items"].([]any)
	if len(list) != 1 || list[0].(map[string]any)["conversation_id"] != conv {
		t.Fatal("hidden group undiscoverable", list)
	}
	if code, _ := h.request(bob, "POST", "/groups/"+conv+"/leave", op("leave")); code != 409 {
		t.Fatal("automatic leave", code)
	}
}
func TestProjectGroupPermissionsAndCreationReceipts(t *testing.T) {
	h := newH(t)
	body := map[string]any{"request_id": "create", "name": "Project", "user_ids": []string{bob, bob}}
	if code, _ := h.request(alice, "POST", "/groups", body); code != 403 {
		t.Fatal("ungranted creator", code)
	}
	if _, e := h.D.Store.Pool.Exec(context.Background(), `UPDATE users SET permissions=permissions || '["groups_create"]'::jsonb WHERE id=$1`, alice); e != nil {
		t.Fatal(e)
	}
	first := h.ok(alice, "POST", "/groups", body)["conversation_id"].(string)
	if again := h.ok(alice, "POST", "/groups", body)["conversation_id"]; again != first {
		t.Fatal("duplicate create")
	}
	if groupCount(t, h, first) != 2 {
		t.Fatal("duplicate invite")
	}
	body["name"] = "Changed"
	if code, _ := h.request(alice, "POST", "/groups", body); code != 409 {
		t.Fatal("reuse conflict", code)
	}
	h.assign(bob, h.Other)
	if groupCount(t, h, first) != 2 {
		t.Fatal("department transfer changed project")
	}
	h.identity(bob, "disabled", 2)
	h.identity(bob, "active", 3)
	if groupCount(t, h, first) != 1 {
		t.Fatal("project grant resurrected")
	}
	if code, _ := h.request(bob, "GET", "/groups/"+first, nil); code != 403 {
		t.Fatal("reactivated project access", code)
	}
}
func TestProjectMembershipEpochsAndIdempotentTransfer(t *testing.T) {
	h := newH(t)
	conv := project(h, "project", alice)
	old := h.send(alice, conv, "old", "before outsider joined")["message_id"].(string)
	invite := map[string]any{"request_id": "invite", "user_ids": []string{outsider}}
	h.ok(admin, "POST", "/groups/"+conv+"/members", invite)
	h.ok(admin, "POST", "/groups/"+conv+"/members", invite)
	h.ok(admin, "POST", "/groups/"+conv+"/members", map[string]any{"request_id": "another-invite", "user_ids": []string{outsider}})
	history, _ := json.Marshal(h.ok(outsider, "GET", "/conversations/"+conv+"/messages", nil))
	if bytes.Contains(history, []byte(old)) {
		t.Fatal("new member saw prior history")
	}
	h.ok(outsider, "POST", "/groups/"+conv+"/leave", op("leave"))
	h.ok(outsider, "POST", "/groups/"+conv+"/leave", op("leave"))
	gap := h.send(alice, conv, "gap", "after leave")["message_id"].(string)
	history, _ = json.Marshal(h.ok(outsider, "GET", "/conversations/"+conv+"/messages", nil))
	if bytes.Contains(history, []byte(gap)) {
		t.Fatal("former member saw new history")
	}
	// Replaying the earlier invitation must not restore a later revoked grant.
	h.ok(admin, "POST", "/groups/"+conv+"/members", invite)
	if groupCount(t, h, conv) != 2 {
		t.Fatal("replay restored membership")
	}
	invite["request_id"] = "rejoin"
	h.ok(admin, "POST", "/groups/"+conv+"/members", invite)
	history, _ = json.Marshal(h.ok(outsider, "GET", "/conversations/"+conv+"/messages", nil))
	if bytes.Contains(history, []byte(gap)) || bytes.Contains(history, []byte(old)) {
		t.Fatal("rejoin expanded history")
	}
	var epochs int
	if e := h.D.Store.Pool.QueryRow(context.Background(), `SELECT count(*) FROM member_epochs WHERE conversation_id=$1 AND user_id=$2`, conv, outsider).Scan(&epochs); e != nil || epochs != 2 {
		t.Fatal("epochs", epochs, e)
	}
	if code, _ := h.request(admin, "POST", "/groups/"+conv+"/leave", op("owner-leave")); code != 409 {
		t.Fatal("owner left", code)
	}
	transfer := map[string]any{"request_id": "transfer", "user_id": alice}
	h.ok(admin, "PUT", "/groups/"+conv+"/owner", transfer)
	h.ok(admin, "PUT", "/groups/"+conv+"/owner", transfer)
	h.ok(admin, "POST", "/groups/"+conv+"/leave", op("after-transfer"))
	if got := h.ok(alice, "GET", "/groups/"+conv, nil)["owner_user_id"]; got != alice {
		t.Fatal("owner", got)
	}
}
func TestDepartmentExplicitGrantsSurviveOrganizationSync(t *testing.T) {
	h := newH(t)
	conv := h.ok(admin, "POST", "/organization/departments/"+h.Other+"/group", nil)["conversation_id"].(string)
	secret := h.send(outsider, conv, "before", "before explicit admin join")["message_id"].(string)
	managed := h.ok(admin, "GET", "/organization/groups/"+conv, nil)
	if managed["is_member"] != false {
		t.Fatal("admin implicitly joined")
	}
	if code, _ := h.request(admin, "GET", "/conversations/"+conv+"/messages", nil); code != 403 && code != 404 {
		t.Fatal("admin history bypass", code)
	}
	h.ok(admin, "POST", "/organization/groups/"+conv+"/members", map[string]any{"request_id": "join", "user_ids": []string{admin, alice}})
	h.assign(alice, h.Other)
	h.assign(alice, h.Dept)
	h.identity(alice, "active", 2)
	if groupCount(t, h, conv) != 3 {
		t.Fatal("explicit collaborator removed")
	}
	history, _ := json.Marshal(h.ok(admin, "GET", "/conversations/"+conv+"/messages", nil))
	if bytes.Contains(history, []byte(secret)) {
		t.Fatal("explicit admin read old history")
	}
	if code, _ := h.request(admin, "DELETE", "/organization/groups/"+conv+"/members", map[string]any{"request_id": "remove-auto", "user_ids": []string{outsider}}); code != 409 {
		t.Fatal("auto member removed", code)
	}
	h.ok(admin, "DELETE", "/organization/groups/"+conv+"/members", map[string]any{"request_id": "remove-extra", "user_ids": []string{alice}})
	h.assign(bob, h.Dept)
	if groupCount(t, h, conv) != 2 {
		t.Fatal("revoked grant restored")
	}
	members := h.ok(admin, "GET", "/groups/"+conv+"/members", nil)["items"].([]any)
	for _, raw := range members {
		m := raw.(map[string]any)
		expected := "automatic"
		if m["user_id"] == admin {
			expected = "invited"
		}
		if m["membership_source"] != expected {
			t.Fatal("source", m)
		}
	}
}
func TestProjectRolesMuteArchiveAndAtomicLimits(t *testing.T) {
	h := newH(t)
	conv := project(h, "project", alice, bob)
	if code, _ := h.request(alice, "PATCH", "/groups/"+conv, map[string]any{"request_id": "member-edit", "name": "unauthorized"}); code != 403 {
		t.Fatal("member editing", code)
	}
	h.ok(admin, "PATCH", "/groups/"+conv+"/members/"+alice, map[string]any{"request_id": "promote", "role": "admin"})
	if code, _ := h.request(alice, "PUT", "/groups/"+conv+"/owner", map[string]any{"request_id": "steal", "user_id": alice}); code != 403 {
		t.Fatal("admin owner escalation", code)
	}
	h.ok(alice, "PATCH", "/groups/"+conv, map[string]any{"request_id": "edit", "name": "Updated", "announcement": "Decisions", "mute_all": true})
	if code, _ := h.request(bob, "POST", "/conversations/"+conv+"/messages", map[string]any{"client_message_id": "muted", "text": "muted"}); code != 403 {
		t.Fatal("mute all", code)
	}
	h.send(alice, conv, "admin-send", "admin can send")
	h.ok(alice, "PATCH", "/groups/"+conv, map[string]any{"request_id": "unmute", "mute_all": false})
	h.ok(alice, "PATCH", "/groups/"+conv+"/members/"+bob, map[string]any{"request_id": "mute-bob", "mute_until": time.Now().Add(time.Hour).UTC().Format(time.RFC3339)})
	if code, _ := h.request(bob, "POST", "/conversations/"+conv+"/messages", map[string]any{"client_message_id": "muted-bob", "text": "muted"}); code != 403 {
		t.Fatal("individual mute", code)
	}
	h.D.Cfg.Limits.GroupMaxMembers = 3
	if code, _ := h.request(admin, "POST", "/groups/"+conv+"/members", map[string]any{"request_id": "full", "user_ids": []string{outsider}}); code != 409 {
		t.Fatal("capacity", code)
	}
	var grants int
	if e := h.D.Store.Pool.QueryRow(context.Background(), `SELECT count(*) FROM group_member_grants WHERE conversation_id=$1 AND user_id=$2`, conv, outsider).Scan(&grants); e != nil || grants != 0 {
		t.Fatal("failed invite persisted", grants, e)
	}
	if code, _ := h.request(alice, "POST", "/groups/"+conv+"/archive", op("archive-admin")); code != 403 {
		t.Fatal("admin archive", code)
	}
	h.ok(admin, "POST", "/groups/"+conv+"/archive", op("archive"))
	h.ok(admin, "POST", "/groups/"+conv+"/archive", op("archive"))
	if code, _ := h.request(admin, "POST", "/conversations/"+conv+"/messages", map[string]any{"client_message_id": "archived-send", "text": "closed"}); code != 409 {
		t.Fatal("archive send", code)
	}
	if len(h.ok(alice, "GET", "/conversations/"+conv+"/messages", nil)["items"].([]any)) == 0 {
		t.Fatal("archive lost history")
	}
	if len(h.ok(alice, "GET", "/groups", nil)["items"].([]any)) != 0 {
		t.Fatal("archived group listed as current")
	}
}

func TestArchivedProjectDoesNotRestoreDisabledMembership(t *testing.T) {
	h := newH(t)
	conv := project(h, "archive-members", alice, bob)
	h.ok(admin, "POST", "/groups/"+conv+"/archive", op("archive"))
	h.identity(bob, "disabled", 2)
	h.identity(bob, "active", 3)
	if groupCount(t, h, conv) != 2 {
		t.Fatal("archived membership resurrected")
	}
	if code, _ := h.request(bob, "GET", "/groups/"+conv, nil); code != 403 {
		t.Fatal("archived current member access", code)
	}
}
