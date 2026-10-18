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
	"os"
	"path/filepath"
	"testing"

	"octop/communication/internal/mediasvc"
)

func TestMediaProcessingRetryAfterCommittedResult(t *testing.T) {
	for _, purpose := range []string{"chat_file", "chat_image"} {
		t.Run(purpose, func(t *testing.T) {
			h := newH(t)
			ctx := context.Background()
			content := []byte("a completed file")
			mime := "text/plain"
			if purpose == "chat_image" {
				var buf bytes.Buffer
				if err := png.Encode(&buf, image.NewRGBA(image.Rect(0, 0, 8, 8))); err != nil {
					t.Fatal(err)
				}
				content = buf.Bytes()
				mime = "image/png"
			}
			sum := sha256.Sum256(content)
			value := h.ok(alice, "POST", "/media/uploads", map[string]any{"purpose": purpose, "filename": "example", "mime": mime, "size_bytes": len(content), "sha256": hex.EncodeToString(sum[:])})
			up, asset := value["upload_id"].(string), value["asset_id"].(string)
			req, _ := http.NewRequest("PUT", h.Server.URL+"/api/app/v1/media/uploads/"+up+"/content", bytes.NewReader(content))
			req.Header.Set("Authorization", "Bearer "+h.token(alice, "request", 1))
			res, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatal(err)
			}
			res.Body.Close()
			if res.StatusCode != 200 {
				t.Fatal(res.StatusCode)
			}
			h.ok(alice, "POST", "/media/uploads/"+up+"/complete", nil)
			payload, _ := json.Marshal(map[string]string{"upload_id": up, "asset_id": asset})
			process := mediasvc.NewProcessJob(h.D)
			if err := process(ctx, payload); err != nil {
				t.Fatal(err)
			}
			var tmp, rel, before string
			if err := h.D.Store.Pool.QueryRow(ctx, `SELECT tmp_path FROM upload_operations WHERE id=$1`, up).Scan(&tmp); err != nil {
				t.Fatal(err)
			}
			if _, err := os.Stat(tmp); !os.IsNotExist(err) {
				t.Fatalf("temp file remains: %v", err)
			}
			if err := h.D.Store.Pool.QueryRow(ctx, `SELECT relpath,to_jsonb(ma)::text FROM media_assets ma WHERE id=$1`, asset).Scan(&rel, &before); err != nil {
				t.Fatal(err)
			}
			original, err := os.ReadFile(filepath.Join(h.D.Cfg.MediaRoot, rel))
			if err != nil {
				t.Fatal(err)
			}
			// Simulate a lease takeover after media committed but before the queue ack.
			if err := process(ctx, payload); err != nil {
				t.Fatalf("retry rejected a committed media result: %v", err)
			}
			var after string
			if err := h.D.Store.Pool.QueryRow(ctx, `SELECT to_jsonb(ma)::text FROM media_assets ma WHERE id=$1`, asset).Scan(&after); err != nil {
				t.Fatal(err)
			}
			again, err := os.ReadFile(filepath.Join(h.D.Cfg.MediaRoot, rel))
			if err != nil {
				t.Fatal(err)
			}
			if before != after || !bytes.Equal(original, again) {
				t.Fatal("retry rewrote completed media")
			}
		})
	}
}
