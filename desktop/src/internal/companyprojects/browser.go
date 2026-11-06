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

// Browser reads project metadata without booting the execution engine. Both
// credentials remain native-only; the server enforces the same installation
// ownership as the Python worker. No task-execution endpoints are available.
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

func (b *Browser) Supported(ctx context.Context) (bool, error) {
	data, err := b.request(ctx, "capabilities", nil, true)
	if err != nil {
		return false, err
	}
	var capabilities struct {
		Protocol int      `json:"protocol_version"`
		Features []string `json:"features"`
	}
	if json.Unmarshal(data, &capabilities) != nil || capabilities.Protocol != 1 || !slices.Contains(capabilities.Features, "checkpoint_batches") {
		return false, errors.New("company_protocol_incompatible")
	}
	return slices.Contains(capabilities.Features, "multi_project_v1"), nil
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
		body["limit"], body["offset"] = 50, extra["offset"]
	case "continue":
		capabilities, err := b.request(ctx, "capabilities", nil, true)
		if err != nil {
			return nil, err
		}
		var support struct {
			Features []string `json:"features"`
		}
		if json.Unmarshal(capabilities, &support) != nil || !slices.Contains(support.Features, "history_continuation_v1") {
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
	return b.request(ctx, path, data, operation == "register" || operation == "adopt")
}

// Join simultaneous startup queries. Cache successful capability negotiation and
// idempotent registration/adoption for this login only; lists are always fresh.
func (b *Browser) request(ctx context.Context, path string, body []byte, remember bool) (json.RawMessage, error) {
	if err := b.ctx.Err(); err != nil {
		return nil, err
	}
	key := path + "\x00" + string(body)
	b.mu.Lock()
	if result, ok := b.completed[key]; ok {
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
