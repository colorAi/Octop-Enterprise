package experts

import (
	"context"
	"encoding/hex"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"octop/communication/internal/auth"
	"octop/communication/internal/httpx"
	"octop/communication/internal/idgen"
	"octop/communication/internal/mediasvc"
	"octop/communication/internal/store"
)

type generatedArtifact struct {
	Run       string `json:"run_id"`
	Operation string `json:"operation_id"`
	File      string `json:"file_id"`
	Filename  string `json:"filename"`
	Mime      string `json:"mime"`
	Data      []byte `json:"data"`
}

func (s *Service) registerArtifacts(m *http.ServeMux) {
	m.HandleFunc("POST /internal/group-experts/v1/calls/{call}/artifacts", auth.ServiceRequestLimit(s.D, 70<<20, func(w http.ResponseWriter, r *http.Request, _ auth.Claims) {
		var v generatedArtifact
		if err := httpx.DecodeJSON(w, r, &v, 70<<20); err != nil {
			httpx.WriteErrorOf(w, r, err)
			return
		}
		purpose := "chat_file"
		limit := int64(mediasvc.MaxFileBytes)
		switch v.Mime {
		case "image/png", "image/jpeg", "image/webp", "image/gif":
			purpose, limit = "chat_image", s.D.Cfg.Resource.MediaMaxImageBytes
		case "video/mp4", "video/webm":
		default:
			httpx.WriteErrorOf(w, r, httpx.ErrUnsupportedMediaType)
			return
		}
		if len(v.Data) == 0 || int64(len(v.Data)) > limit || v.File != hash(v.Data) || len(v.Operation) == 0 || len(v.Operation) > 160 || len(v.Filename) > 255 || strings.ContainsAny(v.Filename, "/\\\r\n") {
			httpx.WriteErrorOf(w, r, httpx.ErrInvalidRequest)
			return
		}
		digest := hash(mustJSON(map[string]string{"run_id": v.Run, "operation_id": v.Operation, "file_id": v.File, "filename": v.Filename, "mime": v.Mime}))
		// File I/O happens before acquiring any authorization or group locks.
		upload := idgen.New("upl")
		tmp := filepath.Join(s.D.Cfg.MediaRoot, "tmp", upload)
		if err := os.MkdirAll(filepath.Dir(tmp), 0o750); err != nil {
			httpx.WriteErrorOf(w, r, err)
			return
		}
		if err := os.WriteFile(tmp, v.Data, 0o640); err != nil {
			_ = os.Remove(tmp)
			httpx.WriteErrorOf(w, r, err)
			return
		}
		keep := false
		defer func() {
			if !keep {
				_ = os.Remove(tmp)
			}
		}()
		var out any
		created := false
		err := s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
			ctx := r.Context()
			c, err := queryCall(ctx, tx, r.PathValue("call"), false)
			if err != nil {
				return err
			}
			if c.Execution == nil || *c.Execution != v.Run || c.State != "dispatched" {
				return failure("group_expert_not_dispatched")
			}
			if err = versionsMatch(ctx, tx, c); err != nil {
				return err
			}
			if _, err = contextFor(ctx, tx, c, true); err != nil {
				return err
			}
			var old, asset, state, filename, mime string
			err = tx.QueryRow(ctx, `SELECT a.request_digest,a.asset_id,m.state,m.filename,m.mime FROM group_expert_artifacts a JOIN media_assets m ON m.id=a.asset_id WHERE call_id=$1 AND operation_id=$2 AND file_id=$3`, c.ID, v.Operation, v.File).Scan(&old, &asset, &state, &filename, &mime)
			if err == nil {
				if filename != v.Filename || mime != v.Mime {
					return httpx.ErrIdemConflict
				}
				// Metadata plus the checked content hash also validates retries
				// of rows written with the former whole-body digest.
				if old != digest {
					if _, err = tx.Exec(ctx, `UPDATE group_expert_artifacts SET request_digest=$4 WHERE call_id=$1 AND operation_id=$2 AND file_id=$3`, c.ID, v.Operation, v.File, digest); err != nil {
						return err
					}
				}
				out = map[string]any{"asset_id": asset, "state": state}
				return nil
			}
			if !errors.Is(err, pgx.ErrNoRows) {
				return err
			}
			var count int
			if err = tx.QueryRow(ctx, `SELECT count(*) FROM group_expert_artifacts WHERE call_id=$1`, c.ID).Scan(&count); err != nil {
				return err
			}
			if count >= 4 {
				return httpx.ErrTooLarge
			}
			asset = idgen.New("asm")
			sum, _ := hex.DecodeString(v.File)
			if _, err = tx.Exec(ctx, `INSERT INTO media_assets(id,owner_id,purpose,state,mime,byte_size,sha256,expected_sha256,expected_size,delete_after,filename) VALUES($1,$2,$3,'processing',$4,$5,$6,$6,$5,now()+interval '24 hours',$7)`, asset, c.Binding, purpose, v.Mime, len(v.Data), sum, v.Filename); err != nil {
				return err
			}
			if _, err = tx.Exec(ctx, `INSERT INTO upload_operations(id,asset_id,owner_id,purpose,expected_sha256,expected_size,received_size,tmp_path,state) VALUES($1,$2,$3,$4,$5,$6,$6,$7,'processing')`, upload, asset, c.Binding, purpose, sum, len(v.Data), tmp); err != nil {
				return err
			}
			if _, err = tx.Exec(ctx, `INSERT INTO group_expert_artifacts VALUES($1,$2,$3,$4,$5)`, c.ID, v.Operation, v.File, asset, digest); err != nil {
				return err
			}
			if err = store.EnqueueJob(ctx, tx, "media.process", "media.process:"+upload, map[string]string{"upload_id": upload, "asset_id": asset}, time.Now()); err != nil {
				return err
			}
			created = true
			out = map[string]any{"asset_id": asset, "state": "processing"}
			return nil
		})
		if err != nil {
			httpx.WriteErrorOf(w, r, err)
			return
		}
		keep = created
		httpx.WriteData(w, r, 200, out)
	}))
}

func (s *Service) artifactStatus(ctx context.Context, tx pgx.Tx, r *http.Request, _ request) (any, error) {
	c, err := queryCall(ctx, tx, r.PathValue("call"), false)
	if err != nil {
		return nil, err
	}
	if _, err = contextFor(ctx, tx, c, false); err != nil {
		return nil, err
	}
	var state string
	err = tx.QueryRow(ctx, `SELECT m.state FROM media_assets m JOIN group_expert_artifacts a ON a.asset_id=m.id WHERE a.call_id=$1 AND m.id=$2`, c.ID, r.PathValue("asset")).Scan(&state)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, httpx.ErrNotFound
	}
	return map[string]any{"asset_id": r.PathValue("asset"), "state": state}, err
}

func validateArtifacts(ctx context.Context, tx pgx.Tx, c Call, assets []string) error {
	if len(assets) > 4 {
		return failure("group_expert_artifact_invalid")
	}
	seen := map[string]bool{}
	for _, asset := range assets {
		if seen[asset] {
			return failure("group_expert_artifact_invalid")
		}
		seen[asset] = true
		var state, owner string
		err := tx.QueryRow(ctx, `SELECT m.state,m.owner_id FROM group_expert_artifacts a JOIN media_assets m ON m.id=a.asset_id WHERE a.call_id=$1 AND a.asset_id=$2 FOR UPDATE OF m`, c.ID, asset).Scan(&state, &owner)
		if errors.Is(err, pgx.ErrNoRows) {
			return failure("group_expert_artifact_unavailable")
		}
		if err != nil {
			return err
		}
		if owner != c.Binding {
			return failure("group_expert_artifact_invalid")
		}
		if state == "processing" || state == "uploaded" || state == "initiated" {
			return failure("group_expert_artifact_pending")
		}
		if state != "ready" {
			return failure("group_expert_artifact_unavailable")
		}
	}
	return nil
}
func attachArtifacts(ctx context.Context, tx pgx.Tx, message string, assets []string) error {
	for i, asset := range assets {
		if _, err := tx.Exec(ctx, `INSERT INTO message_attachments(id,message_id,asset_id,position) VALUES($1,$2,$3,$4)`, idgen.New("att"), message, asset, i); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, `UPDATE media_assets SET delete_after=NULL WHERE id=$1`, asset); err != nil {
			return err
		}
	}
	return nil
}
