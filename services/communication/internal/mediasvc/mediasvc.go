// Package mediasvc implements private media: three-step uploads, image
// processing (thumbnails), authorized downloads and cleanup. Storage paths
// are always server-generated under MediaRoot; clients only ever see ids.
package mediasvc

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/color"
	_ "image/gif"
	"image/jpeg"
	"image/png"
	"io"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode"

	"github.com/jackc/pgx/v5"

	"octop/communication/internal/auth"
	"octop/communication/internal/deps"
	"octop/communication/internal/httpx"
	"octop/communication/internal/idgen"
	"octop/communication/internal/jobs"
	"octop/communication/internal/store"
)

// Service wires dependencies.
type Service struct{ D *deps.Deps }

// New constructs the service.
func New(d *deps.Deps) *Service { return &Service{D: d} }

// Register mounts media routes.
func (s *Service) Register(m *http.ServeMux) {
	a := func(h func(http.ResponseWriter, *http.Request)) http.HandlerFunc {
		return auth.Middleware(s.D, h)
	}
	m.HandleFunc("POST /api/app/v1/media/uploads", a(s.handleInit))
	m.HandleFunc("GET /api/app/v1/media/uploads/{id}", a(s.handleStatus))
	m.HandleFunc("PUT /api/app/v1/media/uploads/{id}/content", a(s.handlePut))
	m.HandleFunc("POST /api/app/v1/media/uploads/{id}/complete", a(s.handleComplete))
	m.HandleFunc("DELETE /api/app/v1/media/uploads/{id}", a(s.handleAbort))
	m.HandleFunc("GET /api/app/v1/media/{id}", a(s.handleGet))
}

var allowedMime = map[string]bool{"image/jpeg": true, "image/png": true, "image/gif": true, "image/webp": true}

const MaxFileBytes = 50 << 20

func cleanFilename(name string) string {
	name = filepath.Base(strings.ReplaceAll(name, "\\", "/"))
	name = strings.TrimSpace(strings.Map(func(r rune) rune {
		if unicode.IsControl(r) {
			return -1
		}
		return r
	}, name))
	if name == "" || name == "." || name == "/" {
		return "download"
	}
	chars := []rune(name)
	if len(chars) > 255 {
		name = string(chars[:255])
	}
	return name
}

// ---------------------------------------------------------------- initiate

func (s *Service) handleInit(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Purpose   string `json:"purpose"`
		Filename  string `json:"filename"`
		Mime      string `json:"mime"`
		SizeBytes int64  `json:"size_bytes"`
		SHA256Hex string `json:"sha256"`
	}
	if err := httpx.DecodeJSON(w, r, &req, 4<<10); err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	a, _ := auth.From(r)
	if req.Purpose != "chat_image" && req.Purpose != "chat_file" {
		httpx.WriteError(w, r, httpx.ErrInvalidRequest.WithMessage("purpose 仅支持 chat_image/chat_file"))
		return
	}
	if req.Purpose == "chat_image" && !allowedMime[req.Mime] {
		httpx.WriteError(w, r, httpx.ErrUnsupportedMediaType.WithMessage("仅支持 jpeg/png/gif/webp"))
		return
	}
	limit := s.D.Cfg.Resource.MediaMaxImageBytes
	if req.Purpose == "chat_file" {
		limit = MaxFileBytes
		req.Filename = cleanFilename(req.Filename)
		if req.Mime == "" {
			req.Mime = "application/octet-stream"
		}
		if _, _, err := mime.ParseMediaType(req.Mime); err != nil {
			httpx.WriteError(w, r, httpx.ErrInvalidRequest.WithMessage("无效的文件类型"))
			return
		}
	}
	if req.SizeBytes <= 0 || req.SizeBytes > limit {
		httpx.WriteError(w, r, httpx.ErrTooLarge.WithMessage(fmt.Sprintf("附件需大于 0 且不超过 %d 字节", limit)))
		return
	}
	raw, err := hex.DecodeString(strings.ToLower(strings.TrimSpace(req.SHA256Hex)))
	if err != nil || len(raw) != 32 {
		httpx.WriteError(w, r, httpx.ErrInvalidRequest.WithMessage("sha256 需为 64 位十六进制"))
		return
	}
	_ = os.MkdirAll(s.D.Cfg.MediaRoot, 0o750)
	if !s.hasFreeSpace() {
		httpx.WriteError(w, r, httpx.ErrStorage.WithMessage("存储空间不足"))
		return
	}
	assetID := idgen.New("asm")
	upID := idgen.New("upl")
	tmp := filepath.Join(s.D.Cfg.MediaRoot, "tmp", upID)
	if err := os.MkdirAll(filepath.Dir(tmp), 0o750); err != nil {
		httpx.WriteError(w, r, httpx.ErrStorage)
		return
	}
	err = s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		if _, err := tx.Exec(r.Context(), `INSERT INTO media_assets(id, owner_id, purpose, state, mime, expected_sha256, expected_size, delete_after, filename)
			VALUES($1,$2,$3,'initiated',$4,$5,$6, now()+interval '24 hours',$7)`,
			assetID, a.UserID, req.Purpose, req.Mime, raw, req.SizeBytes, req.Filename); err != nil {
			return httpx.ErrStorage
		}
		if _, err := tx.Exec(r.Context(), `INSERT INTO upload_operations(id, asset_id, owner_id, purpose, expected_sha256, expected_size, tmp_path)
			VALUES($1,$2,$3,$4,$5,$6,$7)`, upID, assetID, a.UserID, req.Purpose, raw, req.SizeBytes, tmp); err != nil {
			return httpx.ErrStorage
		}
		return nil
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	httpx.WriteData(w, r, 201, map[string]any{
		"upload_id":  upID,
		"asset_id":   assetID,
		"method":     "PUT",
		"upload_url": "/api/im/media/uploads/" + upID + "/content",
		"expires_at": time.Now().Add(24 * time.Hour).UTC().Format(time.RFC3339),
	})
}

// ------------------------------------------------------------------ put

func (s *Service) handlePut(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	upID := r.PathValue("id")
	var assetID, tmpPath, state string
	var expectedSize int64
	var expectedSHA []byte
	err := s.D.Store.Pool.QueryRow(r.Context(), `SELECT asset_id, tmp_path, state, expected_size, expected_sha256
		FROM upload_operations WHERE id=$1 AND owner_id=$2`, upID, a.UserID).
		Scan(&assetID, &tmpPath, &state, &expectedSize, &expectedSHA)
	if errors.Is(err, pgx.ErrNoRows) {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	if state != "created" && state != "writing" {
		httpx.WriteError(w, r, httpx.ErrInvalidState.WithMessage("上传已结束"))
		return
	}
	_ = os.MkdirAll(filepath.Dir(tmpPath), 0o750)
	f, err := os.OpenFile(tmpPath+".part", os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o640)
	if err != nil {
		httpx.WriteError(w, r, httpx.ErrStorage)
		return
	}
	h := sha256.New()
	lim := io.LimitReader(r.Body, expectedSize+1)
	n, err := io.Copy(io.MultiWriter(f, h), lim)
	closeErr := f.Close()
	if err != nil || closeErr != nil {
		_ = os.Remove(tmpPath + ".part")
		httpx.WriteError(w, r, httpx.ErrStorage.WithMessage("写入失败"))
		return
	}
	if n > expectedSize {
		_ = os.Remove(tmpPath + ".part")
		httpx.WriteError(w, r, httpx.ErrTooLarge.WithMessage("超过声明大小"))
		return
	}
	if n != expectedSize || hex.EncodeToString(h.Sum(nil)) != hex.EncodeToString(expectedSHA) {
		_ = os.Remove(tmpPath + ".part")
		httpx.WriteError(w, r, httpx.ErrInvalidRequest.WithMessage("sha256 不匹配"))
		return
	}
	if err := os.Rename(tmpPath+".part", tmpPath); err != nil {
		httpx.WriteError(w, r, httpx.ErrStorage)
		return
	}
	err = s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		if _, err := tx.Exec(r.Context(), `UPDATE upload_operations SET state='uploaded', received_size=$2, updated_at=now() WHERE id=$1`, upID, n); err != nil {
			return err
		}
		_, err := tx.Exec(r.Context(), `UPDATE media_assets SET state='uploaded', byte_size=$2, sha256=$3, updated_at=now() WHERE id=$1 AND state IN ('initiated','uploaded')`,
			assetID, n, expectedSHA)
		return err
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	httpx.WriteData(w, r, 200, map[string]any{"upload_id": upID, "state": "uploaded", "received_bytes": n})
}

// -------------------------------------------------------------- complete

func (s *Service) handleComplete(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	upID := r.PathValue("id")
	var assetID, tmpPath, state string
	err := s.D.Store.Pool.QueryRow(r.Context(), `SELECT asset_id, tmp_path, state FROM upload_operations WHERE id=$1 AND owner_id=$2`, upID, a.UserID).
		Scan(&assetID, &tmpPath, &state)
	if errors.Is(err, pgx.ErrNoRows) {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	if state != "uploaded" {
		httpx.WriteError(w, r, httpx.ErrInvalidState.WithMessage("需先完成内容上传"))
		return
	}
	if _, err := os.Stat(tmpPath); err != nil {
		httpx.WriteError(w, r, httpx.ErrStorage.WithMessage("暂存文件丢失，请重新上传"))
		return
	}
	err = s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		if _, err := tx.Exec(r.Context(), `UPDATE upload_operations SET state='processing', updated_at=now() WHERE id=$1`, upID); err != nil {
			return err
		}
		if _, err := tx.Exec(r.Context(), `UPDATE media_assets SET state='processing', updated_at=now() WHERE id=$1`, assetID); err != nil {
			return err
		}
		return store.EnqueueJob(r.Context(), tx, "media.process", "media.process:"+upID,
			map[string]any{"upload_id": upID, "asset_id": assetID}, time.Time{})
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	httpx.WriteData(w, r, 202, map[string]any{"asset_id": assetID, "state": "processing"})
}

func (s *Service) handleAbort(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	upID := r.PathValue("id")
	var assetID, tmpPath, state string
	err := s.D.Store.Pool.QueryRow(r.Context(), `SELECT asset_id, tmp_path, state FROM upload_operations WHERE id=$1 AND owner_id=$2`, upID, a.UserID).
		Scan(&assetID, &tmpPath, &state)
	if errors.Is(err, pgx.ErrNoRows) {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	if state == "ready" {
		httpx.WriteError(w, r, httpx.ErrInvalidState.WithMessage("资源已就绪，无法中止"))
		return
	}
	err = s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		if _, err := tx.Exec(r.Context(), `UPDATE upload_operations SET state='aborted', completed_at=now(), updated_at=now() WHERE id=$1`, upID); err != nil {
			return err
		}
		_, err := tx.Exec(r.Context(), `UPDATE media_assets SET state='deleted', updated_at=now(), delete_after=now() WHERE id=$1 AND state <> 'ready'`, assetID)
		return err
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	_ = os.Remove(tmpPath)
	_ = os.Remove(tmpPath + ".part")
	httpx.WriteData(w, r, 200, map[string]any{"aborted": true})
}

// -------------------------------------------------------------- downloads

// canRead reports whether user may read the asset. Message references grant
// access only through the unified message-visibility predicate: the message
// must not be recalled, must sit within the viewer's group member epoch, and
// must be past the viewer's own cleared_through_seq boundary. Any single
// still-visible live reference (e.g. a surviving forwarded copy) grants
// access; once every reference is revoked the download must 404 — recall or
// a local history clear revokes both originals and thumbnails.
func (s *Service) canRead(ctx context.Context, assetID, user string) (bool, string, error) {
	var owner, purpose string
	err := s.D.Store.Pool.QueryRow(ctx, `SELECT owner_id, purpose FROM media_assets WHERE id=$1`, assetID).Scan(&owner, &purpose)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, "", nil
	}
	if err != nil {
		return false, "", httpx.ErrStorage
	}
	if owner == user {
		var attached bool
		if err := s.D.Store.Pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM message_attachments WHERE asset_id=$1)`, assetID).Scan(&attached); err != nil {
			return false, "", httpx.ErrStorage
		}
		if !attached {
			return true, purpose, nil
		}
	}

	var ok bool
	err = s.D.Store.Pool.QueryRow(ctx, `SELECT EXISTS(
		SELECT 1 FROM message_attachments ma
		JOIN messages m ON m.id=ma.message_id
		JOIN conversations c ON c.id=m.conversation_id
		LEFT JOIN conversation_user_state cus ON cus.conversation_id=c.id AND cus.user_id=$2
		WHERE ma.asset_id=$1 AND m.recalled_at IS NULL
		  AND m.seq > COALESCE(cus.cleared_through_seq, 0)
		  AND (
			EXISTS(SELECT 1 FROM direct_pairs dp WHERE dp.conversation_id=c.id AND (dp.user_low=$2 OR dp.user_high=$2))
			OR EXISTS(SELECT 1 FROM member_epochs me WHERE me.conversation_id=c.id AND me.user_id=$2
				AND m.seq BETWEEN me.visible_from_seq AND COALESCE(me.visible_through_seq, 9223372036854775807))
		))`, assetID, user).Scan(&ok)
	if err != nil {
		return false, "", httpx.ErrStorage
	}
	return ok, purpose, nil
}

func (s *Service) handleGet(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	assetID := r.PathValue("id")
	kind := r.URL.Query().Get("kind")
	if kind == "" {
		kind = "original"
	}
	ok, _, err := s.canRead(r.Context(), assetID, a.UserID)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if !ok {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	var relpath, contentType, purpose, filename string
	var state string
	err = s.D.Store.Pool.QueryRow(r.Context(), `SELECT v.relpath, v.mime, ma.state, ma.purpose, ma.filename FROM media_variants v
		JOIN media_assets ma ON ma.id=v.asset_id WHERE v.asset_id=$1 AND v.kind=$2`, assetID, kind).Scan(&relpath, &contentType, &state, &purpose, &filename)
	if errors.Is(err, pgx.ErrNoRows) {
		httpx.WriteError(w, r, httpx.ErrNotFound.WithMessage("变体不存在"))
		return
	}
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	if state != "ready" {
		httpx.WriteError(w, r, httpx.ErrInvalidState.WithMessage("资源尚未就绪"))
		return
	}
	f, err := os.Open(filepath.Join(s.D.Cfg.MediaRoot, relpath))
	if err != nil {
		httpx.WriteError(w, r, httpx.ErrStorage.WithMessage("文件缺失"))
		return
	}
	defer f.Close()
	if purpose == "chat_file" {
		contentType = "application/octet-stream"
		w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": filename}))
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if info, err := f.Stat(); err == nil {
		w.Header().Set("Content-Length", strconv.FormatInt(info.Size(), 10))
	}
	w.Header().Set("Cache-Control", "private, no-store")
	w.WriteHeader(200)
	_, _ = io.Copy(w, f)
}

// ---------------------------------------------------------------- workers

// NewProcessJob decodes/validates images, stores originals and thumbnails.
func NewProcessJob(d *deps.Deps) jobs.Handler {
	return func(ctx context.Context, payload json.RawMessage) error {
		var p struct {
			UploadID string `json:"upload_id"`
			AssetID  string `json:"asset_id"`
		}
		if err := json.Unmarshal(payload, &p); err != nil || p.AssetID == "" {
			return jobs.ErrPermanent
		}
		var tmpPath, mime, purpose string
		var owner string
		var uploadState, assetState string
		var expected []byte
		err := d.Store.Pool.QueryRow(ctx, `SELECT o.tmp_path, o.expected_sha256, ma.owner_id, ma.mime, ma.purpose, o.state, ma.state
			FROM upload_operations o JOIN media_assets ma ON ma.id=o.asset_id WHERE ma.id=$1`, p.AssetID).
			Scan(&tmpPath, &expected, &owner, &mime, &purpose, &uploadState, &assetState)
		if errors.Is(err, pgx.ErrNoRows) {
			return jobs.ErrPermanent
		}
		if err != nil {
			return err
		}
		// A crash/lease takeover can replay a committed result after its temp
		// file was removed. The durable ready state is the acknowledgement.
		if uploadState == "ready" && assetState == "ready" {
			return nil
		}
		data, err := os.ReadFile(tmpPath)
		if err != nil {
			return fmt.Errorf("read tmp: %w", err)
		}
		sum := sha256.Sum256(data)
		if hex.EncodeToString(sum[:]) != hex.EncodeToString(expected) {
			return failAsset(ctx, d, p.AssetID, p.UploadID, "sha mismatch at process time")
		}
		if purpose == "chat_file" {
			rel := filepath.Join("assets", owner, p.AssetID+".orig")
			if err := os.MkdirAll(filepath.Dir(filepath.Join(d.Cfg.MediaRoot, rel)), 0o750); err != nil {
				return err
			}
			if err := os.WriteFile(filepath.Join(d.Cfg.MediaRoot, rel), data, 0o640); err != nil {
				return err
			}
			err = d.Store.Tx(ctx, func(tx pgx.Tx) error {
				if _, err := tx.Exec(ctx, `INSERT INTO media_variants(asset_id,kind,relpath,mime,byte_size) VALUES($1,'original',$2,$3,$4) ON CONFLICT (asset_id,kind) DO UPDATE SET relpath=EXCLUDED.relpath`, p.AssetID, rel, mime, len(data)); err != nil {
					return err
				}
				if _, err := tx.Exec(ctx, `UPDATE media_assets SET state='ready',ready_at=now(),updated_at=now(),relpath=$2,delete_after=now()+interval '7 days' WHERE id=$1`, p.AssetID, rel); err != nil {
					return err
				}
				_, err := tx.Exec(ctx, `UPDATE upload_operations SET state='ready',completed_at=now(),updated_at=now() WHERE id=$1`, p.UploadID)
				return err
			})
			if err == nil {
				_ = os.Remove(tmpPath)
			}
			return err
		}
		cfg, _, err := decodeImageMeta(d, data, mime)
		if err != nil {
			return failAsset(ctx, d, p.AssetID, p.UploadID, "image decode failed: "+err.Error())
		}
		// Normalise jpeg/png before persisting: apply EXIF orientation and
		// strip metadata (re-encode); the stored pixels equal shown pixels.
		if mime == "image/jpeg" || mime == "image/png" {
			if nd, nerr := normalizeImage(data, mime); nerr == nil {
				if ncfg, _, derr := decodeImageMeta(d, nd, mime); derr == nil {
					data, cfg = nd, ncfg
				}
			}
		}
		dir := filepath.Join(d.Cfg.MediaRoot, "assets", owner)
		if err := os.MkdirAll(dir, 0o750); err != nil {
			return err
		}
		origRel := filepath.Join("assets", owner, p.AssetID+".orig")
		if err := os.WriteFile(filepath.Join(d.Cfg.MediaRoot, origRel), data, 0o640); err != nil {
			return err
		}
		vars := []struct {
			kind, rel, mime string
			w, h            int
			size            int64
		}{
			{kind: "original", rel: origRel, mime: mime, w: cfg.Width, h: cfg.Height, size: int64(len(data))},
		}
		// thumbnail: re-encode jpeg ≤256px for jpeg/png; others reuse original
		thumbRel, thumbMime, tw, th, thumbBytes := origRel, mime, cfg.Width, cfg.Height, int64(len(data))
		if mime == "image/jpeg" || mime == "image/png" {
			img, err := decodeFull(data, mime)
			if err == nil {
				tj, w, h, err := makeThumbnail(img, 256)
				if err == nil {
					thumbRel = filepath.Join("assets", owner, p.AssetID+".thumb.jpg")
					if err := os.WriteFile(filepath.Join(d.Cfg.MediaRoot, thumbRel), tj, 0o640); err != nil {
						return err
					}
					thumbMime, tw, th, thumbBytes = "image/jpeg", w, h, int64(len(tj))
				}
			}
		}
		vars = append(vars, struct {
			kind, rel, mime string
			w, h            int
			size            int64
		}{kind: "thumbnail", rel: thumbRel, mime: thumbMime, w: tw, h: th, size: thumbBytes})
		if mime == "image/jpeg" || mime == "image/png" {
			vars = append(vars, vars[len(vars)-1]) // avatar_public shares thumbnail
			vars[len(vars)-1].kind = "avatar_public"
		}
		err = d.Store.Tx(ctx, func(tx pgx.Tx) error {
			for _, v := range vars {
				if _, err := tx.Exec(ctx, `INSERT INTO media_variants(asset_id, kind, relpath, mime, width, height, byte_size)
					VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (asset_id, kind) DO UPDATE SET relpath=EXCLUDED.relpath`,
					p.AssetID, v.kind, v.rel, v.mime, v.w, v.h, v.size); err != nil {
					return err
				}
			}
			if _, err := tx.Exec(ctx, `UPDATE media_assets SET state='ready', ready_at=now(), updated_at=now(),
				width=$2, height=$3, relpath=$4, delete_after=now()+interval '7 days' WHERE id=$1`,
				p.AssetID, cfg.Width, cfg.Height, origRel); err != nil {
				return err
			}
			if p.UploadID != "" {
				if _, err := tx.Exec(ctx, `UPDATE upload_operations SET state='ready', completed_at=now(), updated_at=now() WHERE id=$1`, p.UploadID); err != nil {
					return err
				}
			}
			return nil
		})
		if err != nil {
			return err
		}
		_ = os.Remove(tmpPath)
		return nil
	}
}

func failAsset(ctx context.Context, d *deps.Deps, assetID, uploadID, reason string) error {
	_, _ = d.Store.Pool.Exec(ctx, `UPDATE media_assets SET state='failed', updated_at=now(), delete_after=now()+interval '1 hour' WHERE id=$1`, assetID)
	if uploadID != "" {
		_, _ = d.Store.Pool.Exec(ctx, `UPDATE upload_operations SET state='failed', last_error=$2, updated_at=now() WHERE id=$1`, uploadID, reason)
	}
	return jobs.ErrPermanent
}

// NewCleanupJob reclaims expired/unreferenced media files and rows.
func NewCleanupJob(d *deps.Deps) jobs.Handler {
	return func(ctx context.Context, payload json.RawMessage) error {
		// expired initiated uploads
		rows, err := d.Store.Pool.Query(ctx, `SELECT id, tmp_path FROM upload_operations
			WHERE state IN ('created','writing','uploaded') AND created_at < now() - interval '24 hours'`)
		if err != nil {
			return err
		}
		type up struct{ id, path string }
		var ups []up
		for rows.Next() {
			var u up
			if err := rows.Scan(&u.id, &u.path); err != nil {
				rows.Close()
				return err
			}
			ups = append(ups, u)
		}
		rows.Close()
		for _, u := range ups {
			_, _ = d.Store.Pool.Exec(ctx, `UPDATE upload_operations SET state='aborted', completed_at=now(), updated_at=now() WHERE id=$1`, u.id)
			_, _ = d.Store.Pool.Exec(ctx, `UPDATE media_assets SET state='deleted', updated_at=now(), delete_after=now()
				WHERE id=(SELECT asset_id FROM upload_operations WHERE id=$1) AND state <> 'ready'`, u.id)
			_ = os.Remove(u.path)
			_ = os.Remove(u.path + ".part")
		}
		// assets past delete_after with no attachment references
		ARows, err := d.Store.Pool.Query(ctx, `SELECT ma.id FROM media_assets ma
			WHERE ma.state IN ('initiated','uploaded','failed','ready') AND ma.delete_after IS NOT NULL AND ma.delete_after < now()
			  AND NOT EXISTS(SELECT 1 FROM message_attachments x WHERE x.asset_id=ma.id)
			  AND NOT EXISTS(SELECT 1 FROM users u WHERE u.avatar_asset_id=ma.id)
			LIMIT 200`)
		if err != nil {
			return err
		}
		var ids []string
		for ARows.Next() {
			var id string
			if err := ARows.Scan(&id); err != nil {
				ARows.Close()
				return err
			}
			ids = append(ids, id)
		}
		ARows.Close()
		for _, id := range ids {
			var relpaths []string
			r2, err := d.Store.Pool.Query(ctx, `SELECT relpath FROM media_variants WHERE asset_id=$1`, id)
			if err == nil {
				for r2.Next() {
					var p string
					if err := r2.Scan(&p); err == nil {
						relpaths = append(relpaths, p)
					}
				}
				r2.Close()
			}
			if _, err := d.Store.Pool.Exec(ctx, `UPDATE media_assets SET state='deleting', updated_at=now() WHERE id=$1`, id); err != nil {
				return err
			}
			for _, p := range relpaths {
				if !strings.HasPrefix(p, "assets/") {
					continue
				}
				_ = os.Remove(filepath.Join(d.Cfg.MediaRoot, p))
			}
			if _, err := d.Store.Pool.Exec(ctx, `UPDATE media_assets SET state='deleted', updated_at=now() WHERE id=$1`, id); err != nil {
				return err
			}
		}
		return nil
	}
}

// ---------------------------------------------------------------- images

type imgMeta struct {
	Width, Height int
}

func decodeImageMeta(d *deps.Deps, data []byte, mime string) (imgMeta, image.Image, error) {
	switch mime {
	case "image/png":
		cfg, err := png.DecodeConfig(bytes.NewReader(data))
		if err != nil {
			return imgMeta{}, nil, err
		}
		if int64(cfg.Width)*int64(cfg.Height) > int64(d.Cfg.Resource.MediaMaxPixels) {
			return imgMeta{}, nil, errors.New("pixel budget exceeded")
		}
		return imgMeta{cfg.Width, cfg.Height}, nil, nil
	case "image/jpeg":
		cfg, err := jpeg.DecodeConfig(bytes.NewReader(data))
		if err != nil {
			return imgMeta{}, nil, err
		}
		if int64(cfg.Width)*int64(cfg.Height) > int64(d.Cfg.Resource.MediaMaxPixels) {
			return imgMeta{}, nil, errors.New("pixel budget exceeded")
		}
		return imgMeta{cfg.Width, cfg.Height}, nil, nil
	default: // gif/webp: config-only via generic registry (gif registered by import)
		cfg, _, err := image.DecodeConfig(bytes.NewReader(data))
		if err != nil {
			// webp unsupported by stdlib: accept headerless (unknown dims) only for webp magic
			if mime == "image/webp" && len(data) > 16 && string(data[0:4]) == "RIFF" && string(data[8:12]) == "WEBP" {
				return imgMeta{}, nil, nil
			}
			return imgMeta{}, nil, err
		}
		if int64(cfg.Width)*int64(cfg.Height) > int64(d.Cfg.Resource.MediaMaxPixels) {
			return imgMeta{}, nil, errors.New("pixel budget exceeded")
		}
		return imgMeta{cfg.Width, cfg.Height}, nil, nil
	}
}

func decodeFull(data []byte, mime string) (image.Image, error) {
	switch mime {
	case "image/png":
		return png.Decode(bytes.NewReader(data))
	case "image/jpeg":
		return jpeg.Decode(bytes.NewReader(data))
	}
	return nil, errors.New("no full decode for " + mime)
}

// makeThumbnail box-averages down to maxDim and encodes JPEG.
func makeThumbnail(img image.Image, maxDim int) ([]byte, int, int, error) {
	b := img.Bounds()
	w, h := b.Dx(), b.Dy()
	scale := float64(maxDim) / float64(w)
	if h > w {
		scale = float64(maxDim) / float64(h)
	}
	if scale >= 1 {
		scale = 0.5 // still shrink big-but-thin images a bit; cheap storage win
	}
	tw, th := int(float64(w)*scale), int(float64(h)*scale)
	if tw < 1 {
		tw = 1
	}
	if th < 1 {
		th = 1
	}
	dst := image.NewRGBA(image.Rect(0, 0, tw, th))
	for y := 0; y < th; y++ {
		for x := 0; x < tw; x++ {
			sx0 := b.Min.X + x*w/tw
			sx1 := b.Min.X + (x+1)*w/tw
			sy0 := b.Min.Y + y*h/th
			sy1 := b.Min.Y + (y+1)*h/th
			if sx1 <= sx0 {
				sx1 = sx0 + 1
			}
			if sy1 <= sy0 {
				sy1 = sy0 + 1
			}
			var r, g, bl, cnt uint32
			for sy := sy0; sy < sy1; sy++ {
				for sx := sx0; sx < sx1; sx++ {
					c := img.At(sx, sy)
					cr, cg, cb, _ := c.RGBA()
					r += cr >> 8
					g += cg >> 8
					bl += cb >> 8
					cnt++
				}
			}
			if cnt == 0 {
				cnt = 1
			}
			dst.SetRGBA(x, y, colorN(r/cnt, g/cnt, bl/cnt))
		}
	}
	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, dst, &jpeg.Options{Quality: 82}); err != nil {
		return nil, 0, 0, err
	}
	return buf.Bytes(), tw, th, nil
}

func colorN(r, g, b uint32) color.RGBA {
	return color.RGBA{R: uint8(r), G: uint8(g), B: uint8(b), A: 255}
}

func (s *Service) hasFreeSpace() bool {
	var st syscall.Statfs_t
	if err := syscall.Statfs(s.D.Cfg.MediaRoot, &st); err != nil {
		return false
	}
	return int64(st.Bavail)*int64(st.Bsize) >= s.D.Cfg.Resource.StorageMinFreeBytes
}

func (s *Service) handleStatus(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	var asset, state, lastError string
	e := s.D.Store.Pool.QueryRow(r.Context(), `SELECT asset_id,state,last_error FROM upload_operations WHERE id=$1 AND owner_id=$2`, r.PathValue("id"), a.UserID).Scan(&asset, &state, &lastError)
	if errors.Is(e, pgx.ErrNoRows) {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	if e != nil {
		httpx.WriteError(w, r, httpx.ErrStorage)
		return
	}
	httpx.WriteData(w, r, 200, map[string]string{"upload_id": r.PathValue("id"), "asset_id": asset, "state": state, "last_error": lastError})
}
