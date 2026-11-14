package experts

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sort"
	"strings"

	"github.com/jackc/pgx/v5"
	"octop/communication/internal/auth"
	"octop/communication/internal/httpx"
	"octop/communication/internal/idgen"
	"octop/communication/internal/store"
)

type request struct {
	proofIssued  int64
	Operation    string              `json:"operation_id"`
	Request      string              `json:"request_id"`
	Actor        string              `json:"actor_id"`
	Organization bool                `json:"organization"`
	Revision     int64               `json:"revision"`
	Sources      []Source            `json:"sources"`
	Revocations  []bindingRevocation `json:"revocations"`
	Source       string              `json:"source_agent_id"`
	SourceRow    int64               `json:"source_row_id"`
	Generation   int64               `json:"generation"`
	Enabled      bool                `json:"enabled"`
	Protocol     int                 `json:"protocol"`
	Admission    string              `json:"admission_state"`
	Execution    string              `json:"run_id"`
	Reason       string              `json:"reason_code"`
	Digest       string              `json:"request_digest"`
	Receipt      map[string]any      `json:"receipt"`
	ResultWire   string              `json:"result_wire"`
	ResultDigest string              `json:"result_digest"`
	Unpublished  bool                `json:"unpublished"`
}
type bindingRevocation struct {
	Source string `json:"source_agent_id"`
	Row    int64  `json:"source_row_id"`
	Before int64  `json:"before_revision"`
}
type endpoint func(context.Context, pgx.Tx, *http.Request, request) (any, error)

func (s *Service) Register(m *http.ServeMux) {
	s.registerArtifacts(m)
	routes := map[string]endpoint{
		"GET /internal/group-experts/v1/capabilities":                      s.capabilities,
		"POST /internal/group-experts/v1/runtime":                          s.runtime,
		"POST /internal/group-experts/v1/groups/{id}/inspect":              s.inspect,
		"PUT /internal/group-experts/v1/groups/{id}/scope":                 s.scope,
		"PUT /internal/group-experts/v1/authorizations/{domain}":           s.authorization,
		"POST /internal/group-experts/v1/groups/{id}/bindings":             s.invite,
		"DELETE /internal/group-experts/v1/groups/{id}/bindings/{binding}": s.remove,
		"PUT /internal/group-experts/v1/groups/{id}/enabled":               s.enable,
		"GET /internal/group-experts/v1/calls/{call}/context":              s.context,
		"GET /internal/group-experts/v1/calls/{call}/artifacts/{asset}":    s.artifactStatus,
		"GET /internal/group-experts/v1/calls/{call}":                      s.call,
		"POST /internal/group-experts/v1/calls/{call}/admission":           s.admission,
		"POST /internal/group-experts/v1/calls/{call}/delivery":            s.awaitDelivery,
		"POST /internal/group-experts/v1/calls/{call}/result":              s.result,
		"GET /internal/group-experts/v1/calls/{call}/receipt":              s.receipt,
		"GET /internal/group-experts/v1/pending":                           s.pending,
		"POST /internal/group-experts/v1/pending/{operation}/ack":          s.ack,
		"POST /internal/group-experts/v1/pending/{operation}/retry":        s.retry,
	}
	for pattern, fn := range routes {
		m.HandleFunc(pattern, auth.ServiceRequest(s.D, func(w http.ResponseWriter, r *http.Request, c auth.Claims) {
			var v request
			if r.Method != "GET" {
				if e := httpx.DecodeJSON(w, r, &v, 2<<20); e != nil {
					httpx.WriteErrorOf(w, r, e)
					return
				}
			}
			if v.Actor != "" && v.Actor != c.UserID {
				httpx.WriteErrorOf(w, r, httpx.ErrForbidden)
				return
			}
			v.proofIssued = c.Issued
			var out any
			e := s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error { var err error; out, err = fn(r.Context(), tx, r, v); return err })
			if e != nil {
				httpx.WriteErrorOf(w, r, e)
				return
			}
			httpx.WriteData(w, r, 200, out)
		}))
	}
	m.HandleFunc("GET /api/app/v1/groups/{id}/expert-calls/{call}", auth.Middleware(s.D, s.visibleCall))
}
func (s *Service) capabilities(context.Context, pgx.Tx, *http.Request, request) (any, error) {
	return map[string]any{"protocol": 2, "schema": 21}, nil
}
func (s *Service) runtime(ctx context.Context, tx pgx.Tx, _ *http.Request, v request) (any, error) {
	if v.Protocol != 0 && v.Protocol != 2 {
		return nil, httpx.ErrInvalidRequest
	}
	_, e := tx.Exec(ctx, `UPDATE group_expert_runtime SET protocol=$1,valid_until=to_timestamp($2)+interval '15 seconds'`, v.Protocol, v.proofIssued)
	return map[string]any{"protocol": v.Protocol}, e
}
func access(ctx context.Context, tx pgx.Tx, conv string, v request, manage bool) error {
	if v.Actor == "" {
		return httpx.ErrForbidden
	}
	if _, e := store.LockConversation(ctx, tx, conv); e != nil {
		if errors.Is(e, pgx.ErrNoRows) {
			return httpx.ErrNotFound
		}
		return e
	}
	return readAccess(ctx, tx, conv, v, manage)
}
func readAccess(ctx context.Context, tx pgx.Tx, conv string, v request, manage bool) error {
	if v.Actor == "" {
		return httpx.ErrForbidden
	}
	var ok bool
	query := `SELECT EXISTS(SELECT 1 FROM users u JOIN member_epochs m ON m.user_id=u.id WHERE u.id=$1 AND u.status='active' AND u.permissions ? 'messaging' AND m.conversation_id=$2 AND m.left_at IS NULL`
	if manage {
		query += ` AND m.role IN ('owner','admin')`
	}
	query += `)`
	if v.Organization {
		query = `SELECT EXISTS(SELECT 1 FROM users WHERE id=$1 AND status='active' AND permissions ? 'departments') AND EXISTS(SELECT 1 FROM group_settings WHERE conversation_id=$2)`
	}
	if e := tx.QueryRow(ctx, query, v.Actor, conv).Scan(&ok); e != nil {
		return e
	}
	if !ok {
		return httpx.ErrForbidden
	}
	return nil
}
func (s *Service) inspect(ctx context.Context, tx pgx.Tx, r *http.Request, v request) (any, error) {
	conv := r.PathValue("id")
	if e := readAccess(ctx, tx, conv, v, false); e != nil {
		return nil, e
	}
	var enabled bool
	var status, kind string
	var dept *string
	if e := tx.QueryRow(ctx, `SELECT g.experts_enabled,c.status,g.group_kind,dg.department_id FROM group_settings g JOIN conversations c ON c.id=g.conversation_id LEFT JOIN department_groups dg ON dg.conversation_id=g.conversation_id WHERE g.conversation_id=$1`, conv).Scan(&enabled, &status, &kind, &dept); e != nil {
		return nil, e
	}
	rows, e := tx.Query(ctx, `SELECT binding_id FROM group_expert_bindings WHERE conversation_id=$1 ORDER BY created_at,binding_id`, conv)
	if e != nil {
		return nil, e
	}
	ids := []string{}
	for rows.Next() {
		var id string
		if e = rows.Scan(&id); e != nil {
			rows.Close()
			return nil, e
		}
		ids = append(ids, id)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	list := []Binding{}
	for _, id := range ids {
		b, e := binding(ctx, tx, id)
		if e != nil {
			return nil, e
		}
		list = append(list, b)
	}
	canManage := readAccess(ctx, tx, conv, v, true) == nil
	var revision int64
	var sources []Source
	e = tx.QueryRow(ctx, `SELECT revision,sources FROM group_expert_scopes WHERE conversation_id=$1`, conv).Scan(&revision, &sources)
	if e != nil && !errors.Is(e, pgx.ErrNoRows) {
		return nil, e
	}
	return map[string]any{"items": list, "experts_enabled": enabled, "status": status, "group_kind": kind, "department_id": dept, "can_manage": canManage, "scope_revision": revision, "sources": sources, "ready": ready(ctx, tx)}, nil
}
func managementReceipt(ctx context.Context, tx pgx.Tx, r *http.Request, v request, fn func() (any, error)) (any, error) {
	if strings.TrimSpace(v.Request) == "" || len(v.Request) > 128 {
		return nil, httpx.ErrInvalidRequest
	}
	raw, _ := json.Marshal(v)
	digest := hash(append([]byte(r.Method+":"+r.URL.Path+":"), raw...))
	dbDigest, _ := hex.DecodeString(digest)
	var old []byte
	var out map[string]any
	e := tx.QueryRow(ctx, `SELECT content_digest,result FROM operation_receipts WHERE actor_id=$1 AND scope='group.expert' AND idem_key=$2`, v.Actor, v.Request).Scan(&old, &out)
	if e == nil {
		if !bytes.Equal(old, dbDigest) {
			return nil, httpx.ErrIdemConflict
		}
		return out, nil
	}
	if !errors.Is(e, pgx.ErrNoRows) {
		return nil, e
	}
	value, e := fn()
	if e != nil {
		return nil, e
	}
	body, e := json.Marshal(value)
	if e != nil {
		return nil, e
	}
	if _, e = tx.Exec(ctx, `INSERT INTO operation_receipts(id,actor_id,scope,idem_key,content_digest,result) VALUES($1,$2,'group.expert',$3,$4,$5)`, idgen.New("rcp"), v.Actor, v.Request, dbDigest, body); e != nil {
		return nil, e
	}
	_, e = tx.Exec(ctx, `INSERT INTO organization_audit(id,actor_id,action,object_id,request_id,after_data) VALUES($1,$2,'group.expert',$3,$4,$5)`, idgen.New("audit"), v.Actor, r.PathValue("id"), v.Request, body)
	return value, e
}
func ready(ctx context.Context, tx pgx.Tx) bool {
	var ok bool
	_ = tx.QueryRow(ctx, `SELECT protocol=2 AND valid_until>now() FROM group_expert_runtime`).Scan(&ok)
	return ok
}
func (s *Service) enable(ctx context.Context, tx pgx.Tx, r *http.Request, v request) (any, error) {
	if !v.Organization {
		return nil, httpx.ErrForbidden
	}
	if e := access(ctx, tx, r.PathValue("id"), v, true); e != nil {
		return nil, e
	}
	return managementReceipt(ctx, tx, r, v, func() (any, error) {
		if v.Enabled && !ready(ctx, tx) {
			return nil, failure("group_expert_protocol_unavailable")
		}
		var previous bool
		if e := tx.QueryRow(ctx, `SELECT experts_enabled FROM group_settings WHERE conversation_id=$1`, r.PathValue("id")).Scan(&previous); e != nil {
			return nil, e
		}
		if previous == v.Enabled {
			return map[string]any{"experts_enabled": v.Enabled}, nil
		}
		_, e := tx.Exec(ctx, `UPDATE group_settings SET experts_enabled=$2 WHERE conversation_id=$1`, r.PathValue("id"), v.Enabled)
		if e != nil {
			return nil, e
		}
		_, e = system(ctx, tx, r.PathValue("id"), "group.expert.settings")
		return map[string]any{"experts_enabled": v.Enabled}, e
	})
}
func (s *Service) invite(ctx context.Context, tx pgx.Tx, r *http.Request, v request) (any, error) {
	conv := r.PathValue("id")
	if e := access(ctx, tx, conv, v, true); e != nil {
		return nil, e
	}
	return managementReceipt(ctx, tx, r, v, func() (any, error) {
		if !ready(ctx, tx) {
			return nil, failure("group_expert_protocol_unavailable")
		}
		var valid bool
		if e := tx.QueryRow(ctx, `SELECT c.status='active' AND g.experts_enabled FROM conversations c JOIN group_settings g ON g.conversation_id=c.id WHERE c.id=$1`, conv).Scan(&valid); e != nil {
			return nil, e
		}
		if !valid {
			return nil, failure("group_expert_unavailable")
		}
		var sources []Source
		var revision int64
		if e := tx.QueryRow(ctx, `SELECT revision,sources FROM group_expert_scopes WHERE conversation_id=$1`, conv).Scan(&revision, &sources); e != nil {
			return nil, failure("group_expert_authorization_syncing")
		}
		if v.Revision < revision {
			return nil, failure("group_expert_scope_changed")
		}
		if v.Revision > revision {
			return nil, failure("group_expert_authorization_syncing")
		}
		var authority int64
		if e := tx.QueryRow(ctx, `SELECT revision FROM group_expert_authorizations WHERE domain_key=$1`, "scope:"+conv).Scan(&authority); e != nil || authority != revision {
			return nil, failure("group_expert_authorization_syncing")
		}
		var source *Source
		for i := range sources {
			if sources[i].Agent == v.Source && sources[i].Row == v.SourceRow {
				source = &sources[i]
			}
		}
		if source == nil {
			return nil, failure("group_expert_outside_scope")
		}
		for domain, version := range source.Versions {
			var current int64
			if e := tx.QueryRow(ctx, `SELECT revision FROM group_expert_authorizations WHERE domain_key=$1`, domain).Scan(&current); e != nil || current != version {
				return nil, failure("group_expert_authorization_syncing")
			}
		}
		var revoked bool
		if e := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM group_expert_binding_revocations WHERE conversation_id=$1 AND source_agent_id=$2 AND source_row_id=$3 AND before_revision>$4)`, conv, v.Source, v.SourceRow, revision).Scan(&revoked); e != nil {
			return nil, e
		}
		if revoked {
			return nil, failure("group_expert_authorization_syncing")
		}
		var existing string
		e := tx.QueryRow(ctx, `SELECT binding_id FROM group_expert_bindings WHERE conversation_id=$1 AND source_agent_id=$2 AND state='active'`, conv, v.Source).Scan(&existing)
		if e == nil {
			b, e := binding(ctx, tx, existing)
			return map[string]any{"binding": b}, e
		}
		if !errors.Is(e, pgx.ErrNoRows) {
			return nil, e
		}
		var count int
		var generation int64
		if e = tx.QueryRow(ctx, `SELECT count(*) FROM group_expert_bindings WHERE conversation_id=$1 AND state='active'`, conv).Scan(&count); e != nil {
			return nil, e
		}
		if count >= 3 {
			return nil, failure("group_expert_limit")
		}
		if e = tx.QueryRow(ctx, `SELECT COALESCE(max(generation),0)+1 FROM group_expert_bindings WHERE conversation_id=$1 AND source_agent_id=$2`, conv, v.Source).Scan(&generation); e != nil {
			return nil, e
		}
		seq, e := system(ctx, tx, conv, "group.expert.joined")
		if e != nil {
			return nil, e
		}
		id := idgen.New("gexpert")
		avatar, _ := json.Marshal(source.Avatar)
		if source.Avatar == nil {
			avatar = []byte("{}")
		}
		if _, e = tx.Exec(ctx, `INSERT INTO group_expert_bindings(binding_id,conversation_id,source_agent_id,source_row_id,generation,scope_revision,joined_seq,state,display_name,avatar,invited_by) VALUES($1,$2,$3,$4,$5,$6,$7,'active',$8,$9,$10)`, id, conv, v.Source, v.SourceRow, generation, revision, seq, source.Name, avatar, v.Actor); e != nil {
			return nil, e
		}
		b, e := binding(ctx, tx, id)
		return map[string]any{"binding": b}, e
	})
}
func (s *Service) remove(ctx context.Context, tx pgx.Tx, r *http.Request, v request) (any, error) {
	conv := r.PathValue("id")
	if e := access(ctx, tx, conv, v, true); e != nil {
		return nil, e
	}
	return managementReceipt(ctx, tx, r, v, func() (any, error) {
		b, e := binding(ctx, tx, r.PathValue("binding"))
		if e != nil {
			return nil, httpx.ErrNotFound
		}
		if b.Conversation != conv || b.Generation != v.Generation {
			return nil, httpx.ErrIdemConflict
		}
		if b.State == "active" {
			if e = removeBinding(ctx, tx, b, "removed"); e != nil {
				return nil, e
			}
		}
		return map[string]any{"binding_id": b.ID, "generation": b.Generation, "state": "removed"}, nil
	})
}
func removeBinding(ctx context.Context, tx pgx.Tx, b Binding, state string) error {
	if _, e := tx.Exec(ctx, `UPDATE group_expert_bindings SET state=$2,removed_at=now() WHERE binding_id=$1`, b.ID, state); e != nil {
		return e
	}
	if _, e := system(ctx, tx, b.Conversation, "group.expert.removed"); e != nil {
		return e
	}
	return cancelWhere(ctx, tx, `binding_id=$1`, []any{b.ID}, "group_expert_removed")
}
func cancelWhere(ctx context.Context, tx pgx.Tx, where string, args []any, reason string) error {
	rows, e := tx.Query(ctx, `SELECT call_id,conversation_id FROM group_expert_calls WHERE call_state IN ('pending_admission','queued','dispatched','awaiting_delivery') AND (`+where+`) ORDER BY conversation_id,message_seq`, args...)
	if e != nil {
		return e
	}
	type target struct{ id, conv string }
	list := []target{}
	for rows.Next() {
		var t target
		if e = rows.Scan(&t.id, &t.conv); e != nil {
			rows.Close()
			return e
		}
		list = append(list, t)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	for _, t := range list {
		if _, e = store.LockConversation(ctx, tx, t.conv); e != nil {
			return e
		}
		c, e := load(ctx, tx, t.id)
		if e != nil {
			return e
		}
		if e = cancel(ctx, tx, c, reason); e != nil {
			return e
		}
	}
	return nil
}
func setAuthorization(ctx context.Context, tx pgx.Tx, domain string, revision int64) error {
	if domain == "" || len(domain) > 300 || revision < 1 {
		return httpx.ErrInvalidRequest
	}
	raw, _ := json.Marshal(map[string]any{"domain_key": domain, "revision": revision})
	if _, e := tx.Exec(ctx, `INSERT INTO group_expert_authorizations(domain_key,revision,content_digest) VALUES($1,$2,$3) ON CONFLICT(domain_key) DO UPDATE SET revision=GREATEST(group_expert_authorizations.revision,excluded.revision),content_digest=CASE WHEN excluded.revision>group_expert_authorizations.revision THEN excluded.content_digest ELSE group_expert_authorizations.content_digest END,updated_at=now()`, domain, revision, hash(raw)); e != nil {
		return e
	}
	// Every older dependency is invalidated, including notices received out of order.
	return cancelWhere(ctx, tx, `call_id IN (SELECT call_id FROM group_expert_call_authorizations WHERE domain_key=$1 AND revision<$2)`, []any{domain, revision}, "group_expert_authorization_changed")
}
func (s *Service) authorization(ctx context.Context, tx pgx.Tx, r *http.Request, v request) (any, error) {
	domain := r.PathValue("domain")
	if len(v.Revocations) > 500 || (len(v.Revocations) > 0 && !strings.HasPrefix(domain, "bindings:")) {
		return nil, httpx.ErrInvalidRequest
	}
	if e := setAuthorization(ctx, tx, domain, v.Revision); e != nil {
		return nil, e
	}
	if len(v.Revocations) > 0 {
		conv := strings.TrimPrefix(domain, "bindings:")
		if _, e := store.LockConversation(ctx, tx, conv); e != nil {
			return nil, e
		}
		for _, revocation := range v.Revocations {
			if revocation.Source == "" || revocation.Row < 1 || revocation.Before < 1 || revocation.Before > v.Revision {
				return nil, httpx.ErrInvalidRequest
			}
			// Process every cutoff even if a newer notice for another source arrived first.
			if _, e := tx.Exec(ctx, `INSERT INTO group_expert_binding_revocations VALUES($1,$2,$3,$4) ON CONFLICT(conversation_id,source_agent_id,source_row_id) DO UPDATE SET before_revision=GREATEST(group_expert_binding_revocations.before_revision,excluded.before_revision)`, conv, revocation.Source, revocation.Row, revocation.Before); e != nil {
				return nil, e
			}
			var id string
			e := tx.QueryRow(ctx, `SELECT b.binding_id FROM group_expert_bindings b JOIN group_expert_binding_revocations r USING(conversation_id,source_agent_id,source_row_id) WHERE b.conversation_id=$1 AND b.source_agent_id=$2 AND b.source_row_id=$3 AND b.state='active' AND b.scope_revision<r.before_revision`, conv, revocation.Source, revocation.Row).Scan(&id)
			if errors.Is(e, pgx.ErrNoRows) {
				continue
			}
			if e != nil {
				return nil, e
			}
			b, e := binding(ctx, tx, id)
			if e != nil {
				return nil, e
			}
			if e = removeBinding(ctx, tx, b, "invalidated"); e != nil {
				return nil, e
			}
		}
	}
	return map[string]any{"operation_id": v.Operation, "revision": v.Revision, "applied": true}, nil
}
func (s *Service) scope(ctx context.Context, tx pgx.Tx, r *http.Request, v request) (any, error) {
	conv := r.PathValue("id")
	if v.Revision < 1 || len(v.Sources) > 500 {
		return nil, httpx.ErrInvalidRequest
	}
	for _, source := range v.Sources {
		if source.Agent == "" || source.Row < 1 || strings.TrimSpace(source.Name) == "" {
			return nil, httpx.ErrInvalidRequest
		}
	}
	if v.Sources == nil {
		v.Sources = []Source{}
	}
	raw, _ := json.Marshal(v.Sources)
	identities := []map[string]any{}
	for _, source := range v.Sources {
		identities = append(identities, map[string]any{"source_agent_id": source.Agent, "source_row_id": source.Row, "authorization_versions": source.Versions})
	}
	fingerprint := hash(mustJSON(identities))
	// Lock authorizations before conversations, consistently with result commits.
	var high int64
	earlier := tx.QueryRow(ctx, `SELECT revision FROM group_expert_authorizations WHERE domain_key=$1`, "scope:"+conv).Scan(&high)
	if earlier != nil && !errors.Is(earlier, pgx.ErrNoRows) {
		return nil, earlier
	}
	if high > v.Revision {
		return map[string]any{"operation_id": v.Operation, "revision": high, "applied": true}, nil
	}
	if e := setAuthorization(ctx, tx, "scope:"+conv, v.Revision); e != nil {
		return nil, e
	}
	if _, e := store.LockConversation(ctx, tx, conv); e != nil {
		return nil, e
	}
	var rev int64
	var previous string
	e := tx.QueryRow(ctx, `SELECT revision,content_digest FROM group_expert_scopes WHERE conversation_id=$1`, conv).Scan(&rev, &previous)
	if e != nil && !errors.Is(e, pgx.ErrNoRows) {
		return nil, e
	}
	if rev == v.Revision && previous != fingerprint {
		return nil, httpx.ErrIdemConflict
	}
	if rev > v.Revision {
		return map[string]any{"operation_id": v.Operation, "revision": rev, "applied": true}, nil
	}
	if _, e = tx.Exec(ctx, `INSERT INTO group_expert_scopes(conversation_id,revision,content_digest,sources) VALUES($1,$2,$3,$4) ON CONFLICT(conversation_id) DO UPDATE SET revision=excluded.revision,content_digest=excluded.content_digest,sources=excluded.sources,updated_at=now()`, conv, v.Revision, fingerprint, raw); e != nil {
		return nil, e
	}
	rows, e := tx.Query(ctx, `SELECT binding_id FROM group_expert_bindings WHERE conversation_id=$1 AND state='active' ORDER BY binding_id`, conv)
	if e != nil {
		return nil, e
	}
	ids := []string{}
	for rows.Next() {
		var id string
		if e = rows.Scan(&id); e != nil {
			rows.Close()
			return nil, e
		}
		ids = append(ids, id)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	for _, id := range ids {
		b, e := binding(ctx, tx, id)
		if e != nil {
			return nil, e
		}
		var src *Source
		for i := range v.Sources {
			if v.Sources[i].Agent == b.Source && v.Sources[i].Row == b.Row {
				src = &v.Sources[i]
			}
		}
		if src == nil {
			if e = removeBinding(ctx, tx, b, "invalidated"); e != nil {
				return nil, e
			}
		} else {
			avatar, _ := json.Marshal(src.Avatar)
			if src.Avatar == nil {
				avatar = []byte("{}")
			}
			if _, e = tx.Exec(ctx, `UPDATE group_expert_bindings SET display_name=$2,avatar=$3 WHERE binding_id=$1`, id, src.Name, avatar); e != nil {
				return nil, e
			}
		}
	}
	e = notify(ctx, tx, conv, "group.update", conv, 0)
	return map[string]any{"operation_id": v.Operation, "revision": v.Revision, "applied": true}, e
}

func lockCall(ctx context.Context, tx pgx.Tx, id string) (Call, error) {
	var conv string
	var versions map[string]int64
	if e := tx.QueryRow(ctx, `SELECT conversation_id,authorization_versions FROM group_expert_calls WHERE call_id=$1`, id).Scan(&conv, &versions); e != nil {
		if errors.Is(e, pgx.ErrNoRows) {
			return Call{}, httpx.ErrNotFound
		}
		return Call{}, e
	}
	keys := []string{}
	for key := range versions {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		var version int64
		e := tx.QueryRow(ctx, `SELECT revision FROM group_expert_authorizations WHERE domain_key=$1 FOR SHARE`, key).Scan(&version)
		if e != nil && !errors.Is(e, pgx.ErrNoRows) {
			return Call{}, e
		}
	}
	if _, e := store.LockConversation(ctx, tx, conv); e != nil {
		return Call{}, e
	}
	return load(ctx, tx, id)
}
func identity(ctx context.Context, tx pgx.Tx, c Call, maxChars int) (map[string]any, error) {
	if c.MaxChars > 0 {
		maxChars = c.MaxChars
	}
	b, e := binding(ctx, tx, c.Binding)
	if e != nil {
		return nil, e
	}
	var uid int64
	parts := strings.Split(c.Caller, ":")
	if len(parts) != 3 || parts[0] != "octop" {
		return nil, httpx.ErrInvalidRequest
	}
	if _, e = fmt.Sscan(parts[2], &uid); e != nil {
		return nil, e
	}
	return map[string]any{"call_id": c.ID, "user_id": uid, "conversation_id": c.Conversation, "message_id": c.Message, "binding_id": c.Binding, "binding_generation": c.Generation, "binding_scope_revision": b.ScopeRevision, "source_agent_id": b.Source, "source_row_id": b.Row, "authorization_versions": c.Versions, "max_text_chars": maxChars}, nil
}
func (s *Service) call(ctx context.Context, tx pgx.Tx, r *http.Request, _ request) (any, error) {
	c, e := queryCall(ctx, tx, r.PathValue("call"), false)
	if e != nil {
		return nil, e
	}
	i, e := identity(ctx, tx, c, s.D.Cfg.Limits.MaxTextChars)
	return map[string]any{"call": c, "identity": i}, e
}
func (s *Service) context(ctx context.Context, tx pgx.Tx, r *http.Request, _ request) (any, error) {
	c, e := queryCall(ctx, tx, r.PathValue("call"), false)
	if e != nil {
		return nil, e
	}
	if terminal(c.State) || c.State == "cancel_requested" {
		return nil, failure("group_expert_cancelled")
	}
	if e = versionsMatch(ctx, tx, c); e != nil {
		return nil, e
	}
	out, e := contextFor(ctx, tx, c, false)
	if e != nil {
		return nil, e
	}
	out["identity"], e = identity(ctx, tx, c, s.D.Cfg.Limits.MaxTextChars)
	out["dispatched"] = c.Dispatch != nil
	return out, e
}
func versionsMatch(ctx context.Context, tx pgx.Tx, c Call) error {
	for domain, version := range c.Versions {
		var now int64
		if e := tx.QueryRow(ctx, `SELECT revision FROM group_expert_authorizations WHERE domain_key=$1`, domain).Scan(&now); e != nil {
			return failure("group_expert_authorization_syncing")
		}
		if now != version {
			return failure("group_expert_authorization_changed")
		}
	}
	return nil
}
func (s *Service) admission(ctx context.Context, tx pgx.Tx, r *http.Request, v request) (any, error) {
	c, e := lockCall(ctx, tx, r.PathValue("call"))
	if e != nil {
		return nil, e
	}
	if v.Admission != "accepted" && v.Admission != "rejected" && v.Admission != "cancelled" {
		return nil, httpx.ErrInvalidRequest
	}
	if v.Admission == "accepted" && v.Execution == "" {
		return nil, httpx.ErrInvalidRequest
	}
	var previous map[string]any
	if err := tx.QueryRow(ctx, `SELECT receipt FROM group_expert_outbox WHERE operation_id=$1`, "register:"+c.ID).Scan(&previous); err != nil {
		return nil, err
	}
	if previous != nil {
		var current map[string]any
		_ = json.Unmarshal(mustJSON(v), &current)
		if !bytes.Equal(mustJSON(previous), mustJSON(current)) {
			return nil, httpx.ErrIdemConflict
		}
		return map[string]any{"saved": true}, nil
	}
	if c.Execution != nil && *c.Execution != v.Execution {
		return nil, httpx.ErrIdemConflict
	}
	if c.Execution == nil && v.Execution != "" {
		if _, e = tx.Exec(ctx, `UPDATE group_expert_calls SET execution_id=$2 WHERE call_id=$1`, c.ID, v.Execution); e != nil {
			return nil, e
		}
	}
	if !terminal(c.State) && c.State != "cancel_requested" {
		if c.State == "pending_admission" {
			state := v.Admission
			if state == "accepted" {
				state = "queued"
			}
			if e = changed(ctx, tx, c, state, v.Reason); e != nil {
				return nil, e
			}
		}
	}
	if _, e = tx.Exec(ctx, `UPDATE group_expert_outbox SET acknowledged_at=now(),receipt=$2 WHERE operation_id=$1`, "register:"+c.ID, mustJSON(v)); e != nil {
		return nil, e
	}
	if e = advance(ctx, tx, c.Conversation); e != nil {
		return nil, e
	}
	return map[string]any{"saved": true}, nil
}
func mustJSON(v any) []byte { b, _ := json.Marshal(v); return b }
func (s *Service) awaitDelivery(ctx context.Context, tx pgx.Tx, r *http.Request, v request) (any, error) {
	c, e := lockCall(ctx, tx, r.PathValue("call"))
	if e != nil {
		return nil, e
	}
	if c.Execution == nil || *c.Execution != v.Execution || len(v.ResultDigest) != 64 || (c.Result != nil && *c.Result != v.ResultDigest) {
		return nil, httpx.ErrIdemConflict
	}
	if _, e = hex.DecodeString(v.ResultDigest); e != nil {
		return nil, httpx.ErrInvalidRequest
	}
	if _, e = tx.Exec(ctx, `UPDATE group_expert_calls SET result_digest=$2 WHERE call_id=$1`, c.ID, v.ResultDigest); e != nil {
		return nil, e
	}
	if c.State == "dispatched" {
		if e = changed(ctx, tx, c, "awaiting_delivery", ""); e != nil {
			return nil, e
		}
		c.State = "awaiting_delivery"
	}
	return map[string]any{"saved": true, "call_state": c.State}, nil
}
func (s *Service) receipt(ctx context.Context, tx pgx.Tx, r *http.Request, _ request) (any, error) {
	c, e := queryCall(ctx, tx, r.PathValue("call"), false)
	if e != nil {
		return nil, e
	}
	return map[string]any{"receipt": c.Receipt}, nil
}
func (s *Service) result(ctx context.Context, tx pgx.Tx, r *http.Request, v request) (any, error) {
	c, e := lockCall(ctx, tx, r.PathValue("call"))
	if e != nil {
		return nil, e
	}
	if len(v.ResultDigest) != 64 || (!v.Unpublished && hash([]byte(v.ResultWire)) != v.ResultDigest) {
		return nil, httpx.ErrInvalidRequest
	}
	if c.Result != nil && *c.Result != v.ResultDigest {
		return nil, httpx.ErrIdemConflict
	}
	if c.Receipt != nil {
		if c.Result == nil || *c.Result != v.ResultDigest {
			return nil, httpx.ErrIdemConflict
		}
		return c.Receipt, nil
	}
	var result struct {
		Call      string `json:"call_id"`
		Execution string `json:"execution_id"`
		State     string `json:"execution_state"`
		Reason    string `json:"reason_code"`
		Answer    *struct {
			Text        string   `json:"text"`
			Truncated   bool     `json:"truncated"`
			Incomplete  bool     `json:"incomplete"`
			Attachments []string `json:"attachments"`
		} `json:"result"`
	}
	if json.Unmarshal([]byte(v.ResultWire), &result) != nil || result.Call != c.ID || c.Execution == nil || result.Execution != *c.Execution || (result.State != "completed" && result.State != "failed" && result.State != "cancelled" && result.State != "unknown") {
		return nil, httpx.ErrIdemConflict
	}
	if v.Unpublished && result.Answer != nil {
		return nil, httpx.ErrInvalidRequest
	}
	state, reason, delivery := result.State, result.Reason, "confirmed"
	if terminal(c.State) || c.State == "cancel_requested" || v.Unpublished {
		state = c.State
		if !terminal(state) {
			state = "cancelled"
		}
		if result.State == "unknown" && !terminal(c.State) {
			state = "unknown"
		}
		if c.Reason != "" {
			reason = c.Reason
		}
		delivery = "unpublished"
	}
	receipt := map[string]any{"call_id": c.ID, "result_digest": v.ResultDigest}
	if state == "completed" && !terminal(c.State) && !v.Unpublished {
		if c.Dispatch == nil {
			return nil, failure("group_expert_not_dispatched")
		}
		e = versionsMatch(ctx, tx, c)
		if e == nil {
			_, e = contextFor(ctx, tx, c, true)
		}
		if e != nil {
			var he *httpx.Error
			if !errors.As(e, &he) {
				return nil, e
			}
			state, reason, delivery = "cancelled", he.Code, "unpublished"
		} else {
			maximum := c.MaxChars
			if maximum == 0 {
				maximum = s.D.Cfg.Limits.MaxTextChars
			}
			if result.Answer == nil || strings.TrimSpace(result.Answer.Text) == "" || len([]rune(result.Answer.Text)) > maximum {
				state, reason, delivery = "failed", "group_expert_result_invalid", "unpublished"
			} else if e = validateArtifacts(ctx, tx, c, result.Answer.Attachments); e != nil {
				var he *httpx.Error
				if !errors.As(e, &he) || he.Code == "group_expert_artifact_pending" {
					return nil, e
				}
				state, reason, delivery = "failed", he.Code, "unpublished"
			}
			if state == "completed" {
				seq, e := store.NextSeq(ctx, tx, c.Conversation)
				if e != nil {
					return nil, e
				}
				id := idgen.New("msg")
				if _, e = tx.Exec(ctx, `INSERT INTO messages(id,conversation_id,seq,sender_id,client_message_id,type,text,reply_to_message_id,expert_binding_id) VALUES($1,$2,$3,$4,$5,'text',$6,$7,$4)`, id, c.Conversation, seq, c.Binding, c.ID, result.Answer.Text, c.Message); e != nil {
					return nil, e
				}
				if e = attachArtifacts(ctx, tx, id, result.Answer.Attachments); e != nil {
					return nil, e
				}
				if e = notify(ctx, tx, c.Conversation, "message.new", id, seq); e != nil {
					return nil, e
				}
				receipt["message_id"], receipt["seq"] = id, store.BigStr(seq)
				if _, e = tx.Exec(ctx, `UPDATE group_expert_calls SET reply_message_id=$2 WHERE call_id=$1`, c.ID, id); e != nil {
					return nil, e
				}
			}
		}
	}
	receipt["call_state"], receipt["delivery_state"], receipt["reason_code"] = state, delivery, reason
	if _, e = tx.Exec(ctx, `UPDATE group_expert_calls SET result_digest=$2,receipt=$3 WHERE call_id=$1`, c.ID, v.ResultDigest, mustJSON(receipt)); e != nil {
		return nil, e
	}
	if e = changed(ctx, tx, c, state, reason); e != nil {
		return nil, e
	}
	if e = advance(ctx, tx, c.Conversation); e != nil {
		return nil, e
	}
	return receipt, nil
}
func (s *Service) pending(ctx context.Context, tx pgx.Tx, _ *http.Request, _ request) (any, error) {
	rows, e := tx.Query(ctx, `SELECT operation_id,kind,target_id,request_digest,payload FROM group_expert_outbox WHERE acknowledged_at IS NULL AND next_attempt_at<=now() AND kind IN ('register','dispatch','cancel') ORDER BY next_attempt_at,created_at,operation_id LIMIT 100`)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	items := []map[string]any{}
	for rows.Next() {
		var id, kind, target, digest string
		var body map[string]any
		if e = rows.Scan(&id, &kind, &target, &digest, &body); e != nil {
			return nil, e
		}
		items = append(items, map[string]any{"operation_id": id, "kind": kind, "target_id": target, "request_digest": digest, "payload": body})
	}
	return map[string]any{"items": items}, rows.Err()
}
func (s *Service) ack(ctx context.Context, tx pgx.Tx, r *http.Request, v request) (any, error) {
	var kind, target, digest string
	var old, body map[string]any
	if e := tx.QueryRow(ctx, `SELECT kind,target_id,request_digest,receipt,payload FROM group_expert_outbox WHERE operation_id=$1`, r.PathValue("operation")).Scan(&kind, &target, &digest, &old, &body); e != nil {
		return nil, e
	}
	if digest != v.Digest {
		return nil, httpx.ErrIdemConflict
	}
	if old != nil {
		if !bytes.Equal(mustJSON(old), mustJSON(v.Receipt)) {
			return nil, httpx.ErrIdemConflict
		}
		return old, nil
	}
	c, e := lockCall(ctx, tx, target)
	if e != nil {
		return nil, e
	}
	if e = tx.QueryRow(ctx, `SELECT receipt FROM group_expert_outbox WHERE operation_id=$1 FOR UPDATE`, r.PathValue("operation")).Scan(&old); e != nil {
		return nil, e
	}
	if old != nil {
		if !bytes.Equal(mustJSON(old), mustJSON(v.Receipt)) {
			return nil, httpx.ErrIdemConflict
		}
		return old, nil
	}
	if kind == "cancel" && c.State == "cancel_requested" {
		state, _ := v.Receipt["execution_state"].(string)
		stopped, _ := v.Receipt["stopped"].(bool)
		if state == "unknown" {
			e = changed(ctx, tx, c, "unknown", c.Reason)
		} else if stopped {
			e = changed(ctx, tx, c, "cancelled", c.Reason)
		} else {
			_, e = tx.Exec(ctx, `UPDATE group_expert_outbox SET next_attempt_at=now()+interval '1 second' WHERE operation_id=$1`, r.PathValue("operation"))
			return map[string]any{"saved": true, "stopped": false}, e
		}
		if e != nil {
			return nil, e
		}
	}
	if _, e = tx.Exec(ctx, `UPDATE group_expert_outbox SET acknowledged_at=now(),receipt=$2 WHERE operation_id=$1`, r.PathValue("operation"), mustJSON(v.Receipt)); e != nil {
		return nil, e
	}
	if e = advance(ctx, tx, c.Conversation); e != nil {
		return nil, e
	}
	return v.Receipt, nil
}
func (s *Service) retry(ctx context.Context, tx pgx.Tx, r *http.Request, v request) (any, error) {
	tag, e := tx.Exec(ctx, `UPDATE group_expert_outbox SET attempts=attempts+1,next_attempt_at=now()+make_interval(secs=>LEAST(3600,2^LEAST(attempts+1,12))) WHERE operation_id=$1 AND request_digest=$2 AND acknowledged_at IS NULL`, r.PathValue("operation"), v.Digest)
	if e == nil && tag.RowsAffected() == 0 {
		return nil, httpx.ErrIdemConflict
	}
	return map[string]bool{"saved": true}, e
}
func (s *Service) visibleCall(w http.ResponseWriter, r *http.Request) {
	a, _ := auth.From(r)
	var out any
	e := s.D.Store.Tx(r.Context(), func(tx pgx.Tx) error {
		c, e := queryCall(r.Context(), tx, r.PathValue("call"), false)
		if e != nil {
			return e
		}
		if c.Conversation != r.PathValue("id") {
			return httpx.ErrNotFound
		}
		var ok bool
		if e = tx.QueryRow(r.Context(), `SELECT $3>COALESCE((SELECT cleared_through_seq FROM conversation_user_state WHERE conversation_id=$1 AND user_id=$2),0) AND EXISTS(SELECT 1 FROM member_epochs WHERE conversation_id=$1 AND user_id=$2 AND $3 BETWEEN visible_from_seq AND COALESCE(visible_through_seq,9223372036854775807))`, c.Conversation, a.UserID, c.Seq).Scan(&ok); e != nil {
			return e
		}
		if !ok {
			return httpx.ErrNotFound
		}
		out = map[string]any{"call_id": c.ID, "call_state": c.State, "reason_code": c.Reason, "message_id": c.Message}
		return nil
	})
	if e != nil {
		httpx.WriteErrorOf(w, r, e)
		return
	}
	httpx.WriteData(w, r, 200, out)
}
