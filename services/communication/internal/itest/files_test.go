package itest

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"mime"
	"net/http"
	"testing"
	"time"
)

func TestPrivateFileSendForwardRecallAndClear(t *testing.T) {
	h := newH(t)
	conv := h.direct(alice, bob)
	target := h.direct(bob, outsider)
	if err := h.App.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(h.App.Stop)
	content := bytes.Repeat([]byte("x"), 11<<20) // exceeds the image limit, within the document limit
	sum := sha256.Sum256(content)
	init := h.ok(alice, "POST", "/media/uploads", map[string]any{"purpose": "chat_file", "filename": "../报告.html", "mime": "text/html", "size_bytes": len(content), "sha256": hex.EncodeToString(sum[:])})
	up, asset := init["upload_id"].(string), init["asset_id"].(string)
	req, _ := http.NewRequest("PUT", h.Server.URL+"/api/app/v1/media/uploads/"+up+"/content", bytes.NewReader(content))
	req.Header.Set("Authorization", "Bearer "+h.token(alice, "request", 1))
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(res.Body)
	res.Body.Close()
	if res.StatusCode != 200 {
		t.Fatalf("upload: %d %s", res.StatusCode, body)
	}
	h.ok(alice, "POST", "/media/uploads/"+up+"/complete", nil)
	deadline := time.Now().Add(5 * time.Second)
	for {
		status := h.ok(alice, "GET", "/media/uploads/"+up, nil)
		if status["state"] == "ready" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("file not ready: %v", status)
		}
		time.Sleep(20 * time.Millisecond)
	}
	msg := h.ok(alice, "POST", "/conversations/"+conv+"/messages", map[string]any{"client_message_id": "file-send", "asset_ids": []string{asset}})["message_id"].(string)
	history := h.ok(bob, "GET", "/conversations/"+conv+"/messages", nil)["items"].([]any)
	item := history[0].(map[string]any)
	attachment := item["attachments"].([]any)[0].(map[string]any)
	if item["type"] != "file" || attachment["filename"] != "报告.html" || attachment["purpose"] != "chat_file" {
		t.Fatal(item)
	}
	download := func(user string, want int) {
		t.Helper()
		req, _ := http.NewRequest("GET", h.Server.URL+"/api/app/v1/media/"+asset, nil)
		req.Header.Set("Authorization", "Bearer "+h.token(user, "request", h.Versions[user]))
		res, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer res.Body.Close()
		if res.StatusCode != want {
			t.Fatalf("download: %d want %d", res.StatusCode, want)
		}
		if want == 200 {
			if res.Header.Get("Content-Type") != "application/octet-stream" {
				t.Fatal("unsafe inline type")
			}
			disposition, params, err := mime.ParseMediaType(res.Header.Get("Content-Disposition"))
			if err != nil || disposition != "attachment" || params["filename"] != "报告.html" {
				t.Fatal("unsafe filename or disposition")
			}
			data, _ := io.ReadAll(res.Body)
			if !bytes.Equal(data, content) {
				t.Fatal("file bytes changed")
			}
		}
	}
	download(bob, 200)
	reply := h.ok(bob, "POST", "/conversations/"+conv+"/messages", map[string]any{"client_message_id": "file-reply", "text": "收到文件", "reply_to_message_id": msg})
	if reply["message_id"] == nil {
		t.Fatal("file reply missing")
	}
	download(outsider, 404)
	h.ok(bob, "POST", "/messages/"+msg+"/forward", map[string]any{"client_message_id": "file-forward", "to_conversation_ids": []string{target}})
	download(outsider, 200)
	h.ok(alice, "POST", "/conversations/"+conv+"/clear", map[string]any{})
	download(alice, 404)
	h.ok(alice, "POST", "/messages/"+msg+"/recall", nil)
	download(outsider, 200) // the independently forwarded message remains visible
	copy := h.ok(bob, "GET", "/conversations/"+target+"/messages", nil)["items"].([]any)[0].(map[string]any)
	if copy["type"] != "file" {
		t.Fatal(copy)
	}
	h.ok(bob, "POST", "/messages/"+copy["message_id"].(string)+"/recall", nil)
	download(bob, 404)
	download(outsider, 404)
	code, _ := h.request(alice, "POST", "/media/uploads", map[string]any{"purpose": "chat_file", "size_bytes": (50 << 20) + 1, "sha256": hex.EncodeToString(sum[:])})
	if code != 413 {
		t.Fatal("oversized file accepted", code)
	}
}
