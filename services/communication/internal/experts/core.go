// Package experts owns group bindings, ordering and the final message boundary.
// All writes use the existing organization transaction before conversation locks.
package experts

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/jackc/pgx/v5"
	"octop/communication/internal/deps"
	"octop/communication/internal/httpx"
	"octop/communication/internal/idgen"
	"octop/communication/internal/store"
)

type Service struct {
	D              *deps.Deps
	reconcileMu    sync.Mutex
	reconcileAfter string
}

func New(d *deps.Deps) *Service { return &Service{D: d} }
func hash(b []byte) string      { v := sha256.Sum256(b); return hex.EncodeToString(v[:]) }
func failure(code string) error { return httpx.ErrInvalidState.WithCode(code) }
func terminal(state string) bool {
	return state == "completed" || state == "failed" || state == "rejected" || state == "cancelled" || state == "unknown"
}

type Source struct {
	Agent    string           `json:"source_agent_id"`
	Row      int64            `json:"source_row_id"`
	Name     string           `json:"display_name"`
	Avatar   map[string]any   `json:"avatar"`
	Versions map[string]int64 `json:"authorization_versions"`
}
type Binding struct {
	ID            string         `json:"binding_id"`
	Conversation  string         `json:"conversation_id"`
	Source        string         `json:"source_agent_id"`
	Row           int64          `json:"source_row_id"`
	Generation    int64          `json:"generation"`
	ScopeRevision int64          `json:"scope_revision"`
	Joined        int64          `json:"joined_seq"`
	State         string         `json:"state"`
	Name          string         `json:"display_name"`
	Avatar        map[string]any `json:"avatar"`
}
type Call struct {
	AgeSeconds   float64          `json:"-"`
	Name         string           `json:"expert_name,omitempty"`
	ID           string           `json:"call_id"`
	Conversation string           `json:"conversation_id"`
	Message      string           `json:"message_id"`
	Seq          int64            `json:"message_seq"`
	Binding      string           `json:"binding_id"`
	Generation   int64            `json:"binding_generation"`
	Caller       string           `json:"caller_id"`
	Versions     map[string]int64 `json:"authorization_versions"`
	State        string           `json:"call_state"`
	Reason       string           `json:"reason_code"`
	Control      int64            `json:"control_seq"`
	Dispatch     *int64           `json:"dispatch_seq"`
	Execution    *string          `json:"execution_id"`
	Result       *string          `json:"result_digest"`
	Receipt      map[string]any   `json:"receipt"`
	MaxChars     int              `json:"-"`
}

func load(ctx context.Context, tx pgx.Tx, id string) (Call, error) {
	return queryCall(ctx, tx, id, true)
}
func queryCall(ctx context.Context, tx pgx.Tx, id string, lock bool) (Call, error) {
	var c Call
	query := `SELECT call_id,conversation_id,message_id,message_seq,binding_id,COALESCE(binding_generation,0),caller_id,authorization_versions,call_state,reason_code,control_seq,dispatch_seq,execution_id,result_digest,receipt,COALESCE(max_text_chars,0),EXTRACT(EPOCH FROM clock_timestamp()-updated_at)::double precision FROM group_expert_calls WHERE call_id=$1`
	if lock {
		query += " FOR UPDATE"
	}
	err := tx.QueryRow(ctx, query, id).Scan(&c.ID, &c.Conversation, &c.Message, &c.Seq, &c.Binding, &c.Generation, &c.Caller, &c.Versions, &c.State, &c.Reason, &c.Control, &c.Dispatch, &c.Execution, &c.Result, &c.Receipt, &c.MaxChars, &c.AgeSeconds)
	if errors.Is(err, pgx.ErrNoRows) {
		return c, httpx.ErrNotFound
	}
	return c, err
}
func binding(ctx context.Context, tx pgx.Tx, id string) (Binding, error) {
	var b Binding
	e := tx.QueryRow(ctx, `SELECT binding_id,conversation_id,source_agent_id,source_row_id,generation,scope_revision,joined_seq,state,display_name,avatar FROM group_expert_bindings WHERE binding_id=$1`, id).Scan(&b.ID, &b.Conversation, &b.Source, &b.Row, &b.Generation, &b.ScopeRevision, &b.Joined, &b.State, &b.Name, &b.Avatar)
	return b, e
}
func enqueue(ctx context.Context, tx pgx.Tx, kind, op, target string, payload any) error {
	raw, e := json.Marshal(payload)
	if e != nil {
		return e
	}
	_, e = tx.Exec(ctx, `INSERT INTO group_expert_outbox(operation_id,kind,target_id,request_digest,payload) VALUES($1,$2,$3,$4,$5) ON CONFLICT(operation_id) DO NOTHING`, op, kind, target, hash(raw), raw)
	if e != nil {
		return e
	}
	return store.EnqueueJob(ctx, tx, "group.expert.reconcile.call", op, map[string]string{"call_id": target}, time.Now())
}
func notify(ctx context.Context, tx pgx.Tx, conv, kind, object string, seq int64) error {
	// For a call update, the referenced message may predate a member's join
	// or have been cleared on another device. Use the same visibility as history.
	rows, e := tx.Query(ctx, `SELECT DISTINCT me.user_id FROM member_epochs me
		LEFT JOIN conversation_user_state st ON st.conversation_id=me.conversation_id AND st.user_id=me.user_id
		WHERE me.conversation_id=$1 AND me.left_at IS NULL AND (NOT $2 OR (
			$3>COALESCE(st.cleared_through_seq,0) AND EXISTS(
				SELECT 1 FROM member_epochs history WHERE history.conversation_id=me.conversation_id AND history.user_id=me.user_id
				AND $3 BETWEEN history.visible_from_seq AND COALESCE(history.visible_through_seq,9223372036854775807)
			)
		)) ORDER BY me.user_id`, conv, kind == "group.expert.call", seq)
	if e != nil {
		return e
	}
	var users []string
	for rows.Next() {
		var u string
		if e = rows.Scan(&u); e != nil {
			rows.Close()
			return e
		}
		users = append(users, u)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	for _, u := range users {
		if _, e = store.EmitSync(ctx, tx, u, kind, object, seq, map[string]any{"conversation_id": conv, "message_id": object, "seq": store.BigStr(seq)}); e != nil {
			return e
		}
	}
	return nil
}
func system(ctx context.Context, tx pgx.Tx, conv, kind string) (int64, error) {
	seq, e := store.NextSeq(ctx, tx, conv)
	if e != nil {
		return 0, e
	}
	id := idgen.New("msg")
	if _, e = tx.Exec(ctx, `INSERT INTO messages(id,conversation_id,seq,type,text,system_kind) VALUES($1,$2,$3,'system','',$4)`, id, conv, seq, kind); e != nil {
		return 0, e
	}
	if e = notify(ctx, tx, conv, "message.new", id, seq); e != nil {
		return 0, e
	}
	return seq, notify(ctx, tx, conv, "group.update", conv, seq)
}
func changed(ctx context.Context, tx pgx.Tx, c Call, state, reason string) error {
	if _, e := tx.Exec(ctx, `UPDATE group_expert_calls SET call_state=$2,reason_code=$3,updated_at=now() WHERE call_id=$1`, c.ID, state, reason); e != nil {
		return e
	}
	// History readers re-read the original visible message; no answer in events.
	return notify(ctx, tx, c.Conversation, "group.expert.call", c.Message, c.Seq)
}
func advance(ctx context.Context, tx pgx.Tx, conv string) error {
	var occupied bool
	if e := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM group_expert_calls WHERE conversation_id=$1 AND call_state IN ('dispatched','awaiting_delivery','cancel_requested'))`, conv).Scan(&occupied); e != nil {
		return e
	}
	if occupied {
		return nil
	}
	var id, state string
	e := tx.QueryRow(ctx, `SELECT call_id,call_state FROM group_expert_calls WHERE conversation_id=$1 AND call_state IN ('pending_admission','queued') ORDER BY message_seq,call_id LIMIT 1`, conv).Scan(&id, &state)
	if errors.Is(e, pgx.ErrNoRows) {
		return nil
	}
	if e != nil {
		return e
	}
	if state != "queued" {
		return nil
	}
	c, e := load(ctx, tx, id)
	if e != nil {
		return e
	}
	seq := c.Control + 1
	if _, e = tx.Exec(ctx, `UPDATE group_expert_calls SET dispatch_seq=$2,control_seq=$2 WHERE call_id=$1`, id, seq); e != nil {
		return e
	}
	if e = changed(ctx, tx, c, "dispatched", ""); e != nil {
		return e
	}
	return enqueue(ctx, tx, "dispatch", fmt.Sprintf("control:%s:%d", id, seq), id, map[string]any{"call_id": id, "control_seq": seq, "kind": "dispatch"})
}
func cancel(ctx context.Context, tx pgx.Tx, c Call, reason string) error {
	if terminal(c.State) || c.State == "cancel_requested" {
		return nil
	}
	seq := c.Control + 1
	if _, e := tx.Exec(ctx, `UPDATE group_expert_calls SET control_seq=$2 WHERE call_id=$1`, c.ID, seq); e != nil {
		return e
	}
	state := "cancelled"
	if c.Dispatch != nil {
		state = "cancel_requested"
	}
	if e := changed(ctx, tx, c, state, reason); e != nil {
		return e
	}
	if e := enqueue(ctx, tx, "cancel", fmt.Sprintf("control:%s:%d", c.ID, seq), c.ID, map[string]any{"call_id": c.ID, "control_seq": seq, "kind": "cancel"}); e != nil {
		return e
	}
	return advance(ctx, tx, c.Conversation)
}

// contextFor intentionally ignores personal clear markers AFTER submission.
// It never reads more than the question and its one direct quote.
func contextFor(ctx context.Context, tx pgx.Tx, c Call, lock bool) (map[string]any, error) {
	var reason string
	err := tx.QueryRow(ctx, `SELECT COALESCE(payload->>'reason_code','group_expert_input_unavailable') FROM group_expert_outbox WHERE kind='recall' AND target_id=$1 ORDER BY created_at,operation_id LIMIT 1`, c.ID).Scan(&reason)
	if err == nil {
		return nil, failure(reason)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return nil, err
	}
	suffix := ""
	if lock {
		suffix = " FOR SHARE"
	}
	b, e := binding(ctx, tx, c.Binding)
	if e != nil || b.State != "active" || b.Conversation != c.Conversation || b.Generation != c.Generation {
		return nil, failure("group_expert_removed")
	}
	var allowed bool
	e = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM conversations c JOIN group_settings g ON g.conversation_id=c.id JOIN member_epochs me ON me.conversation_id=c.id JOIN users u ON u.id=me.user_id WHERE c.id=$1 AND c.status='active' AND u.id=$2 AND u.status='active' AND u.permissions ? 'messaging' AND me.left_at IS NULL AND (me.role='owner' OR me.mute_until IS NULL OR me.mute_until<=now()) AND (NOT g.mute_all OR me.role<>'member'))`, c.Conversation, c.Caller).Scan(&allowed)
	if e != nil {
		return nil, e
	}
	if !allowed {
		return nil, failure("group_expert_send_not_allowed")
	}
	var text, quoteID *string
	var recalled bool
	var seq int64
	e = tx.QueryRow(ctx, `SELECT text,reply_to_message_id,recalled_at IS NOT NULL,seq FROM messages WHERE id=$1 AND conversation_id=$2`+suffix, c.Message, c.Conversation).Scan(&text, &quoteID, &recalled, &seq)
	if e != nil {
		return nil, e
	}
	if recalled || seq <= b.Joined {
		return nil, failure("group_expert_input_unavailable")
	}
	question := ""
	if text != nil {
		question = strings.TrimSpace(*text)
	}
	if question == "" || question == "@"+b.Name {
		return nil, failure("group_expert_question_required")
	}
	if e = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM member_epochs WHERE conversation_id=$1 AND user_id=$2 AND $3 BETWEEN visible_from_seq AND COALESCE(visible_through_seq,9223372036854775807))`, c.Conversation, c.Caller, seq).Scan(&allowed); e != nil {
		return nil, e
	}
	if !allowed {
		return nil, failure("group_expert_input_unavailable")
	}
	quote := ""
	if quoteID != nil {
		var qtext *string
		var qseq int64
		var qrecall bool
		e = tx.QueryRow(ctx, `SELECT text,seq,recalled_at IS NOT NULL FROM messages WHERE id=$1 AND conversation_id=$2`+suffix, *quoteID, c.Conversation).Scan(&qtext, &qseq, &qrecall)
		if e != nil {
			return nil, e
		}
		if qrecall || qseq <= b.Joined || qseq >= seq || qtext == nil || strings.TrimSpace(*qtext) == "" {
			return nil, failure("group_expert_quote_unavailable")
		}
		if e = tx.QueryRow(ctx, `SELECT NOT EXISTS(SELECT 1 FROM member_epochs audience WHERE audience.conversation_id=$1 AND audience.left_at IS NULL AND NOT EXISTS(SELECT 1 FROM member_epochs visible WHERE visible.conversation_id=$1 AND visible.user_id=audience.user_id AND $2 BETWEEN visible.visible_from_seq AND COALESCE(visible.visible_through_seq,9223372036854775807)))`, c.Conversation, qseq).Scan(&allowed); e != nil {
			return nil, e
		}
		if !allowed {
			return nil, failure("group_expert_quote_audience")
		}
		quote = *qtext
	}
	return map[string]any{"question": question, "quote": quote, "source_agent_id": b.Source, "source_row_id": b.Row}, nil
}

// Start is called after the ordinary human message has passed all validations.
func (s *Service) Start(ctx context.Context, tx pgx.Tx, conv, message, caller, expert string, seq int64) (Call, error) {
	c := Call{ID: idgen.New("gcall"), Conversation: conv, Message: message, Seq: seq, Binding: expert, Caller: caller, State: "pending_admission", MaxChars: s.D.Cfg.Limits.MaxTextChars, Versions: map[string]int64{}}
	var enabled bool
	if e := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM group_settings g,group_expert_runtime r WHERE g.conversation_id=$1 AND g.experts_enabled AND r.protocol=2 AND r.valid_until>now())`, conv).Scan(&enabled); e != nil {
		return c, e
	}
	reject := func(reason string) { c.State = "rejected"; c.Reason = reason }
	b, e := binding(ctx, tx, expert)
	if e == nil && b.Conversation == conv {
		c.Name = b.Name
	}
	if !enabled {
		reject("group_expert_unavailable")
	} else if errors.Is(e, pgx.ErrNoRows) || b.State != "active" || b.Conversation != conv {
		reject("group_expert_removed")
	} else if e != nil {
		return c, e
	} else {
		c.Generation = b.Generation
		var sources []Source
		var revision int64
		e = tx.QueryRow(ctx, `SELECT revision,sources FROM group_expert_scopes WHERE conversation_id=$1`, conv).Scan(&revision, &sources)
		if e != nil && !errors.Is(e, pgx.ErrNoRows) {
			return c, e
		}
		found := false
		for _, src := range sources {
			if src.Agent == b.Source && src.Row == b.Row {
				found = true
				for k, v := range src.Versions {
					c.Versions[k] = v
				}
			}
		}
		if !found {
			reject("group_expert_outside_scope")
		} else {
			c.Versions["scope:"+conv] = revision
			parts := strings.Split(caller, ":")
			domain := "user:" + parts[len(parts)-1]
			var v int64
			e = tx.QueryRow(ctx, `SELECT revision FROM group_expert_authorizations WHERE domain_key=$1`, domain).Scan(&v)
			if errors.Is(e, pgx.ErrNoRows) {
				reject("group_expert_authorization_syncing")
			} else if e != nil {
				return c, e
			} else {
				c.Versions[domain] = v
			}
			if c.State != "rejected" {
				if _, e = contextFor(ctx, tx, c, true); e != nil {
					var he *httpx.Error
					if !errors.As(e, &he) {
						return c, e
					}
					reject(he.Code)
				}
			}
			var count int
			if e = tx.QueryRow(ctx, `SELECT count(*) FROM group_expert_calls WHERE conversation_id=$1 AND call_state IN ('pending_admission','queued','dispatched','awaiting_delivery','cancel_requested')`, conv).Scan(&count); e != nil {
				return c, e
			}
			if count >= 4 {
				reject("group_expert_queue_full")
			}
		}
	}
	if c.State == "pending_admission" {
		if e = versionsMatch(ctx, tx, c); e != nil {
			reject("group_expert_authorization_syncing")
		}
	}
	raw, _ := json.Marshal(c.Versions)
	if _, e = tx.Exec(ctx, `INSERT INTO group_expert_calls(call_id,conversation_id,message_id,message_seq,binding_id,binding_generation,caller_id,request_digest,authorization_versions,call_state,reason_code,max_text_chars) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`, c.ID, conv, message, seq, expert, c.Generation, caller, hash([]byte(message+":"+expert)), raw, c.State, c.Reason, c.MaxChars); e != nil {
		return c, e
	}
	if c.State == "pending_admission" {
		for domain, version := range c.Versions {
			if _, e = tx.Exec(ctx, `INSERT INTO group_expert_call_authorizations(call_id,domain_key,revision) VALUES($1,$2,$3)`, c.ID, domain, version); e != nil {
				return c, e
			}
		}
		e = enqueue(ctx, tx, "register", "register:"+c.ID, c.ID, map[string]string{"call_id": c.ID})
	}
	return c, e
}
