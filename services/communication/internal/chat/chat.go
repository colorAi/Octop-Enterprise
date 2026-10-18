// Package chat implements direct-message conversations: creation, message
// send with idempotent receipts, list/recall/forward and per-user state
// (read/pin/mute/hide/clear).
package chat

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	"octop/communication/internal/auth"
	"octop/communication/internal/deps"
	"octop/communication/internal/httpx"
	"octop/communication/internal/idgen"
	"octop/communication/internal/policy"
	"octop/communication/internal/store"
)

// Service wires dependencies.
type Service struct{ D *deps.Deps }

// New constructs the service.
func New(d *deps.Deps) *Service { return &Service{D: d} }

// Register mounts chat routes.
func (s *Service) Register(m *http.ServeMux) {
	a := func(h func(http.ResponseWriter, *http.Request)) http.HandlerFunc {
		return auth.Middleware(s.D, h)
	}
	m.HandleFunc("GET /api/app/v1/conversations", a(s.handleConvList))
	m.HandleFunc("POST /api/app/v1/conversations/direct", a(s.handleDirectCreate))
	m.HandleFunc("GET /api/app/v1/conversations/{id}/messages/{message_id}", a(s.handleMessage))
	m.HandleFunc("DELETE /api/app/v1/conversations/{id}", a(s.handleDelete))
	m.HandleFunc("GET /api/app/v1/conversations/{id}/messages", a(s.handleMsgList))
	m.HandleFunc("POST /api/app/v1/conversations/{id}/messages", a(s.handleSend))
	m.HandleFunc("POST /api/app/v1/conversations/{id}/read", a(s.handleRead))
	m.HandleFunc("PATCH /api/app/v1/conversations/{id}/state", a(s.handleState))
	m.HandleFunc("POST /api/app/v1/conversations/{id}/hide", a(s.handleHide))
	m.HandleFunc("POST /api/app/v1/conversations/{id}/clear", a(s.handleClear))
	m.HandleFunc("POST /api/app/v1/messages/{id}/recall", a(s.handleRecall))
	m.HandleFunc("POST /api/app/v1/messages/{id}/forward", a(s.handleForward))
}

// ---------------------------------------------------------------- helpers

// dmView returns the conversation membership for a private conversation.
func dmView(ctx context.Context, q queryable, convID string) (lo, hi string, status string, found bool, err error) {
	err = q.QueryRow(ctx, `SELECT dp.user_low, dp.user_high, c.status FROM direct_pairs dp
		JOIN conversations c ON c.id=dp.conversation_id WHERE dp.conversation_id=$1`, convID).Scan(&lo, &hi, &status)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", "", "", false, nil
	}
	if err != nil {
		return "", "", "", false, httpx.ErrStorage
	}
	return lo, hi, status, true, nil
}

type queryable interface {
	QueryRow(context.Context, string, ...any) pgx.Row
}

// convKind reports the conversation type and status.
func convKind(ctx context.Context, q queryable, convID string) (kind, status string, found bool, err error) {
	err = q.QueryRow(ctx, `SELECT type, status FROM conversations WHERE id=$1`, convID).Scan(&kind, &status)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", "", false, nil
	}
	if err != nil {
		return "", "", false, httpx.ErrStorage
	}
	return kind, status, true, nil
}

// groupRole returns the caller role in the ACTIVE epoch (empty if not active).
func groupRole(ctx context.Context, q queryable, convID, user string) (role string, muted bool, err error) {
	var mu *time.Time
	err = q.QueryRow(ctx, `SELECT role, mute_until FROM member_epochs WHERE conversation_id=$1 AND user_id=$2 AND left_at IS NULL`, convID, user).Scan(&role, &mu)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", false, nil
	}
	if err != nil {
		return "", false, httpx.ErrStorage
	}
	return role, mu != nil && mu.After(time.Now()), nil
}

// groupEverMember reports whether the user has any epoch (current or past).
func groupEverMember(ctx context.Context, q queryable, convID, user string) (bool, error) {
	var ok bool
	err := q.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM member_epochs WHERE conversation_id=$1 AND user_id=$2)`, convID, user).Scan(&ok)
	return ok, err
}

// memberCheck is the unified membership gate for conversation endpoints.
// For private conversations it also returns the pair and active flag.
func memberCheck(ctx context.Context, q queryable, convID, user string) (kind, status, lo, hi string, isMember, active bool, err error) {
	kind, status, found, err := convKind(ctx, q, convID)
	if err != nil || !found {
		return kind, status, "", "", false, false, err
	}
	switch kind {
	case "private":
		plo, phi, pstatus, pfound, perr := dmView(ctx, q, convID)
		if perr != nil {
			return kind, status, "", "", false, false, perr
		}
		if !pfound {
			return kind, status, "", "", false, false, nil
		}
		return kind, pstatus, plo, phi, (user == plo || user == phi), pstatus == "active" && (user == plo || user == phi), nil
	default:
		role, _, gerr := groupRole(ctx, q, convID, user)
		if gerr != nil {
			return kind, status, "", "", false, false, gerr
		}
		ever, eerr := groupEverMember(ctx, q, convID, user)
		if eerr != nil {
			return kind, status, "", "", false, false, eerr
		}
		return kind, status, "", "", ever, role != "" && status == "active", nil
	}
}

// membersOf lists users to notify for events on this conversation.
func membersOf(ctx context.Context, q interface {
	Query(context.Context, string, ...any) (pgx.Rows, error)
}, kind, lo, hi, convID string) ([]string, error) {
	if kind == "private" {
		return []string{lo, hi}, nil
	}
	rows, err := q.Query(ctx, `SELECT user_id FROM member_epochs WHERE conversation_id=$1 AND left_at IS NULL`, convID)
	if err != nil {
		return nil, httpx.ErrStorage
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var u string
		if err := rows.Scan(&u); err != nil {
			return nil, httpx.ErrStorage
		}
		out = append(out, u)
	}
	return out, nil
}

// Recalls also invalidate cached history for former members, but must not
// reveal message IDs outside a recipient's epochs or already cleared history.
func recallRecipients(ctx context.Context, tx pgx.Tx, kind, lo, hi, convID string, seq int64) ([]string, error) {
	if kind == "private" {
		return []string{lo, hi}, nil
	}
	rows, err := tx.Query(ctx, `SELECT DISTINCT me.user_id FROM member_epochs me
		LEFT JOIN conversation_user_state st ON st.conversation_id=me.conversation_id AND st.user_id=me.user_id
		WHERE me.conversation_id=$1
		  AND $2 BETWEEN me.visible_from_seq AND COALESCE(me.visible_through_seq, 9223372036854775807)
		  AND $2 > COALESCE(st.cleared_through_seq,0)
		ORDER BY me.user_id`, convID, seq)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var user string
		if err := rows.Scan(&user); err != nil {
			return nil, err
		}
		out = append(out, user)
	}
	return out, rows.Err()
}

func seqStr(v int64) string { return strconv.FormatInt(v, 10) }

// ---------------------------------------------------------------- create

func (s *Service) handleDirectCreate(w http.ResponseWriter, r *http.Request) {
	var req struct {
		PeerUserID string `json:"peer_user_id"`
	}
	if err := httpx.DecodeJSON(w, r, &req, 2<<10); err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	a, _ := auth.From(r)
	if req.PeerUserID == "" || req.PeerUserID == a.UserID {
		httpx.WriteError(w, r, httpx.ErrInvalidRequest.WithMessage("需要有效的 peer_user_id"))
		return
	}
	if !policy.CanCommunicate(r.Context(), s.D.Store.Pool, a.UserID, req.PeerUserID) {
		httpx.WriteError(w, r, httpx.ErrForbidden.WithMessage("仅同部门的启用账号可以发起私聊"))
		return
	}
	var out map[string]any
	err := s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		if !policy.Active(r.Context(), tx, a.UserID) {
			return httpx.ErrAccountDisabled
		}
		if err := store.LockFriendPair(r.Context(), tx, a.UserID, req.PeerUserID); err != nil {
			return err
		}
		if !policy.CanCommunicate(r.Context(), tx, a.UserID, req.PeerUserID) {
			return httpx.ErrForbidden.WithMessage("仅同部门的启用账号可以发起私聊")
		}
		lo, hi := a.UserID, req.PeerUserID
		if hi < lo {
			lo, hi = hi, lo
		}
		var convID string
		var status string
		err := tx.QueryRow(r.Context(), `SELECT conversation_id FROM direct_pairs WHERE user_low=$1 AND user_high=$2`, lo, hi).Scan(&convID)
		if err == nil {
			_ = tx.QueryRow(r.Context(), `SELECT status FROM conversations WHERE id=$1`, convID).Scan(&status)
			if status == "active" {
				out = map[string]any{"conversation_id": convID, "type": "private", "peer_user_id": req.PeerUserID, "created": false}
				return nil
			}
			// reactivate historical conversation (spec: history preserved)
			if _, err := tx.Exec(r.Context(), `UPDATE conversations SET status='active', updated_at=now() WHERE id=$1`, convID); err != nil {
				return httpx.ErrStorage
			}
			out = map[string]any{"conversation_id": convID, "type": "private", "peer_user_id": req.PeerUserID, "created": false, "reactivated": true}
			return nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return httpx.ErrStorage
		}
		convID = idgen.New("conv")
		if _, err := tx.Exec(r.Context(), `INSERT INTO conversations(id, type, created_by) VALUES($1,'private',$2)`, convID, a.UserID); err != nil {
			return httpx.ErrStorage
		}
		if _, err := tx.Exec(r.Context(), `INSERT INTO direct_pairs(conversation_id, user_low, user_high) VALUES($1,$2,$3)`, convID, lo, hi); err != nil {
			return httpx.ErrStorage
		}
		for _, u := range []string{lo, hi} {
			if _, err := tx.Exec(r.Context(), `INSERT INTO conversation_user_state(id, conversation_id, user_id)
				VALUES($1,$2,$3) ON CONFLICT (conversation_id, user_id) DO NOTHING`, idgen.New("cus"), convID, u); err != nil {
				return httpx.ErrStorage
			}
			if _, err := store.EmitSync(r.Context(), tx, u, "conversation.created", convID, 0,
				map[string]any{"conversation_id": convID, "type": "private", "peer_user_id": peerOf(u, lo, hi)}); err != nil {
				return err
			}
		}
		out = map[string]any{"conversation_id": convID, "type": "private", "peer_user_id": req.PeerUserID, "created": true}
		return nil
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	s.wake(a.UserID, req.PeerUserID)
	httpx.WriteData(w, r, 200, out)
}

func peerOf(u, lo, hi string) string {
	if u == lo {
		return hi
	}
	return lo
}

// ---------------------------------------------------------------- list

func (s *Service) handleConvList(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	includeHidden := r.URL.Query().Get("include_hidden") == "1"
	limit := httpx.PageParams(r, s.D.Cfg.Limits.ListDefault, s.D.Cfg.Limits.ListMax)
	var after string
	var afterStamp time.Time
	var afterPinned bool
	if token := r.URL.Query().Get("cursor"); token != "" {
		p, ok := s.D.Cursor.Decode(token)
		if !ok || p["u"] != a.UserID {
			httpx.WriteError(w, r, httpx.ErrInvalidRequest)
			return
		}
		after, _ = p["i"].(string)
		stamp, _ := p["t"].(string)
		var err error
		afterStamp, err = time.Parse(time.RFC3339Nano, stamp)
		afterPinned, _ = p["p"].(bool)
		if after == "" || err != nil {
			httpx.WriteError(w, r, httpx.ErrInvalidRequest)
			return
		}
	}

	hiddenFilter := ""
	if !includeHidden {
		hiddenFilter = `AND (cus.hidden_at_seq IS NULL OR c.last_seq > cus.hidden_at_seq)`
	}
	args := []any{a.UserID, limit + 1}
	afterFilter := ""
	if after != "" {
		args = append(args, afterPinned, afterStamp, after)
		afterFilter = ` WHERE (x.pinned,x.updated_at,x.id)<($3,$4,$5)`
	}

	q := `
		SELECT x.* FROM ((
			SELECT c.id AS id, c.last_seq AS last_seq, c.updated_at AS updated_at, 'private' AS kind,
			       CASE WHEN dp.user_low=$1 THEN dp.user_high ELSE dp.user_low END AS peer,
			       coalesce(cus.last_read_seq,0) AS last_read, coalesce(cus.pinned,false) AS pinned, coalesce(cus.muted,false) AS muted,
			       coalesce(cus.hidden_at_seq, -1) AS hidden_at,
			       COALESCE(lm.type,'') AS ltype, lm.text AS ltext, lm.recalled_at IS NOT NULL AS lrecalled
			FROM direct_pairs dp
			JOIN conversations c ON c.id=dp.conversation_id AND c.status='active'
			LEFT JOIN conversation_user_state cus ON cus.conversation_id=c.id AND cus.user_id=$1
			LEFT JOIN LATERAL (
				SELECT m.type, m.text, m.recalled_at FROM messages m
				WHERE m.conversation_id=c.id AND m.seq <= c.last_seq AND m.seq > coalesce(cus.cleared_through_seq,0)
				ORDER BY m.seq DESC LIMIT 1) lm ON true
			WHERE (dp.user_low=$1 OR dp.user_high=$1) ` + hiddenFilter + `
		) UNION ALL (
			SELECT c.id AS id, LEAST(c.last_seq,COALESCE(me.visible_through_seq,c.last_seq)) AS last_seq, c.updated_at AS updated_at, 'group' AS kind,
			       '' AS peer,
			       coalesce(cus.last_read_seq,0) AS last_read, coalesce(cus.pinned,false) AS pinned, coalesce(cus.muted,false) AS muted,
			       coalesce(cus.hidden_at_seq, -1) AS hidden_at,
			       COALESCE(lm.type,'') AS ltype, lm.text AS ltext, lm.recalled_at IS NOT NULL AS lrecalled
			FROM member_epochs me
			JOIN conversations c ON c.id=me.conversation_id AND c.status='active'
			LEFT JOIN conversation_user_state cus ON cus.conversation_id=c.id AND cus.user_id=$1
			LEFT JOIN LATERAL (
				SELECT m.type, m.text, m.recalled_at FROM messages m
				WHERE m.conversation_id=c.id AND m.seq <= c.last_seq AND m.seq > coalesce(cus.cleared_through_seq,0)
				  AND EXISTS(SELECT 1 FROM member_epochs me2 WHERE me2.conversation_id=c.id AND me2.user_id=$1
				      AND m.seq BETWEEN me2.visible_from_seq AND COALESCE(me2.visible_through_seq, 9223372036854775807))
				ORDER BY m.seq DESC LIMIT 1) lm ON true
			WHERE me.user_id=$1 AND me.epoch=(SELECT MAX(own.epoch) FROM member_epochs own WHERE own.conversation_id=me.conversation_id AND own.user_id=$1) ` + hiddenFilter + `
		)) x
		` + afterFilter + ` ORDER BY x.pinned DESC, x.updated_at DESC, x.id DESC LIMIT $2`
	rows, err := s.D.Store.Pool.Query(r.Context(), q, args...)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	defer rows.Close()
	items := []map[string]any{}
	cursors := []map[string]any{}
	for rows.Next() {
		var cid, kindv, peer, ltype string
		var lastSeq, lastRead int64
		var updated time.Time
		var pinned, muted bool
		var hidden int64
		var text *string
		var recalled bool
		if err := rows.Scan(&cid, &lastSeq, &updated, &kindv, &peer, &lastRead, &pinned, &muted, &hidden, &ltype, &text, &recalled); err != nil {
			httpx.WriteErrorOf(w, r, httpx.ErrStorage)
			return
		}
		var preview any
		switch {
		case ltype == "":
		case recalled:
			preview = "[已撤回]"
		case ltype == "image":
			preview = "[图片]"
		case ltype == "file":
			preview = "[文件]"
		case ltype == "system":
			preview = derefStr(text)
		default:
			preview = derefStr(text)
		}
		// unread: valid, visible, non-self, non-system, non-recalled messages
		var unread int64
		uparams := []any{cid, a.UserID, lastRead, lastSeq}
		uwhere := `conversation_id=$1 AND sender_id IS DISTINCT FROM $2 AND seq > $3 AND seq <= $4
			AND type <> 'system' AND recalled_at IS NULL
			AND seq > COALESCE((SELECT cleared_through_seq FROM conversation_user_state WHERE conversation_id=$1 AND user_id=$2),0)`
		if kindv == "group" {
			uwhere += ` AND EXISTS(SELECT 1 FROM member_epochs me3 WHERE me3.conversation_id=$5 AND me3.user_id=$2
				AND messages.seq BETWEEN me3.visible_from_seq AND COALESCE(me3.visible_through_seq, 9223372036854775807))`
			uparams = append(uparams, cid)
		}
		if err := s.D.Store.Pool.QueryRow(r.Context(), `SELECT count(*) FROM messages WHERE `+uwhere, uparams...).Scan(&unread); err != nil {
			httpx.WriteErrorOf(w, r, httpx.ErrStorage)
			return
		}
		it := map[string]any{"conversation_id": cid, "type": kindv, "last_seq": seqStr(lastSeq),
			"last_read_seq": seqStr(lastRead), "hidden_at_seq": nil,
			"unread": unread, "pinned": pinned, "muted": muted,
			"updated_at": updated.UTC().Format(time.RFC3339)}
		if hidden >= 0 {
			it["hidden_at_seq"] = seqStr(hidden)
		}
		if kindv == "private" {
			it["peer_user_id"] = peer
			it["can_send"] = policy.CanCommunicate(r.Context(), s.D.Store.Pool, a.UserID, peer) && !policy.AreBlocked(r.Context(), s.D.Store.Pool, a.UserID, peer)
		}
		if preview != nil {
			it["last_message"] = map[string]any{"type": ltype, "preview": preview}
		}
		items = append(items, it)
		cursors = append(cursors, map[string]any{"u": a.UserID, "i": cid, "t": updated.UTC().Format(time.RFC3339Nano), "p": pinned})
	}
	if err := rows.Err(); err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	more := len(items) > limit
	if more {
		items = items[:limit]
	}
	out := map[string]any{"items": items, "has_more": more}
	if more {
		out["next_cursor"] = s.D.Cursor.Encode(cursors[limit-1])
	}
	httpx.WriteData(w, r, 200, out)
}

func derefStr(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

// ---------------------------------------------------------------- send

type sendReq struct {
	ClientMessageID string   `json:"client_message_id"`
	Text            *string  `json:"text"`
	AssetIDs        []string `json:"asset_ids"`
	ReplyTo         string   `json:"reply_to_message_id"`
	MentionUserIDs  []string `json:"mention_user_ids"`
	MentionAll      bool     `json:"mention_all"`
	forwardSourceID string
}

func (r *sendReq) digest(convID string) string {
	type canon struct {
		C    string   `json:"c"`
		T    string   `json:"t,omitempty"`
		A    []string `json:"a,omitempty"`
		R    string   `json:"r,omitempty"`
		CMID string   `json:"m,omitempty"`
		M    []string `json:"M,omitempty"`
		MA   bool     `json:"MA,omitempty"`
	}
	c := canon{C: convID, CMID: r.ClientMessageID, R: r.ReplyTo, MA: r.MentionAll}
	if r.Text != nil {
		c.T = *r.Text
	}
	if len(r.AssetIDs) > 0 {
		c.A = append([]string{}, r.AssetIDs...)
	}
	if len(r.MentionUserIDs) > 0 {
		c.M = append([]string{}, r.MentionUserIDs...)
		sort.Strings(c.M)
	}
	b, _ := json.Marshal(c)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func (s *Service) handleSend(w http.ResponseWriter, r *http.Request) {
	var req sendReq
	if err := httpx.DecodeJSON(w, r, &req, 64<<10); err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	a, _ := auth.From(r)
	convID := r.PathValue("id")
	if req.ClientMessageID == "" || len(req.ClientMessageID) > 128 {
		httpx.WriteError(w, r, httpx.ErrInvalidRequest.WithMessage("client_message_id 需为 1–128 字节"))
		return
	}
	if len(req.MentionUserIDs) > 50 {
		httpx.WriteError(w, r, httpx.ErrInvalidRequest.WithMessage("@ 列表最多 50 人"))
		return
	}
	digest := req.digest(convID)

	var (
		out       map[string]any
		replayed  bool
		wakeUsers []string
	)
	err := s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		if !policy.Active(r.Context(), tx, a.UserID) {
			return httpx.ErrAccountDisabled
		}
		// 1) idempotent replay BEFORE permission checks
		if req.ClientMessageID != "" {
			var msgID, msgConv string
			var seq int64
			var created time.Time
			var storedDigest []byte
			err := tx.QueryRow(r.Context(), `SELECT m.id, m.conversation_id, m.seq, m.created_at, x.content_digest
				FROM messages m JOIN message_receipts x ON x.sender_id=m.sender_id AND x.client_message_id=m.client_message_id
				WHERE m.sender_id=$1 AND m.client_message_id=$2`, a.UserID, req.ClientMessageID).
				Scan(&msgID, &msgConv, &seq, &created, &storedDigest)
			if err == nil {
				if hex.EncodeToString(storedDigest) != digest {
					return httpx.ErrIdemConflict
				}
				out = map[string]any{"message_id": msgID, "conversation_id": msgConv, "seq": seqStr(seq),
					"created_at": created.UTC().Format(time.RFC3339), "replayed": true}
				replayed = true
				return nil
			}
			if !errors.Is(err, pgx.ErrNoRows) {
				return httpx.ErrStorage
			}
		}
		// 2) conversation access + send permission
		kind, status, lo, hi, isMember, active, err := memberCheck(r.Context(), tx, convID, a.UserID)
		if err != nil {
			return err
		}
		if !found(kind) {
			return httpx.ErrNotFound
		}
		if !isMember {
			return httpx.ErrForbidden
		}
		var members []string
		if kind == "private" {
			if err := store.LockFriendPair(r.Context(), tx, lo, hi); err != nil {
				return err
			}
			if !active {
				return httpx.ErrInvalidState.WithMessage("会话不可用")
			}
			if !policy.CanCommunicate(r.Context(), tx, lo, hi) || policy.AreBlocked(r.Context(), tx, lo, hi) {
				return httpx.ErrSendNotAllowed
			}
			members = []string{lo, hi}
		} else {
			if _, err := tx.Exec(r.Context(), `SELECT last_seq FROM conversations WHERE id=$1 FOR UPDATE`, convID); err != nil {
				return httpx.ErrStorage
			}
			role, mutedNow, err := groupRole(r.Context(), tx, convID, a.UserID)
			if err != nil {
				return err
			}
			if status != "active" {
				return httpx.ErrInvalidState.WithMessage("群已解散")
			}
			if role == "" {
				return httpx.ErrSendNotAllowed.WithMessage("需要当前群成员身份")
			}
			var muteAll bool
			if err := tx.QueryRow(r.Context(), `SELECT mute_all FROM group_settings WHERE conversation_id=$1`, convID).Scan(&muteAll); err != nil {
				return httpx.ErrStorage
			}
			if (role == "member" && muteAll) || (role != "owner" && mutedNow) {
				return httpx.ErrSendNotAllowed.WithMessage("禁言中")
			}
			if req.MentionAll && role == "member" && !reqIsOwnerLike(role) {
				return httpx.ErrForbidden.WithMessage("仅群主/管理员可@全体")
			}
			members, err = membersOf(r.Context(), tx, kind, lo, hi, convID)
			if err != nil {
				return err
			}
			for _, m := range req.MentionUserIDs {
				ok := false
				for _, u := range members {
					if u == m {
						ok = true
						break
					}
				}
				if !ok {
					return httpx.ErrInvalidRequest.WithMessage("@ 目标需为当前群成员")
				}
			}
		}
		// 3) validate payload
		text := ""
		if req.Text != nil {
			text = *req.Text
		}
		assets := req.AssetIDs
		for i := range assets {
			assets[i] = strings.TrimSpace(assets[i])
		}
		if len(assets) > s.D.Cfg.Limits.MaxImagesPerMessage {
			return httpx.ErrInvalidRequest.WithMessage("单条消息附件数量超出上限")
		}
		mType := "text"
		if len(assets) > 0 {
			mType = "image"
			if len([]rune(text)) > s.D.Cfg.Limits.MaxImageCaption {
				return httpx.ErrInvalidRequest.WithMessage("附件说明文字过长")
			}
		} else if len([]rune(text)) == 0 || len([]rune(text)) > s.D.Cfg.Limits.MaxTextChars {
			return httpx.ErrInvalidRequest.WithMessage("文本为空或超过长度上限")
		}
		if len(assets) > 0 {
			if _, err := store.LockAssets(r.Context(), tx, assets); err != nil {
				return err
			}
			var n int
			if err := tx.QueryRow(r.Context(), `SELECT count(*) FROM media_assets WHERE id = ANY($1) AND owner_id=$2 AND state='ready'`,
				pq(assets), a.UserID).Scan(&n); err != nil || n != len(assets) {
				return httpx.ErrNotFound.WithMessage("存在无效或未就绪的附件资源")
			}
			mType, err = attachmentType(r.Context(), tx, assets)
			if err != nil {
				return err
			}
		}
		// reply must be visible to sender in the same conversation
		if req.ReplyTo != "" {
			var rc string
			var rseq int64
			err := tx.QueryRow(r.Context(), `SELECT conversation_id, seq FROM messages WHERE id=$1`, req.ReplyTo).Scan(&rc, &rseq)
			if errors.Is(err, pgx.ErrNoRows) {
				return httpx.ErrNotFound.WithMessage("引用消息不存在")
			}
			if err != nil {
				return httpx.ErrStorage
			}
			if rc != convID || !s.visibleTo(r.Context(), tx, convID, a.UserID, kind, rseq) {
				return httpx.ErrForbidden.WithMessage("无权引用该消息")
			}
		}
		// 4) append message
		if _, err := tx.Exec(r.Context(), `SELECT last_seq FROM conversations WHERE id=$1 FOR UPDATE`, convID); err != nil {
			return httpx.ErrStorage
		}
		seq, err := store.NextSeq(r.Context(), tx, convID)
		if err != nil {
			return err
		}
		msgID := idgen.New("msg")
		var textArg any
		if text != "" {
			textArg = text
		}
		var replyArg any
		if req.ReplyTo != "" {
			replyArg = req.ReplyTo
		}
		mentions := req.MentionUserIDs
		if mentions == nil {
			mentions = []string{}
		}
		if _, err := tx.Exec(r.Context(), `INSERT INTO messages(id, conversation_id, seq, sender_id, client_message_id, type, text, reply_to_message_id, mentions, mention_all)
			VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`, msgID, convID, seq, a.UserID, req.ClientMessageID, mType, textArg, replyArg, mentions, req.MentionAll); err != nil {
			if isUnique(err, "messages_sender_idem_idx") {
				return httpx.ErrIdemConflict
			}
			return httpx.ErrStorage
		}
		for i, as := range assets {
			if _, err := tx.Exec(r.Context(), `INSERT INTO message_attachments(id, message_id, asset_id, position) VALUES($1,$2,$3,$4)`,
				idgen.New("att"), msgID, as, i); err != nil {
				return httpx.ErrStorage
			}
			if _, err := tx.Exec(r.Context(), `UPDATE media_assets SET delete_after=NULL WHERE id=$1`, as); err != nil {
				return httpx.ErrStorage
			}
		}
		result := map[string]any{"message_id": msgID, "conversation_id": convID, "seq": seqStr(seq),
			"created_at": time.Now().UTC().Format(time.RFC3339)}
		rawRes, _ := json.Marshal(result)
		if req.ClientMessageID != "" {
			if _, err := tx.Exec(r.Context(), `INSERT INTO message_receipts(id, sender_id, client_message_id, content_digest, result)
				VALUES($1,$2,$3,$4,$5) ON CONFLICT (sender_id, client_message_id) DO NOTHING`,
				idgen.New("rcp"), a.UserID, req.ClientMessageID, digestBytes(digest), rawRes); err != nil {
				return httpx.ErrStorage
			}
		}
		for _, u := range members {
			if u == a.UserID {
				if _, err := tx.Exec(r.Context(), `UPDATE conversation_user_state SET last_read_seq=$3, hidden_at_seq=NULL, updated_at=now()
					WHERE conversation_id=$1 AND user_id=$2`, convID, u, seq); err != nil {
					return httpx.ErrStorage
				}
			}
			if _, err := store.EmitSync(r.Context(), tx, u, "message.new", msgID, seq,
				map[string]any{"conversation_id": convID, "message_id": msgID, "seq": seqStr(seq)}); err != nil {
				return err
			}

		}
		if _, err := tx.Exec(r.Context(), `UPDATE conversations SET last_seq=$2, updated_at=now() WHERE id=$1`, convID, seq); err != nil {
			return httpx.ErrStorage
		}
		out = result
		wakeUsers = members
		return nil
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if !replayed {
		s.wake(wakeUsers...)
		httpx.WriteData(w, r, 201, out)
		return
	}
	out["replayed"] = true
	httpx.WriteData(w, r, 200, out)
}

func reqIsOwnerLike(role string) bool { return role == "owner" }

// found reports whether kind is non-empty (conversation existed).
func found(kind string) bool { return kind != "" }

// visibleTo checks the shared history-visibility rule for a single seq.
func (s *Service) visibleTo(ctx context.Context, tx pgx.Tx, convID, user, kind string, seq int64) bool {
	var ok bool
	q := interface {
		QueryRow(context.Context, string, ...any) pgx.Row
	}(s.D.Store.Pool)
	if tx != nil {
		q = tx
	}
	_ = q.QueryRow(ctx, `SELECT $3 > COALESCE((SELECT cleared_through_seq FROM conversation_user_state WHERE conversation_id=$1 AND user_id=$2),0)
		AND ($4='private' OR EXISTS(SELECT 1 FROM member_epochs me
		WHERE me.conversation_id=$1 AND me.user_id=$2
		  AND $3 BETWEEN me.visible_from_seq AND COALESCE(me.visible_through_seq, 9223372036854775807)))`,
		convID, user, seq, kind).Scan(&ok)
	return ok
}

// ---------------------------------------------------------------- history

func (s *Service) handleMsgList(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	convID := r.PathValue("id")
	kind, _, _, _, isMember, _, err := memberCheck(r.Context(), s.D.Store.Pool, convID, a.UserID)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if !isMember {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	limit := httpx.PageParams(r, 30, 100)
	before := int64(-1)
	if v := r.URL.Query().Get("before_seq"); v != "" {
		n, err := strconv.ParseInt(v, 10, 64)
		if err != nil || n < 0 {
			httpx.WriteError(w, r, httpx.ErrInvalidRequest.WithMessage("before_seq 需为非负整数字符串"))
			return
		}
		before = n
	}
	var cleared int64
	_ = s.D.Store.Pool.QueryRow(r.Context(), `SELECT cleared_through_seq FROM conversation_user_state WHERE conversation_id=$1 AND user_id=$2`, convID, a.UserID).Scan(&cleared)
	where := `m.conversation_id=$1 AND m.seq < $2 AND m.seq > $3`
	hargs := []any{convID, mapBefore(before), cleared, limit + 1}
	if kind != "private" {
		where += ` AND EXISTS(SELECT 1 FROM member_epochs me WHERE me.conversation_id=m.conversation_id AND me.user_id=$5
			AND m.seq BETWEEN me.visible_from_seq AND COALESCE(me.visible_through_seq, 9223372036854775807))`
		hargs = append(hargs, a.UserID)
	}
	rows, err := s.D.Store.Pool.Query(r.Context(), `SELECT m.id, m.seq, m.sender_id, m.type, m.text, m.recalled_at IS NOT NULL, m.created_at,m.client_message_id,m.reply_to_message_id,m.mentions,m.mention_all,m.system_kind
		FROM messages m WHERE `+where+`
		ORDER BY m.seq DESC LIMIT $4`, hargs...)
	if err != nil {
		httpx.WriteError(w, r, httpx.ErrStorage)
		return
	}
	defer rows.Close()
	var items = []messageItem{}
	hasMore := false
	for rows.Next() {
		var it messageItem
		var sender *string
		var created time.Time
		if err := rows.Scan(&it.ID, newSeq(&it.Seq), &sender, &it.Type, &it.Text, &it.Recalled, &created, &it.ClientMessageID, &it.ReplyToMessageID, &it.MentionUserIDs, &it.MentionAll, &it.SystemKind); err != nil {
			httpx.WriteErrorOf(w, r, httpx.ErrStorage)
			return
		}
		if len(items) == limit {
			hasMore = true
			break
		}
		if sender != nil {
			it.SenderID = *sender
		}
		it.CreatedAt = created.UTC().Format(time.RFC3339)
		items = append(items, it)
	}
	if err = rows.Err(); err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	rows.Close()
	for i := range items {
		if err = s.attachMessageAssets(r.Context(), &items[i]); err != nil {
			httpx.WriteErrorOf(w, r, err)
			return
		}
		if items[i].ReplyToMessageID != nil && !items[i].Recalled {
			items[i].ReplyPreview, err = s.visibleMessage(r.Context(), convID, *items[i].ReplyToMessageID, a.UserID, kind)
			if err != nil {
				httpx.WriteErrorOf(w, r, err)
				return
			}
		}
	}
	var oldest int64
	if len(items) > 0 {
		oldest, _ = strconv.ParseInt(items[len(items)-1].Seq, 10, 64)
	}
	httpx.WriteData(w, r, 200, map[string]any{"items": items, "has_more": hasMore, "next_before_seq": seqStr(oldest)})
}

// mapBefore turns -1 into "no upper bound" via max int64.
func mapBefore(v int64) int64 {
	if v < 0 {
		return int64(^uint64(0) >> 1)
	}
	return v
}

func newSeq(dst *string) *seqScanner { return &seqScanner{dst: dst} }

type seqScanner struct{ dst *string }

func (s *seqScanner) Scan(src any) error {
	switch v := src.(type) {
	case int64:
		*s.dst = strconv.FormatInt(v, 10)
	case []byte:
		*s.dst = string(v)
	case string:
		*s.dst = v
	default:
		return errors.New("bad seq type")
	}
	return nil
}

// ---------------------------------------------------------------- mutate

func (s *Service) handleRead(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	convID := r.PathValue("id")
	_, _, _, _, isMember, _, err := memberCheck(r.Context(), s.D.Store.Pool, convID, a.UserID)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if !isMember {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	var req struct {
		LastReadSeq string `json:"last_read_seq"`
	}
	if err := httpx.DecodeJSON(w, r, &req, 1<<10); err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	n, err := strconv.ParseInt(req.LastReadSeq, 10, 64)
	if err != nil || n < 0 {
		httpx.WriteError(w, r, httpx.ErrInvalidRequest.WithMessage("last_read_seq 需为非负整数字符串"))
		return
	}
	var actualRead int64
	err = s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		if !policy.Active(r.Context(), tx, a.UserID) {
			return httpx.ErrAccountDisabled
		}
		_, _, _, _, member, _, e := memberCheck(r.Context(), tx, convID, a.UserID)
		if e != nil {
			return e
		}
		if !member {
			return httpx.ErrForbidden
		}
		_, e = tx.Exec(r.Context(), `INSERT INTO conversation_user_state(id, conversation_id, user_id, last_read_seq)
		VALUES($1,$2,$3, LEAST($4, (SELECT CASE WHEN c.type='private' THEN c.last_seq ELSE COALESCE((SELECT MAX(COALESCE(me.visible_through_seq,c.last_seq)) FROM member_epochs me WHERE me.conversation_id=c.id AND me.user_id=$3),0) END FROM conversations c WHERE c.id=$2)))
		ON CONFLICT (conversation_id, user_id) DO UPDATE
		SET last_read_seq = GREATEST(conversation_user_state.last_read_seq, LEAST($4, (SELECT CASE WHEN c.type='private' THEN c.last_seq ELSE COALESCE((SELECT MAX(COALESCE(me.visible_through_seq,c.last_seq)) FROM member_epochs me WHERE me.conversation_id=c.id AND me.user_id=$3),0) END FROM conversations c WHERE c.id=$2))), updated_at=now()`,
			idgen.New("cus"), convID, a.UserID, n)
		if e != nil {
			return e
		}
		if e = tx.QueryRow(r.Context(), `SELECT last_read_seq FROM conversation_user_state WHERE conversation_id=$1 AND user_id=$2`, convID, a.UserID).Scan(&actualRead); e != nil {
			return e
		}
		_, e = store.EmitSync(r.Context(), tx, a.UserID, "conversation.state.changed", convID, 0, map[string]string{"conversation_id": convID, "last_read_seq": seqStr(actualRead)})
		return e
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	httpx.WriteData(w, r, 200, map[string]any{"last_read_seq": seqStr(actualRead)})
}

func (s *Service) handleState(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	convID := r.PathValue("id")
	_, _, _, _, isMember, _, err := memberCheck(r.Context(), s.D.Store.Pool, convID, a.UserID)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if !isMember {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	var req struct {
		Pinned *bool `json:"pinned"`
		Muted  *bool `json:"muted"`
	}
	if err := httpx.DecodeJSON(w, r, &req, 1<<10); err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if req.Pinned == nil && req.Muted == nil {
		httpx.WriteError(w, r, httpx.ErrInvalidRequest.WithMessage("需要 pinned 或 muted"))
		return
	}
	cols := []string{"id", "conversation_id", "user_id"}
	vals := []string{"$1", "$2", "$3"}
	sets := []string{"updated_at=now()"}
	args := []any{idgen.New("cus"), convID, a.UserID}
	if req.Pinned != nil {
		args = append(args, *req.Pinned)
		cols = append(cols, "pinned")
		vals = append(vals, "$"+strconv.Itoa(len(args)))
		sets = append(sets, "pinned=EXCLUDED.pinned")
	}
	if req.Muted != nil {
		args = append(args, *req.Muted)
		cols = append(cols, "muted")
		vals = append(vals, "$"+strconv.Itoa(len(args)))
		sets = append(sets, "muted=EXCLUDED.muted")
	}
	q := "INSERT INTO conversation_user_state(" + strings.Join(cols, ", ") + ") VALUES(" + strings.Join(vals, ", ") + ") " +
		"ON CONFLICT (conversation_id, user_id) DO UPDATE SET " + strings.Join(sets, ", ")
	err = s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		if !policy.Active(r.Context(), tx, a.UserID) {
			return httpx.ErrAccountDisabled
		}
		_, _, _, _, member, _, e := memberCheck(r.Context(), tx, convID, a.UserID)
		if e != nil {
			return e
		}
		if !member {
			return httpx.ErrForbidden
		}
		if _, e = tx.Exec(r.Context(), q, args...); e != nil {
			return e
		}
		_, e = store.EmitSync(r.Context(), tx, a.UserID, "conversation.state.changed", convID, 0, map[string]string{"conversation_id": convID})
		return e
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	httpx.WriteData(w, r, 200, map[string]any{"pinned": req.Pinned, "muted": req.Muted})
}

func (s *Service) handleHide(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	convID := r.PathValue("id")
	_, _, _, _, isMember, _, err := memberCheck(r.Context(), s.D.Store.Pool, convID, a.UserID)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if !isMember {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	err = s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		if !policy.Active(r.Context(), tx, a.UserID) {
			return httpx.ErrAccountDisabled
		}
		_, _, _, _, member, _, e := memberCheck(r.Context(), tx, convID, a.UserID)
		if e != nil {
			return e
		}
		if !member {
			return httpx.ErrForbidden
		}
		_, e = tx.Exec(r.Context(), `INSERT INTO conversation_user_state(id, conversation_id, user_id, hidden_at_seq)
		VALUES($1,$2,$3, (SELECT last_seq FROM conversations WHERE id=$2))
		ON CONFLICT (conversation_id, user_id) DO UPDATE SET hidden_at_seq=(SELECT last_seq FROM conversations WHERE id=$2), updated_at=now()`,
			idgen.New("cus"), convID, a.UserID)
		if e != nil {
			return e
		}
		_, e = store.EmitSync(r.Context(), tx, a.UserID, "conversation.hidden", convID, 0, map[string]string{"conversation_id": convID})
		return e
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	httpx.WriteData(w, r, 200, map[string]any{"hidden": true})
}

func (s *Service) handleClear(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	convID := r.PathValue("id")
	_, _, _, _, isMember, _, err := memberCheck(r.Context(), s.D.Store.Pool, convID, a.UserID)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if !isMember {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	var req struct {
		ThroughSeq string `json:"through_seq"`
	}
	if err := httpx.DecodeJSON(w, r, &req, 1<<10); err != nil && err != errEOI {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	through := int64(-1)
	if req.ThroughSeq != "" {
		n, err := strconv.ParseInt(req.ThroughSeq, 10, 64)
		if err != nil || n < 0 {
			httpx.WriteError(w, r, httpx.ErrInvalidRequest.WithMessage("through_seq 需为非负整数字符串"))
			return
		}
		through = n
	}
	err = s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		if !policy.Active(r.Context(), tx, a.UserID) {
			return httpx.ErrAccountDisabled
		}
		_, _, _, _, member, _, e := memberCheck(r.Context(), tx, convID, a.UserID)
		if e != nil {
			return e
		}
		if !member {
			return httpx.ErrForbidden
		}
		_, e = tx.Exec(r.Context(), `INSERT INTO conversation_user_state(id, conversation_id, user_id, cleared_through_seq)
		VALUES($1,$2,$3, LEAST($4, (SELECT last_seq FROM conversations WHERE id=$2)))
		ON CONFLICT (conversation_id, user_id) DO UPDATE SET cleared_through_seq=GREATEST(conversation_user_state.cleared_through_seq, LEAST($4, (SELECT last_seq FROM conversations WHERE id=$2))), updated_at=now()`,
			idgen.New("cus"), convID, a.UserID, mapBefore(through))
		if e != nil {
			return e
		}
		_, e = store.EmitSync(r.Context(), tx, a.UserID, "conversation.cleared", convID, 0, map[string]string{"conversation_id": convID})
		return e
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	httpx.WriteData(w, r, 200, map[string]any{"cleared": true})
}

// ---------------------------------------------------------------- recall

func (s *Service) handleRecall(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	msgID := r.PathValue("id")
	var convID, sender string
	var seq int64
	var created time.Time
	err := s.D.Store.Pool.QueryRow(r.Context(), `SELECT conversation_id, sender_id, seq, created_at FROM messages WHERE id=$1`, msgID).
		Scan(&convID, &sender, &seq, &created)
	if errors.Is(err, pgx.ErrNoRows) {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	if sender != a.UserID {
		httpx.WriteError(w, r, httpx.ErrForbidden.WithMessage("只能撤回自己的消息"))
		return
	}
	if time.Since(created) > s.D.Cfg.RecallWindow {
		httpx.WriteError(w, r, httpx.ErrInvalidState.WithMessage("超过撤回时间窗"))
		return
	}
	kind, _, lo, hi, isMember, _, err := memberCheck(r.Context(), s.D.Store.Pool, convID, a.UserID)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if !isMember {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	var wakeUsers []string
	err = s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		if !policy.Active(r.Context(), tx, a.UserID) {
			return httpx.ErrAccountDisabled
		}
		tag, err := tx.Exec(r.Context(), `UPDATE messages SET text=NULL, recalled_at=now() WHERE id=$1 AND recalled_at IS NULL`, msgID)
		if err != nil {
			return httpx.ErrStorage
		}
		if tag.RowsAffected() == 0 {
			return httpx.ErrInvalidState.WithMessage("消息已撤回")
		}
		targets, err := recallRecipients(r.Context(), tx, kind, lo, hi, convID, seq)
		if err != nil {
			return err
		}
		for _, u := range targets {
			if _, err := store.EmitSync(r.Context(), tx, u, "message.recall", msgID, seq,
				map[string]any{"conversation_id": convID, "message_id": msgID}); err != nil {
				return err
			}
		}
		wakeUsers = targets
		return nil
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	s.wake(wakeUsers...)
	httpx.WriteData(w, r, 200, map[string]any{"recalled": true})
}

// ---------------------------------------------------------------- forward

func (s *Service) handleForward(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	msgID := r.PathValue("id")
	var req struct {
		ToConversationIDs []string `json:"to_conversation_ids"`
		ClientMessageID   string   `json:"client_message_id"`
	}
	if err := httpx.DecodeJSON(w, r, &req, 4<<10); err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if len(req.ToConversationIDs) == 0 || len(req.ToConversationIDs) > 50 {
		httpx.WriteError(w, r, httpx.ErrInvalidRequest.WithMessage("to_conversation_ids 需为 1–50 个"))
		return
	}
	var text *string
	var mType string
	var assets []string
	err := s.D.Store.Pool.QueryRow(r.Context(), `SELECT type, text FROM messages WHERE id=$1`, msgID).Scan(&mType, &text)
	if errors.Is(err, pgx.ErrNoRows) {
		httpx.WriteError(w, r, httpx.ErrNotFound)
		return
	}
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	// source access: must be a (current or past) member with visibility
	{
		sconv := sourceConvOf(r.Context(), s, msgID)
		kind, _, _, _, isMember, _, err := memberCheck(r.Context(), s.D.Store.Pool, sconv, a.UserID)
		if err != nil {
			httpx.WriteErrorOf(w, r, err)
			return
		}
		if !isMember {
			httpx.WriteError(w, r, httpx.ErrForbidden)
			return
		}
		var sseq int64
		var srecalled bool
		if err := s.D.Store.Pool.QueryRow(r.Context(), `SELECT seq, recalled_at IS NOT NULL FROM messages WHERE id=$1`, msgID).Scan(&sseq, &srecalled); err != nil {
			httpx.WriteErrorOf(w, r, httpx.ErrStorage)
			return
		}
		if srecalled || !s.visibleTo(r.Context(), nil, sconv, a.UserID, kind, sseq) {
			httpx.WriteError(w, r, httpx.ErrForbidden.WithMessage("源消息不可见"))
			return
		}
	}
	rows, err := s.D.Store.Pool.Query(r.Context(), `SELECT asset_id FROM message_attachments WHERE message_id=$1 ORDER BY position`, msgID)
	if err != nil {
		httpx.WriteErrorOf(w, r, httpx.ErrStorage)
		return
	}
	for rows.Next() {
		var x string
		if err := rows.Scan(&x); err == nil {
			assets = append(assets, x)
		}
	}
	rows.Close()
	// forwarded copy loses original text after recall (text is NULL then)
	created := []map[string]any{}
	failed := []map[string]any{}
	for _, to := range req.ToConversationIDs {
		single := sendReq{forwardSourceID: msgID}
		if text != nil {
			single.Text = text
		}
		single.AssetIDs = assets
		base := ""
		if req.ClientMessageID != "" {
			base = req.ClientMessageID
		}
		if base != "" {
			single.ClientMessageID = base + "|fwd|" + to
		}
		err := s.forwardOne(r.Context(), a.UserID, to, &single)
		if err != nil {
			failed = append(failed, map[string]any{"conversation_id": to, "error": httpx.AsError(err).Code})
			continue
		}
		created = append(created, map[string]any{"conversation_id": to, "forwarded": true})
	}
	httpx.WriteData(w, r, 200, map[string]any{"created": created, "failed": failed})
}

func sourceConvOf(ctx context.Context, s *Service, msgID string) string {
	var c string
	_ = s.D.Store.Pool.QueryRow(ctx, `SELECT conversation_id FROM messages WHERE id=$1`, msgID).Scan(&c)
	return c
}

// forwardOne performs a restricted send into an existing conversation
// (assets are re-attached, ownership transfers to the forwarder's message;
// the underlying media row stays with its original owner).
func (s *Service) forwardOne(ctx context.Context, userID, convID string, req *sendReq) error {
	return s.D.Store.Tx(ctx, func(tx pgx.Tx) error {
		if !policy.Active(ctx, tx, userID) {
			return httpx.ErrAccountDisabled
		}
		if req.forwardSourceID != "" {
			var sourceConv, sourceType string
			var sourceSeq int64
			var recalled bool
			var sourceText *string
			if e := tx.QueryRow(ctx, `SELECT conversation_id,type,seq,recalled_at IS NOT NULL,text FROM messages WHERE id=$1`, req.forwardSourceID).Scan(&sourceConv, &sourceType, &sourceSeq, &recalled, &sourceText); e != nil {
				return httpx.ErrNotFound
			}
			kind, _, _, _, member, _, e := memberCheck(ctx, tx, sourceConv, userID)
			if e != nil {
				return e
			}
			if recalled || !member || !s.visibleTo(ctx, tx, sourceConv, userID, kind, sourceSeq) || (sourceType != "text" && sourceType != "image" && sourceType != "file") {
				return httpx.ErrForbidden
			}
			req.Text = sourceText
			req.AssetIDs = nil
			rows, e := tx.Query(ctx, `SELECT asset_id FROM message_attachments WHERE message_id=$1 ORDER BY position`, req.forwardSourceID)
			if e != nil {
				return e
			}
			for rows.Next() {
				var id string
				if e = rows.Scan(&id); e != nil {
					rows.Close()
					return e
				}
				req.AssetIDs = append(req.AssetIDs, id)
			}
			e = rows.Err()
			rows.Close()
			if e != nil {
				return e
			}
		}

		// idempotent replay: the forwarded copy already exists → original success
		if req.ClientMessageID != "" {
			var done bool
			if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM messages
				WHERE sender_id=$1 AND client_message_id=$2 AND conversation_id=$3)`,
				userID, req.ClientMessageID, convID).Scan(&done); err != nil {
				return httpx.ErrStorage
			}
			if done {
				return nil
			}
		}
		kind, _, lo, hi, _, active, err := memberCheck(ctx, tx, convID, userID)
		if err != nil {
			return err
		}
		if !active {
			return httpx.ErrSendNotAllowed
		}
		var members []string
		if kind == "private" {
			if err := store.LockFriendPair(ctx, tx, lo, hi); err != nil {
				return err
			}
			if !policy.CanCommunicate(ctx, tx, lo, hi) || policy.AreBlocked(ctx, tx, lo, hi) {
				return httpx.ErrSendNotAllowed
			}
			members = []string{lo, hi}
		} else {
			if _, err := tx.Exec(ctx, `SELECT last_seq FROM conversations WHERE id=$1 FOR UPDATE`, convID); err != nil {
				return httpx.ErrStorage
			}
			role, mutedNow, err := groupRole(ctx, tx, convID, userID)
			if err != nil {
				return err
			}
			var muteAll bool
			_ = tx.QueryRow(ctx, `SELECT mute_all FROM group_settings WHERE conversation_id=$1`, convID).Scan(&muteAll)
			if role == "" || (role == "member" && muteAll) || (role != "owner" && mutedNow) {
				return httpx.ErrSendNotAllowed
			}
			members, err = membersOf(ctx, tx, kind, lo, hi, convID)
			if err != nil {
				return err
			}
		}
		mType := "text"
		if len(req.AssetIDs) > 0 {
			mType, err = attachmentType(ctx, tx, req.AssetIDs)
			if err != nil {
				return err
			}
		}
		if _, err := tx.Exec(ctx, `SELECT last_seq FROM conversations WHERE id=$1 FOR UPDATE`, convID); err != nil {
			return httpx.ErrStorage
		}
		seq, err := store.NextSeq(ctx, tx, convID)
		if err != nil {
			return err
		}
		msgID := idgen.New("msg")
		text := ""
		if req.Text != nil {
			text = *req.Text
		}
		var textArg any
		if text != "" {
			textArg = text
		}
		var cmArg any
		if req.ClientMessageID != "" {
			cmArg = req.ClientMessageID
		}
		if _, err := tx.Exec(ctx, `INSERT INTO messages(id, conversation_id, seq, sender_id, client_message_id, type, text)
			VALUES($1,$2,$3,$4,$5,$6,$7)`, msgID, convID, seq, userID, cmArg, mType, textArg); err != nil {
			return httpx.ErrStorage
		}
		for i, as := range req.AssetIDs {
			// forwarded assets must stay ready; ownership does not move
			var ready bool
			_ = tx.QueryRow(ctx, `SELECT state='ready' FROM media_assets WHERE id=$1`, as).Scan(&ready)
			if !ready {
				continue
			}
			if _, err := tx.Exec(ctx, `INSERT INTO message_attachments(id, message_id, asset_id, position) VALUES($1,$2,$3,$4)
				ON CONFLICT (message_id, asset_id) DO NOTHING`, idgen.New("att"), msgID, as, i); err != nil {
				return httpx.ErrStorage
			}
		}
		for _, u := range members {
			if _, err := store.EmitSync(ctx, tx, u, "message.new", msgID, seq,
				map[string]any{"conversation_id": convID, "message_id": msgID, "seq": seqStr(seq)}); err != nil {
				return err
			}

		}
		if _, err := tx.Exec(ctx, `UPDATE conversations SET last_seq=$2, updated_at=now() WHERE id=$1`, convID, seq); err != nil {
			return httpx.ErrStorage
		}
		return nil
	})
}

func attachmentType(ctx context.Context, tx pgx.Tx, assets []string) (string, error) {
	var file bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM media_assets WHERE id=ANY($1) AND purpose='chat_file')`, assets).Scan(&file); err != nil {
		return "", httpx.ErrStorage
	}
	if file {
		return "file", nil
	}
	return "image", nil
}

// ---------------------------------------------------------------- wiring

func (s *Service) wake(userIDs ...string) {
	if s.D.Realtime != nil {
		s.D.Realtime.Wakeup(userIDs...)
	}
}

func pq(ss []string) []string { return ss }

func digestBytes(hexStr string) []byte {
	b, _ := hex.DecodeString(hexStr)
	return b
}

func isUnique(err error, constraint string) bool {
	var pe *pgconn.PgError
	return errors.As(err, &pe) && pe.Code == "23505" && strings.Contains(err.Error(), constraint)
}

// errEOI marks an empty body for optional-JSON endpoints.
var errEOI = errors.New("eof")

var _ = pgconn.PgError{}
