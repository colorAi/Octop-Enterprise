package itest

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"image"
	"image/png"
	"net/http"
	"testing"

	"octop/communication/internal/mediasvc"
)

func TestGroupExpertArtifactsStayPrivateUntilReplyAndFollowVisibility(t *testing.T) {
	h, conv, _ := expertSetup(t)
	b := inviteExpert(h, conv, 0)
	question := askExpert(h, conv, b["binding_id"].(string), "image-question", "")
	call := question["expert_call"].(map[string]any)["call_id"].(string)
	admitExpert(h, call)
	var imageBytes bytes.Buffer
	if err := png.Encode(&imageBytes, image.NewRGBA(image.Rect(0, 0, 8, 8))); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(imageBytes.Bytes())
	body := map[string]any{"run_id": "run-" + call, "operation_id": "image-one", "file_id": hex.EncodeToString(sum[:]), "filename": "cat.png", "mime": "image/png", "data": imageBytes.Bytes()}
	path := "/calls/" + call + "/artifacts"
	asset := expertOK(h, admin, "POST", path, body)["asset_id"].(string)
	if expertOK(h, admin, "POST", path, body)["asset_id"] != asset {
		t.Fatal("lost receipt duplicated artifact")
	}
	var upload string
	if err := h.D.Store.Pool.QueryRow(context.Background(), `SELECT id FROM upload_operations WHERE asset_id=$1`, asset).Scan(&upload); err != nil {
		t.Fatal(err)
	}
	payload, _ := json.Marshal(map[string]any{"asset_id": asset, "upload_id": upload})
	if err := mediasvc.NewProcessJob(h.D)(context.Background(), payload); err != nil {
		t.Fatal(err)
	}
	if expertOK(h, admin, "GET", path+"/"+asset, nil)["state"] != "ready" {
		t.Fatal("not ready")
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
			t.Fatalf("%s download got %d want %d", user, res.StatusCode, want)
		}
	}
	download(alice, 404)
	download(bob, 404)
	download(admin, 404)
	value := map[string]any{"call_id": call, "execution_id": "run-" + call, "execution_state": "completed", "result": map[string]any{"text": "Generated cat", "attachments": []string{asset}}}
	raw, _ := json.Marshal(value)
	digest := sha256.Sum256(raw)
	result := map[string]any{"result_wire": string(raw), "result_digest": hex.EncodeToString(digest[:])}
	receipt := expertOK(h, admin, "POST", "/calls/"+call+"/result", result)
	if expertOK(h, admin, "POST", "/calls/"+call+"/result", result)["message_id"] != receipt["message_id"] {
		t.Fatal("duplicate reply")
	}
	message := receipt["message_id"].(string)
	item := h.ok(bob, "GET", "/conversations/"+conv+"/messages/"+message, nil)
	if item["attachments"].([]any)[0].(map[string]any)["asset_id"] != asset {
		t.Fatal("missing output")
	}
	download(alice, 200)
	download(bob, 200)
	download(outsider, 404)
	download(admin, 200)
	h.ok(bob, "POST", "/conversations/"+conv+"/clear", nil)
	download(bob, 404)
	download(alice, 200)
	// A recalled expert reply must revoke the attachment just like any message.
	if _, err := h.D.Store.Pool.Exec(context.Background(), `UPDATE messages SET recalled_at=now(),text=NULL WHERE id=$1`, message); err != nil {
		t.Fatal(err)
	}
	download(alice, 404)
}

func TestGroupExpertRejectsArtifactFromAnotherCall(t *testing.T) {
	h, conv, _ := expertSetup(t)
	b := inviteExpert(h, conv, 0)
	question := askExpert(h, conv, b["binding_id"].(string), "cross-artifact", "")
	call := question["expert_call"].(map[string]any)["call_id"].(string)
	admitExpert(h, call)
	value := map[string]any{"call_id": call, "execution_id": "run-" + call, "execution_state": "completed", "result": map[string]any{"text": "Wrong output", "attachments": []string{"asset-from-other-call"}}}
	raw, _ := json.Marshal(value)
	sum := sha256.Sum256(raw)
	code, body := expertInternal(h, admin, "POST", "/calls/"+call+"/result", map[string]any{"result_wire": string(raw), "result_digest": hex.EncodeToString(sum[:])})
	if code != 200 || body["data"].(map[string]any)["call_state"] != "failed" {
		t.Fatal(code, body)
	}
	var count int
	if err := h.D.Store.Pool.QueryRow(context.Background(), `SELECT count(*) FROM messages WHERE client_message_id=$1 AND sender_type='group_expert'`, call).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatal("reply partially committed")
	}
}
