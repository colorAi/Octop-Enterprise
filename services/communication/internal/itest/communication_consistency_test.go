package itest

import (
	"testing"
	"time"
)

func TestGroupAdministratorPersonalMute(t *testing.T) {
	for _, kind := range []string{"company", "department", "project"} {
		t.Run(kind, func(t *testing.T) {
			h := newH(t)
			var conv string
			switch kind {
			case "company":
				conv = h.ok(admin, "POST", "/organization/company-group", op("company"))["conversation_id"].(string)
			case "department":
				conv = h.ok(admin, "POST", "/organization/departments/"+h.Dept+"/group", nil)["conversation_id"].(string)
			default:
				conv = project(h, "project", alice, bob)
			}
			h.ok(admin, "PATCH", "/groups/"+conv+"/members/"+alice, map[string]any{"request_id": "promote", "role": "admin"})
			h.ok(admin, "PATCH", "/groups/"+conv, map[string]any{"request_id": "mute-all", "mute_all": true})
			h.send(alice, conv, "admin-announcement", "administrator can announce during mute-all")
			h.ok(admin, "PATCH", "/groups/"+conv+"/members/"+alice, map[string]any{
				"request_id": "personal-mute", "mute_until": time.Now().Add(time.Hour).UTC().Format(time.RFC3339),
			})
			if code, body := h.request(alice, "POST", "/conversations/"+conv+"/messages", map[string]any{
				"client_message_id": "muted-send", "text": "must be blocked",
			}); code != 403 {
				t.Errorf("personally muted administrator sent: %d %v", code, body)
			}
			source := h.direct(alice, bob)
			msg := h.send(bob, source, "source", "forward source")["message_id"].(string)
			forward := map[string]any{"client_message_id": "forward-muted", "to_conversation_ids": []string{conv}}
			result := h.ok(alice, "POST", "/messages/"+msg+"/forward", forward)
			if failed, ok := result["failed"].([]any); !ok || len(failed) != 1 {
				t.Errorf("personally muted administrator forwarded: %v", result)
			}
			h.send(admin, conv, "owner-send", "owner remains able to send")
			h.ok(admin, "PATCH", "/groups/"+conv+"/members/"+alice, map[string]any{"request_id": "unmute", "mute_until": nil})
			h.send(alice, conv, "unmuted-send", "individual mute removed")
			result = h.ok(alice, "POST", "/messages/"+msg+"/forward", forward)
			if created, ok := result["created"].([]any); !ok || len(created) != 1 {
				t.Fatal("unmuted administrator could not forward", result)
			}
		})
	}
}

func TestRecallReachesFormerMembersWithinVisibleEpoch(t *testing.T) {
	for _, kind := range []string{"department", "project"} {
		t.Run(kind, func(t *testing.T) {
			h := newH(t)
			var conv string
			if kind == "department" {
				conv = h.ok(admin, "POST", "/organization/departments/"+h.Dept+"/group", nil)["conversation_id"].(string)
			} else {
				conv = project(h, "project", alice, bob)
			}
			msg := h.send(alice, conv, "before-leave", "visible before leaving")["message_id"].(string)
			if kind == "department" {
				h.assign(bob, h.Other)
				h.assign(outsider, h.Dept)
			} else {
				h.ok(bob, "POST", "/groups/"+conv+"/leave", op("leave"))
				h.ok(admin, "POST", "/groups/"+conv+"/members", map[string]any{"request_id": "invite", "user_ids": []string{outsider}})
			}
			h.ok(admin, "POST", "/conversations/"+conv+"/clear", map[string]any{})
			if code, _ := h.request(bob, "GET", "/conversations/"+conv+"/messages/"+msg, nil); code != 200 {
				t.Fatal("former member lost authorized history", code)
			}
			cursors := map[string]string{}
			for _, user := range []string{alice, bob, admin, outsider} {
				cursors[user] = h.ok(user, "POST", "/sync/snapshot", nil)["cursor"].(string)
			}
			h.ok(alice, "POST", "/messages/"+msg+"/recall", nil)
			for _, user := range []string{alice, bob, admin, outsider} {
				events := h.ok(user, "GET", "/sync?cursor="+cursors[user], nil)["events"].([]any)
				count := 0
				for _, raw := range events {
					event := raw.(map[string]any)
					if event["type"] == "message.recall" && event["object_id"] == msg {
						count++
					}
				}
				want := 0
				if user == alice || user == bob {
					want = 1
				}
				if count != want {
					t.Errorf("recall events for %s: got %d, want %d", user, count, want)
				}
			}
			if item := h.ok(bob, "GET", "/conversations/"+conv+"/messages/"+msg, nil); item["recalled"] != true || item["text"] != nil {
				t.Fatal("former member history did not reflect recall", item)
			}
		})
	}
}

func TestForwardProcessesAllTargetsAndRetriesWithoutDuplicates(t *testing.T) {
	h := newH(t)
	source := h.direct(alice, bob)
	msg := h.send(bob, source, "source", "forward to every authorized target")["message_id"].(string)
	first := h.direct(alice, outsider)
	departed := project(h, "departed", alice, bob)
	last := h.direct(alice, admin)
	// Membership can change after the user selected the destination in the UI.
	h.ok(alice, "POST", "/groups/"+departed+"/leave", op("leave"))
	body := map[string]any{"client_message_id": "batch", "to_conversation_ids": []string{first, departed, "missing-conversation", last}}
	for attempt := 0; attempt < 2; attempt++ {
		result := h.ok(alice, "POST", "/messages/"+msg+"/forward", body)
		created, _ := result["created"].([]any)
		failed, _ := result["failed"].([]any)
		if len(created) != 2 || len(failed) != 2 {
			t.Errorf("incomplete batch result on attempt %d: %v", attempt, result)
		}
		for _, target := range []string{first, last} {
			items := h.ok(alice, "GET", "/conversations/"+target+"/messages", nil)["items"].([]any)
			if len(items) != 1 || items[0].(map[string]any)["text"] != "forward to every authorized target" {
				t.Errorf("target %s lost or duplicated the forward: %v", target, items)
			}
		}
	}
}
