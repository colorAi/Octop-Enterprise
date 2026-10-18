package chat

import (
	"context"
	"errors"
	"net/http"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
	"octop/communication/internal/auth"
	"octop/communication/internal/httpx"
	"octop/communication/internal/idgen"
	"octop/communication/internal/policy"
	"octop/communication/internal/store"
)

type messageItem struct {
	ID               string           `json:"message_id"`
	Seq              string           `json:"seq"`
	SenderID         string           `json:"sender_id"`
	Type             string           `json:"type"`
	Text             *string          `json:"text"`
	Recalled         bool             `json:"recalled"`
	CreatedAt        string           `json:"created_at"`
	Attachments      []map[string]any `json:"attachments,omitempty"`
	ClientMessageID  *string          `json:"client_message_id,omitempty"`
	ReplyToMessageID *string          `json:"reply_to_message_id,omitempty"`
	ReplyPreview     *messageItem     `json:"reply_preview,omitempty"`
	MentionUserIDs   []string         `json:"mention_user_ids"`
	MentionAll       bool             `json:"mention_all"`
	SystemKind       string           `json:"system_kind,omitempty"`
}

func (s *Service) attachMessageAssets(ctx context.Context, item *messageItem) error {
	if item.Recalled {
		item.Text = nil
		item.Attachments = nil
		return nil
	}
	rows, err := s.D.Store.Pool.Query(ctx, `SELECT a.asset_id,ma.state,ma.purpose,ma.filename,ma.mime,ma.byte_size
        FROM message_attachments a JOIN media_assets ma ON ma.id=a.asset_id WHERE a.message_id=$1 ORDER BY a.position`, item.ID)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var id, state, purpose, name, mime string
		var size int64
		if err = rows.Scan(&id, &state, &purpose, &name, &mime, &size); err != nil {
			return err
		}
		item.Attachments = append(item.Attachments, map[string]any{"asset_id": id, "state": state, "purpose": purpose, "filename": name, "mime": mime, "size_bytes": size})
	}
	return rows.Err()
}

// A quote never bypasses the reader's clear marker or membership epochs.
// The parent message being visible does not imply its source is visible.
func (s *Service) visibleMessage(ctx context.Context, conv, id, user, kind string) (*messageItem, error) {
	var item messageItem
	var sender *string
	var created time.Time
	err := s.D.Store.Pool.QueryRow(ctx, `SELECT id,seq,sender_id,type,text,recalled_at IS NOT NULL,created_at,reply_to_message_id
        FROM messages WHERE conversation_id=$1 AND id=$2`, conv, id).Scan(&item.ID, newSeq(&item.Seq), &sender, &item.Type, &item.Text, &item.Recalled, &created, &item.ReplyToMessageID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	seq, err := strconv.ParseInt(item.Seq, 10, 64)
	if err != nil {
		return nil, err
	}
	if !s.visibleTo(ctx, nil, conv, user, kind, seq) {
		return nil, nil
	}
	if sender != nil {
		item.SenderID = *sender
	}
	item.CreatedAt = created.UTC().Format(time.RFC3339)
	if err = s.attachMessageAssets(ctx, &item); err != nil {
		return nil, err
	}
	return &item, nil
}

func (s *Service) handleMessage(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	conv := r.PathValue("id")
	kind, _, _, _, member, _, err := memberCheck(r.Context(), s.D.Store.Pool, conv, a.UserID)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if !member {
		httpx.WriteErrorOf(w, r, httpx.ErrNotFound)
		return
	}
	item, err := s.visibleMessage(r.Context(), conv, r.PathValue("message_id"), a.UserID, kind)
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	if item == nil {
		httpx.WriteErrorOf(w, r, httpx.ErrNotFound)
		return
	}
	httpx.WriteData(w, r, 200, item)
}

// Delete affects only this user's visibility. It never deletes another member's
// history, dissolves a compulsory group, or prevents future messages reopening it.
func (s *Service) handleDelete(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	conv := r.PathValue("id")
	err := s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		if !policy.Active(r.Context(), tx, a.UserID) {
			return httpx.ErrAccountDisabled
		}
		_, _, _, _, member, _, err := memberCheck(r.Context(), tx, conv, a.UserID)
		if err != nil {
			return err
		}
		if !member {
			return httpx.ErrNotFound
		}
		var seq int64
		// Serialize the deletion boundary with concurrent sends.
		if err = tx.QueryRow(r.Context(), `SELECT last_seq FROM conversations WHERE id=$1 FOR UPDATE`, conv).Scan(&seq); err != nil {
			return err
		}
		_, err = tx.Exec(r.Context(), `INSERT INTO conversation_user_state(id,conversation_id,user_id,hidden_at_seq,cleared_through_seq,last_read_seq,pinned)
            VALUES($1,$2,$3,$4,$4,$4,false) ON CONFLICT(conversation_id,user_id) DO UPDATE
            SET hidden_at_seq=$4,cleared_through_seq=GREATEST(conversation_user_state.cleared_through_seq,$4),
            last_read_seq=GREATEST(conversation_user_state.last_read_seq,$4),pinned=false,updated_at=now()`, idgen.New("cus"), conv, a.UserID, seq)
		if err != nil {
			return err
		}
		if _, err = store.EmitSync(r.Context(), tx, a.UserID, "conversation.cleared", conv, 0, map[string]string{"conversation_id": conv}); err != nil {
			return err
		}
		_, err = store.EmitSync(r.Context(), tx, a.UserID, "conversation.hidden", conv, 0, map[string]string{"conversation_id": conv})
		return err
	})
	if err != nil {
		httpx.WriteErrorOf(w, r, err)
		return
	}
	httpx.WriteData(w, r, 200, map[string]bool{"deleted": true})
}
