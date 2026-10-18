package itest

import (
	"encoding/json"
	"testing"
)

func TestNewDepartmentPublishesDirectoryChange(t *testing.T) {
	h := newH(t)
	snapshot := h.ok(alice, "POST", "/sync/snapshot", nil)
	h.ok(admin, "POST", "/organization/departments", map[string]any{"name": "Empty department"})
	page := h.ok(alice, "GET", "/sync?cursor="+snapshot["cursor"].(string), nil)
	for _, raw := range page["events"].([]any) {
		if raw.(map[string]any)["type"] == "contacts.changed" {
			return
		}
	}
	t.Fatal("employee received no notification for the new department")
}

func TestClearedHistoryCannotBeRepliedToOrForwarded(t *testing.T) {
	for _, kind := range []string{"private", "group"} {
		t.Run(kind, func(t *testing.T) {
			h := newH(t)
			var conv string
			if kind == "private" {
				conv = h.direct(alice, bob)
			} else {
				conv = h.ok(admin, "POST", "/organization/departments/"+h.Dept+"/group", nil)["conversation_id"].(string)
			}
			old := h.send(alice, conv, "before-clear", "clear me")["message_id"].(string)
			h.ok(bob, "POST", "/conversations/"+conv+"/clear", map[string]any{})
			if code, body := h.request(bob, "POST", "/messages/"+old+"/forward", map[string]any{
				"client_message_id": "forward-cleared", "to_conversation_ids": []string{conv},
			}); code != 403 {
				t.Errorf("forwarded cleared history: %d %v", code, body)
			}
			if code, body := h.request(bob, "POST", "/conversations/"+conv+"/messages", map[string]any{
				"client_message_id": "reply-cleared", "text": "reply", "reply_to_message_id": old,
			}); code != 403 {
				t.Errorf("replied to cleared history: %d %v", code, body)
			}
		})
	}
}

func TestClearedPrivateMessagesDoNotStayUnread(t *testing.T) {
	h := newH(t)
	conv := h.direct(alice, bob)
	h.send(alice, conv, "unread", "unread")
	h.ok(bob, "POST", "/conversations/"+conv+"/clear", map[string]any{})
	page := h.ok(bob, "GET", "/conversations", nil)
	for _, raw := range page["items"].([]any) {
		item := raw.(map[string]any)
		if item["conversation_id"] == conv && item["unread"] != float64(0) {
			t.Fatalf("cleared messages still unread: %v", item)
		}
	}
}

func TestGroupMentionMetadataSurvivesHistoryReload(t *testing.T) {
	h := newH(t)
	conv := h.ok(admin, "POST", "/organization/departments/"+h.Dept+"/group", nil)["conversation_id"].(string)
	h.ok(admin, "POST", "/conversations/"+conv+"/messages", map[string]any{
		"client_message_id": "mention", "text": "notice", "mention_user_ids": []string{alice}, "mention_all": true,
	})
	page := h.ok(alice, "GET", "/conversations/"+conv+"/messages", nil)
	message := page["items"].([]any)[0].(map[string]any)
	mentions, _ := json.Marshal(message["mention_user_ids"])
	want, _ := json.Marshal([]string{alice})
	if string(mentions) != string(want) || message["mention_all"] != true {
		t.Fatalf("lost mention metadata: %v", message)
	}
}

func TestConversationListReflectsOwnReadAndHiddenState(t *testing.T) {
	h := newH(t)
	conv := h.direct(alice, bob)
	first := h.send(alice, conv, "first", "first")
	h.ok(bob, "POST", "/conversations/"+conv+"/read", map[string]any{"last_read_seq": first["seq"]})
	h.ok(bob, "POST", "/conversations/"+conv+"/hide", nil)
	page := h.ok(bob, "GET", "/conversations?include_hidden=1", nil)
	item := page["items"].([]any)[0].(map[string]any)
	if item["last_read_seq"] != first["seq"] || item["hidden_at_seq"] != first["seq"] {
		t.Fatalf("missing own conversation state: %v", item)
	}
	h.send(bob, conv, "unhide", "unhide")
	page = h.ok(bob, "GET", "/conversations?include_hidden=1", nil)
	item = page["items"].([]any)[0].(map[string]any)
	if item["hidden_at_seq"] != nil || item["last_read_seq"] != item["last_seq"] {
		t.Fatalf("stale own conversation state: %v", item)
	}
}
