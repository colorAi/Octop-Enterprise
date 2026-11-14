package companyprojects

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"sync"
	"time"

	"octop.desktop/internal/company"
)

// InstallationToken shares the worker's account identity. Call with the native
// session lock, before either browsing projects or starting the Python worker.
// Never rotate an unreadable identity: existing conversations belong to it.
func InstallationToken(state string) (string, error) {
	path := filepath.Join(state, "installation.json")
	data, err := os.ReadFile(path)
	if err == nil {
		var saved struct {
			Token string `json:"token"`
		}
		if json.Unmarshal(data, &saved) != nil || len(saved.Token) < 32 || len(saved.Token) > 256 {
			return "", errors.New("project_installation_invalid")
		}
		return saved.Token, nil
	}
	if !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	var secret [32]byte
	if _, err := rand.Read(secret[:]); err != nil {
		return "", err
	}
	token := base64.RawURLEncoding.EncodeToString(secret[:])
	data, _ = json.Marshal(map[string]string{"token": token})
	if err := os.MkdirAll(state, 0700); err != nil {
		return "", err
	}
	file, err := os.CreateTemp(state, ".installation-*")
	if err != nil {
		return "", err
	}
	defer os.Remove(file.Name())
	defer file.Close()
	if _, err := file.Write(data); err != nil {
		return "", err
	}
	if err := file.Sync(); err != nil {
		return "", err
	}
	if err := file.Close(); err != nil {
		return "", err
	}
	if err := os.Rename(file.Name(), path); err != nil {
		return "", err
	}
	return token, nil
}

type browserRequest struct {
	done chan struct{}
	data json.RawMessage
	err  error
}

// Browser reads account project metadata without booting the execution engine.
// Credentials remain native-only. The installation token proves legacy receipts.
type Browser struct {
	base, credential, installation string
	http                           *http.Client
	ctx                            context.Context
	cancel                         context.CancelFunc
	mu                             sync.Mutex
	pending                        map[string]*browserRequest
	completed                      map[string]json.RawMessage
}

func NewBrowser(base, credential, installation string) (*Browser, error) {
	base, err := company.BaseURL(base)
	if err != nil {
		return nil, err
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil // Match the worker's trust_env=False transport.
	ctx, cancel := context.WithCancel(context.Background())
	return &Browser{base: base, credential: credential, installation: installation,
		http: &http.Client{Transport: transport, Timeout: 15 * time.Second,
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }},
		ctx: ctx, cancel: cancel, pending: map[string]*browserRequest{}, completed: map[string]json.RawMessage{}}, nil
}

func (b *Browser) Close() {
	b.cancel()
	b.http.CloseIdleConnections()
}

func (b *Browser) features(ctx context.Context) ([]string, error) {
	data, err := b.request(ctx, "capabilities", nil, true)
	if err != nil {
		return nil, err
	}
	var capabilities struct {
		Protocol int      `json:"protocol_version"`
		Features []string `json:"features"`
	}
	if json.Unmarshal(data, &capabilities) != nil || capabilities.Protocol != 1 || !slices.Contains(capabilities.Features, "checkpoint_batches") {
		return nil, errors.New("company_protocol_incompatible")
	}
	return capabilities.Features, nil
}

func (b *Browser) Supported(ctx context.Context) (bool, error) {
	features, err := b.features(ctx)
	return slices.Contains(features, "multi_project_v1"), err
}

func (b *Browser) RequireAccountProjects(ctx context.Context) error {
	features, err := b.features(ctx)
	if err != nil {
		return err
	}
	if !slices.Contains(features, "account_projects_v1") {
		return errors.New("project_sync_server_required")
	}
	return nil
}

type AccountProject struct {
	ID   string `json:"project_id"`
	Name string `json:"name"`
}

func (b *Browser) AccountProjects(ctx context.Context) ([]AccountProject, error) {
	if err := b.RequireAccountProjects(ctx); err != nil {
		return nil, err
	}
	projects := []AccountProject{}
	const limit = 100
	for offset := 0; ; offset += limit {
		body, err := json.Marshal(map[string]any{"installation_token": b.installation, "limit": limit, "offset": offset})
		if err != nil {
			return nil, err
		}
		raw, err := b.request(ctx, "projects/query", body, false)
		if err != nil {
			return nil, err
		}
		var page struct {
			Items []AccountProject `json:"items"`
			More  bool             `json:"has_more"`
		}
		if json.Unmarshal(raw, &page) != nil || (page.More && len(page.Items) != limit) {
			return nil, errors.New("invalid_project_response")
		}
		projects = append(projects, page.Items...)
		if !page.More {
			return projects, nil
		}
	}
}

// AdoptLegacyConversations binds pre-project conversations recorded on this installation.
func (b *Browser) AdoptLegacyConversations(ctx context.Context, project Project, bindings map[string]string) error {
	if !project.Legacy {
		return nil
	}
	var threads []string
	for thread, id := range bindings {
		if id == project.ID {
			threads = append(threads, thread)
		}
	}
	slices.Sort(threads)
	for _, thread := range threads {
		if _, err := b.Project(ctx, "adopt", project, map[string]any{"thread_id": thread}); err != nil {
			return err
		}
	}
	return nil
}

func (b *Browser) ProjectConversations(ctx context.Context, project Project, bindings map[string]string) (string, []string, error) {
	raw, err := b.Project(ctx, "register", project, map[string]any{"refresh": true})
	if err != nil {
		return "", nil, err
	}
	var current AccountProject
	if json.Unmarshal(raw, &current) != nil || current.ID != project.ID || current.Name == "" {
		return "", nil, errors.New("invalid_project_response")
	}
	if err := b.AdoptLegacyConversations(ctx, project, bindings); err != nil {
		return "", nil, err
	}
	var threads []string
	for after := ""; ; {
		raw, err := b.Project(ctx, "threads", project, map[string]any{"after_thread_id": after})
		if err != nil {
			return "", nil, err
		}
		var page struct {
			Items []struct {
				Thread  string `json:"thread_id"`
				Project string `json:"project_id"`
			} `json:"items"`
			More bool `json:"has_more"`
		}
		if json.Unmarshal(raw, &page) != nil {
			return "", nil, errors.New("invalid_project_response")
		}
		for _, item := range page.Items {
			if !archiveID.MatchString(item.Thread) || item.Project != project.ID {
				return "", nil, errors.New("invalid_project_response")
			}
			threads = append(threads, item.Thread)
		}
		if !page.More {
			return current.Name, threads, nil
		}
		if len(page.Items) == 0 || page.Items[len(page.Items)-1].Thread == after {
			return "", nil, errors.New("invalid_project_response")
		}
		after = page.Items[len(page.Items)-1].Thread
	}
}

func (b *Browser) Project(ctx context.Context, operation string, project Project, extra map[string]any) (json.RawMessage, error) {
	body := map[string]any{"installation_token": b.installation}
	path := "projects/" + url.PathEscape(project.ID)
	switch operation {
	case "register":
		path = "projects"
		body["project_id"], body["name"] = project.ID, project.Name
	case "adopt":
		path += "/adopt"
		body["thread_id"] = extra["thread_id"]
	case "threads":
		path += "/threads/query"
		body["limit"] = 50
		if after, ok := extra["after_thread_id"]; ok {
			body["limit"], body["after_thread_id"] = 100, after
		} else {
			body["offset"] = extra["offset"]
		}
	case "continue":
		features, err := b.features(ctx)
		if err != nil {
			return nil, err
		}
		if !slices.Contains(features, "history_continuation_v1") {
			return nil, errors.New("history_continuation_server_required")
		}
		path += "/continue"
		for _, key := range []string{"source_thread_id", "thread_id", "agent_id"} {
			body[key] = extra[key]
		}
	default:
		return nil, errors.New("invalid_project_operation")
	}
	data, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	return b.request(ctx, path, data, (operation == "register" && extra["refresh"] != true) || operation == "adopt")
}

// Join simultaneous startup queries. Cache successful capability negotiation and
// idempotent registration/adoption for this login only; lists are always fresh.
func (b *Browser) request(ctx context.Context, path string, body []byte, remember bool) (json.RawMessage, error) {
	if err := b.ctx.Err(); err != nil {
		return nil, err
	}
	key := path + "\x00" + string(body)
	b.mu.Lock()
	if result, ok := b.completed[key]; remember && ok {
		b.mu.Unlock()
		return result, nil
	}
	if pending := b.pending[key]; pending != nil {
		b.mu.Unlock()
		select {
		case <-pending.done:
			return pending.data, pending.err
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-b.ctx.Done():
			return nil, b.ctx.Err()
		}
	}
	pending := &browserRequest{done: make(chan struct{})}
	b.pending[key] = pending
	b.mu.Unlock()
	pending.data, pending.err = b.fetch(ctx, path, body)
	b.mu.Lock()
	delete(b.pending, key)
	if remember && pending.err == nil {
		b.completed[key] = pending.data
	}
	close(pending.done)
	b.mu.Unlock()
	return pending.data, pending.err
}

func (b *Browser) fetch(ctx context.Context, path string, body []byte) (json.RawMessage, error) {
	ctx, cancel := context.WithCancel(ctx)
	stop := context.AfterFunc(b.ctx, cancel)
	defer stop()
	defer cancel()
	method := http.MethodGet
	if body != nil {
		method = http.MethodPost
	}
	req, err := http.NewRequestWithContext(ctx, method, b.base+"/api/company-runtime/"+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-Octop-Device-Credential", b.credential)
	req.Header.Set("Content-Type", "application/json")
	response, err := b.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, (2<<20)+1))
	if err != nil || len(data) > 2<<20 || !json.Valid(data) {
		return nil, errors.New("invalid_project_response")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		var envelope struct {
			Error struct {
				Code    string `json:"code"`
				Details struct {
					Reason string `json:"reason"`
				} `json:"details"`
			} `json:"error"`
		}
		_ = json.Unmarshal(data, &envelope)
		if envelope.Error.Details.Reason != "" {
			return nil, errors.New(envelope.Error.Details.Reason)
		}
		if envelope.Error.Code != "" {
			return nil, errors.New(envelope.Error.Code)
		}
		return nil, fmt.Errorf("company_service_%d", response.StatusCode)
	}
	return data, nil
}
