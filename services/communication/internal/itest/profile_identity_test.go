package itest

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"testing"
)

func portraitIdentity(t *testing.T, h *H, user string, version int64, url string) {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"user_id": user, "version": version, "nickname": user, "status": "active", "permissions": []string{"contacts", "messaging"}, "avatar_icon": "business", "avatar_url": url})
	req, _ := http.NewRequest("PUT", h.Server.URL+"/internal/identities/"+user, bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+h.token(user, "identity", version))
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		t.Fatal("portrait identity", response.StatusCode)
	}
	if h.Versions[user] < version {
		h.Versions[user] = version
	}
}

func profileSnapshot(h *H, user string) []any {
	snapshot := h.ok(user, "POST", "/sync/snapshot", nil)
	return h.ok(user, "GET", "/sync/snapshots/"+snapshot["snapshot_id"].(string), nil)["events"].([]any)
}

func TestAccountPortraitsAndPositionsAcrossDirectorySnapshotAndGroups(t *testing.T) {
	h := newH(t)
	url := "/api/users/2/avatar?v=123"
	// Existing identities receive uploaded portraits on startup without a new revision.
	portraitIdentity(t, h, alice, 1, url)
	var stored string
	if err := h.D.Store.Pool.QueryRow(context.Background(), "SELECT avatar_url FROM users WHERE id=$1", alice).Scan(&stored); err != nil || stored != url {
		t.Fatal("existing portrait not backfilled", stored, err)
	}
	portraitIdentity(t, h, alice, 2, url)
	title := h.ok(admin, "POST", "/organization/job-titles", map[string]string{"name": "产品经理"})
	h.ok(admin, "PUT", "/organization/users/"+alice+"/job-title", map[string]any{"job_title_id": title["job_title_id"]})
	conv := project(h, "portrait-roster", alice, bob)
	assertProfile := func(person map[string]any) {
		t.Helper()
		if person["avatar_url"] != url || person["avatar_icon"] != "business" || person["job_title"] != "产品经理" {
			t.Fatal("incomplete account profile", person)
		}
	}
	for _, people := range [][]any{
		h.ok(bob, "GET", "/contacts", nil)["items"].([]any),
		h.ok(admin, "GET", "/organization/users", nil)["items"].([]any),
		h.ok(bob, "GET", "/groups/"+conv+"/members", nil)["items"].([]any),
		h.ok(admin, "GET", "/organization/groups/"+conv, nil)["members"].([]any),
	} {
		found := false
		for _, raw := range people {
			person := raw.(map[string]any)
			if person["user_id"] == alice {
				assertProfile(person)
				found = true
			}
		}
		if !found {
			t.Fatal("account absent from roster")
		}
	}
	for _, viewer := range []string{alice, bob} {
		found := false
		for _, raw := range profileSnapshot(h, viewer) {
			event := raw.(map[string]any)
			person := event["payload"].(map[string]any)
			if person["user_id"] == alice {
				assertProfile(person)
				found = true
			}
		}
		if !found {
			t.Fatal("account absent from snapshot")
		}
	}
	// Older bridges omit avatar_url. Clearing requires an explicit empty URL.
	h.identity(alice, "active", 3)
	if err := h.D.Store.Pool.QueryRow(context.Background(), "SELECT avatar_url FROM users WHERE id=$1", alice).Scan(&stored); err != nil || stored != url {
		t.Fatal("legacy update lost portrait", stored, err)
	}
	portraitIdentity(t, h, alice, 4, "")
	portraitIdentity(t, h, alice, 2, url)
	if err := h.D.Store.Pool.QueryRow(context.Background(), "SELECT avatar_url FROM users WHERE id=$1", alice).Scan(&stored); err != nil || stored != "" {
		t.Fatal("removed portrait resurrected", stored, err)
	}
}

func TestMessagingOnlySnapshotIncludesRelatedPeopleWithoutDirectoryAccess(t *testing.T) {
	h := newH(t)
	h.direct(alice, bob)
	if _, err := h.D.Store.Pool.Exec(context.Background(), `UPDATE users SET permissions='["messaging"]'::jsonb WHERE id=$1`, alice); err != nil {
		t.Fatal(err)
	}
	found := false
	for _, raw := range profileSnapshot(h, alice) {
		event := raw.(map[string]any)
		if event["type"] != "contact.updated" {
			continue
		}
		person := event["payload"].(map[string]any)
		if person["user_id"] == bob {
			found = true
		} else {
			t.Fatal("unrelated directory entry exposed", person)
		}
	}
	if !found {
		t.Fatal("private peer profile missing")
	}
	project(h, "messaging-only-roster", alice)
	found = false
	for _, raw := range profileSnapshot(h, alice) {
		event := raw.(map[string]any)
		if event["type"] == "contact.updated" {
			person := event["payload"].(map[string]any)
			if person["user_id"] == outsider {
				t.Fatal("unrelated directory entry exposed", person)
			}
			if person["user_id"] == admin {
				found = true
			}
		}
	}
	if !found {
		t.Fatal("group member profile missing")
	}
	base := h.ok(alice, "POST", "/sync/snapshot", nil)
	title := h.ok(admin, "POST", "/organization/job-titles", map[string]string{"name": "技术主管"})
	h.ok(admin, "PUT", "/organization/users/"+bob+"/job-title", map[string]any{"job_title_id": title["job_title_id"]})
	changed := false
	for _, raw := range h.ok(alice, "GET", "/sync?cursor="+base["cursor"].(string), nil)["events"].([]any) {
		if raw.(map[string]any)["type"] == "contacts.changed" {
			changed = true
		}
	}
	if !changed {
		t.Fatal("messaging-only client not notified of position change")
	}
}
