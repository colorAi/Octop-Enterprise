package itest

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"testing"
)

func TestReplyPreviewRespectsHistoryVisibility(t *testing.T) {
	for _, kind := range []string{"private", "group"} {
		t.Run(kind, func(t *testing.T) {
			h := newH(t)
			conv := h.direct(alice, bob)
			if kind == "group" {
				conv = h.ok(admin, "POST", "/organization/departments/"+h.Dept+"/group", nil)["conversation_id"].(string)
			}
			first := h.send(alice, conv, "source", "original confidential text")
			original := first["message_id"].(string)
			reply := h.ok(bob, "POST", "/conversations/"+conv+"/messages", map[string]any{"client_message_id": "reply", "text": "response", "reply_to_message_id": original})
			page := h.ok(bob, "GET", "/conversations/"+conv+"/messages?limit=1", nil)
			item := page["items"].([]any)[0].(map[string]any)
			if item["message_id"] != reply["message_id"] || item["reply_preview"].(map[string]any)["text"] != "original confidential text" {
				t.Fatal(item)
			}
			h.ok(bob, "GET", "/conversations/"+conv+"/messages/"+original, nil)
			if code, _ := h.request(outsider, "GET", "/conversations/"+conv+"/messages/"+original, nil); code != 404 {
				t.Fatal("non-member preview", code)
			}
			h.ok(bob, "POST", "/conversations/"+conv+"/clear", map[string]any{"through_seq": first["seq"]})
			page = h.ok(bob, "GET", "/conversations/"+conv+"/messages?limit=1", nil)
			item = page["items"].([]any)[0].(map[string]any)
			if item["reply_preview"] != nil {
				t.Fatal("cleared quote leaked", item)
			}
			if code, _ := h.request(bob, "GET", "/conversations/"+conv+"/messages/"+original, nil); code != 404 {
				t.Fatal("cleared preview", code)
			}
			h.ok(alice, "POST", "/messages/"+original+"/recall", nil)
			tombstone := h.ok(alice, "GET", "/conversations/"+conv+"/messages/"+original, nil)
			if tombstone["text"] != nil || tombstone["attachments"] != nil || tombstone["recalled"] != true {
				t.Fatal(tombstone)
			}
		})
	}
}

func TestDeleteConversationOnlyChangesOwnVisibility(t *testing.T) {
	h := newH(t)
	conv := h.direct(alice, bob)
	h.send(alice, conv, "old", "keep for alice")
	if code, _ := h.request(outsider, "DELETE", "/conversations/"+conv, nil); code != 404 {
		t.Fatal("non-member delete", code)
	}
	h.ok(bob, "DELETE", "/conversations/"+conv, nil)
	if len(h.ok(bob, "GET", "/conversations", nil)["items"].([]any)) != 0 {
		t.Fatal("deleted still listed")
	}
	if len(h.ok(bob, "GET", "/conversations/"+conv+"/messages", nil)["items"].([]any)) != 0 {
		t.Fatal("deleted history still visible")
	}
	if len(h.ok(alice, "GET", "/conversations/"+conv+"/messages", nil)["items"].([]any)) != 1 {
		t.Fatal("peer history lost")
	}
	h.send(alice, conv, "new", "new message")
	if len(h.ok(bob, "GET", "/conversations", nil)["items"].([]any)) != 1 {
		t.Fatal("new message did not reopen conversation")
	}
	messages := h.ok(bob, "GET", "/conversations/"+conv+"/messages", nil)["items"].([]any)
	if len(messages) != 1 || messages[0].(map[string]any)["text"] != "new message" {
		t.Fatal("old history reappeared", messages)
	}
}

func TestReusablePositionsAndAdminDirectoryIdentity(t *testing.T) {
	h := newH(t)
	if len(h.ok(admin, "GET", "/organization/job-titles", nil)["items"].([]any)) < 8 {
		t.Fatal("missing presets")
	}
	title := h.ok(admin, "POST", "/organization/job-titles", map[string]string{"name": "  项目负责人  "})
	duplicate := h.ok(admin, "POST", "/organization/job-titles", map[string]string{"name": "项目负责人"})
	if title["job_title_id"] != duplicate["job_title_id"] {
		t.Fatal("duplicate position")
	}
	for _, u := range []string{alice, bob} {
		h.ok(admin, "PUT", "/organization/users/"+u+"/job-title", map[string]any{"job_title_id": title["job_title_id"]})
	}
	if code, _ := h.request(alice, "POST", "/organization/job-titles", map[string]string{"name": "hacker"}); code != 403 {
		t.Fatal("position permission", code)
	}
	if code, _ := h.request(admin, "PUT", "/organization/users/"+bob+"/job-title", map[string]string{"job_title_id": "unknown"}); code != 400 {
		t.Fatal("unknown title", code)
	}
	// Same-version reconciliation must populate administrators and new admin capabilities on an upgrade.
	if _, err := h.D.Store.Pool.Exec(context.Background(), `UPDATE users SET permissions=permissions - 'groups_create' WHERE id=$1`, admin); err != nil {
		t.Fatal(err)
	}
	body, _ := json.Marshal(map[string]any{"user_id": admin, "version": 1, "nickname": admin, "status": "active", "permissions": []string{"contacts", "messaging", "departments", "groups_create"}, "is_admin": true})
	req, _ := http.NewRequest("PUT", h.Server.URL+"/internal/identities/"+admin, bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+h.token(admin, "identity", 1))
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != 200 {
		t.Fatal(response.StatusCode)
	}
	project(h, "upgrade-admin-create", alice)
	contacts := h.ok(alice, "GET", "/contacts", nil)["items"].([]any)
	for _, raw := range contacts {
		person := raw.(map[string]any)
		switch person["user_id"] {
		case admin:
			if person["is_admin"] != true {
				t.Fatal("administrator flag lost", person)
			}
		case alice, bob:
			if person["job_title"] != "项目负责人" {
				t.Fatal("position lost", person)
			}
		}
	}
	departments := h.ok(alice, "GET", "/departments", nil)["items"].([]any)
	for _, raw := range departments {
		d := raw.(map[string]any)
		if d["department_id"] == h.Dept && d["member_count"] != float64(2) {
			t.Fatal("admin counted in directory", d)
		}
	}
	h.ok(admin, "PUT", "/organization/users/"+bob+"/job-title", map[string]any{"job_title_id": nil})
}
