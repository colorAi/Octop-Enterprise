package companyprojects

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func testBrowser(t *testing.T, handler http.HandlerFunc) *Browser {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	browser, err := NewBrowser(server.URL, "octop-device.test", strings.Repeat("i", 43))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(browser.Close)
	return browser
}

func TestInstallationIdentitySurvivesBrowsingAndWorkerRestarts(t *testing.T) {
	state := filepath.Join(t.TempDir(), "account")
	first, err := InstallationToken(state)
	if err != nil || len(first) != 43 {
		t.Fatal("could not create worker-compatible installation identity", err)
	}
	second, err := InstallationToken(state)
	if err != nil || second != first {
		t.Fatal("browsing replaced the installation identity", err)
	}
	other, err := InstallationToken(t.TempDir())
	if err != nil || other == first {
		t.Fatal("accounts share an installation identity", err)
	}
	path := filepath.Join(state, "installation.json")
	existing := `{"token":"` + strings.Repeat("legacy", 8) + `"}`
	if err := os.WriteFile(path, []byte(existing), 0600); err != nil {
		t.Fatal(err)
	}
	if token, err := InstallationToken(state); err != nil || token != strings.Repeat("legacy", 8) {
		t.Fatal("did not reuse the Python worker's existing token", err)
	}
	if err := os.WriteFile(path, []byte(`{"token":`), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := InstallationToken(state); err == nil {
		t.Fatal("silently replaced a damaged installation identity")
	}
	if data, _ := os.ReadFile(path); string(data) != `{"token":` {
		t.Fatal("damaged installation record was overwritten")
	}
}

func TestBrowserJoinsStartupReadsButRefreshesFinishedLists(t *testing.T) {
	var capabilities, registrations, queries atomic.Int32
	entered, release := make(chan struct{}), make(chan struct{})
	browser := testBrowser(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Octop-Device-Credential") != "octop-device.test" || r.Header.Get("Origin") != "" || r.URL.RawQuery != "" {
			t.Error("invalid native credential transport")
		}
		if r.URL.Path == "/api/company-runtime/capabilities" {
			capabilities.Add(1)
			_, _ = w.Write([]byte(`{"protocol_version":1,"features":["checkpoint_batches","multi_project_v1"]}`))
			return
		}
		var body map[string]any
		if json.NewDecoder(r.Body).Decode(&body) != nil || body["installation_token"] != strings.Repeat("i", 43) {
			t.Error("missing installation ownership")
		}
		switch r.URL.Path {
		case "/api/company-runtime/projects":
			registrations.Add(1)
			_, _ = w.Write([]byte(`{}`))
		case "/api/company-runtime/projects/A/threads/query":
			if body["limit"] != float64(50) || body["offset"] != float64(0) {
				t.Error("lost bounded list pagination")
			}
			if queries.Add(1) == 1 {
				close(entered)
				select {
				case <-release:
				case <-r.Context().Done():
					return
				}
			}
			_, _ = w.Write([]byte(`{"items":[],"has_more":false}`))
		default:
			t.Error("unexpected browser operation", r.URL.Path)
		}
	})
	ctx := context.Background()
	project := Project{ID: "A", Name: "Project A"}
	for range 2 {
		if supported, err := browser.Supported(ctx); err != nil || !supported {
			t.Fatal(supported, err)
		}
		if _, err := browser.Project(ctx, "register", project, nil); err != nil {
			t.Fatal(err)
		}
	}
	done := make(chan error, 1)
	go func() {
		_, err := browser.Project(ctx, "threads", project, map[string]any{"offset": 0})
		done <- err
	}()
	<-entered
	joining, cancel := context.WithTimeout(ctx, 25*time.Millisecond)
	defer cancel()
	_, err := browser.Project(joining, "threads", project, map[string]any{"offset": 0})
	if !errors.Is(err, context.DeadlineExceeded) || queries.Load() != 1 {
		t.Fatal("duplicate startup request was not joined", err, queries.Load())
	}
	close(release)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if _, err := browser.Project(ctx, "threads", project, map[string]any{"offset": 0}); err != nil {
		t.Fatal(err)
	}
	if capabilities.Load() != 1 || registrations.Load() != 1 || queries.Load() != 2 {
		t.Fatal("cached a list or repeated initialization", capabilities.Load(), registrations.Load(), queries.Load())
	}
	if _, err := browser.Project(ctx, "start", project, nil); err == nil {
		t.Fatal("browser exposed task execution")
	}
	if _, err := browser.Project(ctx, "continue", project, nil); err == nil || err.Error() != "history_continuation_server_required" {
		t.Fatal("continuation did not identify an older server", err)
	}
}

func TestBrowserRejectsRedirectsAndRetriesFailedAuthorization(t *testing.T) {
	var attempts atomic.Int32
	browser := testBrowser(t, func(w http.ResponseWriter, r *http.Request) {
		if attempts.Add(1) == 1 {
			w.WriteHeader(http.StatusForbidden)
			_, _ = w.Write([]byte(`{"error":{"details":{"reason":"device_revoked"}}}`))
			return
		}
		_, _ = w.Write([]byte(`{"protocol_version":1,"features":["checkpoint_batches"]}`))
	})
	if _, err := browser.Supported(context.Background()); err == nil || err.Error() != "device_revoked" {
		t.Fatal("lost the company's authorization error", err)
	}
	if supported, err := browser.Supported(context.Background()); err != nil || supported || attempts.Load() != 2 {
		t.Fatal("failed negotiation was cached or unsupported server accepted", supported, err)
	}
	var redirected atomic.Bool
	target := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { redirected.Store(true) }))
	defer target.Close()
	redirect := testBrowser(t, func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	})
	if _, err := redirect.Supported(context.Background()); err == nil || redirected.Load() {
		t.Fatal("redirected native credentials to another origin", err)
	}
}

func TestBrowserCloseCancelsOutstandingReads(t *testing.T) {
	entered := make(chan struct{})
	browser := testBrowser(t, func(w http.ResponseWriter, r *http.Request) {
		close(entered)
		<-r.Context().Done()
	})
	done := make(chan error, 1)
	go func() { _, err := browser.Supported(context.Background()); done <- err }()
	<-entered
	browser.Close()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("closed account returned a successful read")
		}
	case <-time.After(time.Second):
		t.Fatal("switching accounts did not stop the old request")
	}
}
