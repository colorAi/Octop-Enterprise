package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"octop.desktop/internal/companyprojects"
)

func TestProjectBrowserDiscardsRepliesAfterAccountChange(t *testing.T) {
	entered := make(chan struct{})
	app := startupTestApp(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(entered)
		<-r.Context().Done()
	}))
	done := make(chan error, 1)
	go func() {
		_, err := app.performProjectAction("nonce", app.companyURL, "project_status", json.RawMessage(`{}`))
		done <- err
	}()
	<-entered
	app.companyMu.Lock()
	app.stopCompanyRuntimeLocked()
	app.workspaceNonce = "new-account"
	app.companyMu.Unlock()
	select {
	case err := <-done:
		if err == nil || err.Error() != "session_changed" {
			t.Fatal("old capability response survived the account switch", err)
		}
	case <-time.After(time.Second):
		t.Fatal("old project request was not cancelled")
	}
	if app.projectBrowser != nil || app.projectStore != nil || app.projectSupported {
		t.Fatal("old browser state leaked into the new account")
	}
}

func TestProjectBrowsingDoesNotWaitForToolStartup(t *testing.T) {
	var projectID, installation string
	app := startupTestApp(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Octop-Device-Credential") != "octop-device.test" {
			t.Error("missing device credential")
		}
		if r.URL.Path == "/api/company-runtime/capabilities" {
			_, _ = w.Write([]byte(`{"protocol_version":1,"features":["checkpoint_batches","multi_project_v1","history_continuation_v1"]}`))
			return
		}
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body["installation_token"] != installation {
			t.Error("project browser did not reuse the worker's account identity")
		}
		switch r.URL.Path {
		case "/api/company-runtime/projects":
			_, _ = w.Write([]byte(`{}`))
		case "/api/company-runtime/projects/" + projectID + "/threads/query":
			_ = json.NewEncoder(w).Encode(map[string]any{"items": []map[string]string{{"thread_id": "old-chat", "project_id": projectID, "agent_id": "writer"}}, "has_more": false})
		case "/api/company-runtime/projects/" + projectID + "/continue":
			if body["source_thread_id"] != "server-chat" || body["agent_id"] != "writer" {
				t.Error("continuation lost source or expert", body)
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"thread_id": "continued", "agent_id": "writer", "project_id": projectID, "copied_messages": 42})
		default:
			t.Error("unexpected request", r.URL.Path)
		}
	}))
	defer func() { app.companyMu.Lock(); app.stopCompanyRuntimeLocked(); app.companyMu.Unlock() }()
	store, err := app.projectStoreLocked(app.companyURL)
	if err != nil {
		t.Fatal(err)
	}
	project, err := store.Register(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	projectID = project.ID
	if err := os.WriteFile(filepath.Join(project.Path, "notes.txt"), []byte("existing work"), 0600); err != nil {
		t.Fatal(err)
	}
	installation, err = companyprojects.InstallationToken(companyStateDir(app.companyURL, 7))
	if err != nil {
		t.Fatal(err)
	}
	entered, release, stopped := make(chan struct{}), make(chan struct{}), make(chan struct{})
	go func() {
		defer close(stopped)
		_, _ = app.ensureRuntimeProcess("nonce", app.companyURL, "status", func() (string, error) {
			close(entered)
			<-release
			return "", errors.New("tools are still preparing")
		})
	}()
	<-entered
	defer func() { close(release); <-stopped }()
	for _, method := range []string{"project_status", "project_files", "project_read", "project_threads", "project_select", "project_continue"} {
		params, _ := json.Marshal(map[string]any{"project_id": projectID, "path": "notes.txt"})
		if method == "project_files" {
			params, _ = json.Marshal(map[string]any{"project_id": projectID})
		}
		if method == "project_continue" {
			params, _ = json.Marshal(map[string]string{"project_id": projectID, "source_thread_id": "server-chat", "thread_id": "continued", "agent_id": "writer"})
		}
		read := make(chan error, 1)
		go func() {
			_, err := app.performProjectAction("nonce", app.companyURL, method, params)
			read <- err
		}()
		select {
		case err := <-read:
			if err != nil {
				t.Fatalf("%s failed while tools were blocked: %v", method, err)
			}
		case <-time.After(2 * time.Second):
			t.Fatalf("%s waited for Python initialization", method)
		}
	}
	if store.Snapshot().Threads["old-chat"] != projectID || store.Snapshot().Threads["continued"] != projectID || app.companyRuntime != nil {
		t.Fatal("lost conversation binding or started a worker for browsing")
	}
	if err := store.Update(projectID, "", false, true); err != nil {
		t.Fatal(err)
	}
	params, _ := json.Marshal(map[string]string{"project_id": projectID})
	if _, err := app.performProjectAction("nonce", app.companyURL, "project_files", params); err == nil {
		t.Fatal("fast browsing bypassed revoked directory authorization")
	}
	params, _ = json.Marshal(map[string]string{"project_id": projectID, "source_thread_id": "server-chat", "thread_id": "denied", "agent_id": "writer"})
	if _, err := app.performProjectAction("nonce", app.companyURL, "project_continue", params); err == nil {
		t.Fatal("continuation bypassed revoked directory authorization")
	}
	if _, err := app.performProjectAction("other", app.companyURL, "project_status", json.RawMessage(`{}`)); err == nil {
		t.Fatal("fast browsing accepted another login's nonce")
	}
}

func TestProjectMigrationUsesLatestReceiptAndOriginalConsent(t *testing.T) {
	state := t.TempDir()
	original := t.TempDir()
	outside := t.TempDir()
	first := filepath.Join(original, "old")
	latest := filepath.Join(original, "latest")
	for _, p := range []string{first, latest} {
		if err := os.Mkdir(p, 0700); err != nil {
			t.Fatal(err)
		}
	}
	store, err := companyprojects.Open(filepath.Join(state, "projects.json"))
	if err != nil {
		t.Fatal(err)
	}
	write := func(task, thread, path string, started int) {
		dir := filepath.Join(state, "tasks", task)
		_ = os.MkdirAll(dir, 0700)
		data, _ := json.Marshal(map[string]any{"thread_id": thread, "project_path": path, "started_at_ns": started})
		if err := os.WriteFile(filepath.Join(dir, "task.json"), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	write("z-old", "chat", first, 1)
	write("a-new", "chat", latest, 2)
	write("other", "outside", outside, 3)
	for i := 0; i < 2; i++ {
		if err := migrateProjectReceipts(store, state, original, true); err != nil {
			t.Fatal(err)
		}
	}
	p, err := store.ForThread("chat")
	if err != nil || p.Path != func() string { value, _ := filepath.EvalSymlinks(latest); return value }() {
		t.Fatal(p, err)
	}
	if _, err := store.ForThread("outside"); err == nil {
		t.Fatal("migration expanded old consent")
	}
	if store.Snapshot().Current != "" {
		t.Fatal("migration selected a default project")
	}
	if len(store.Snapshot().Projects) != 2 {
		t.Fatal("duplicate migration")
	}
	if err := store.Update(p.ID, "", false, true); err != nil {
		t.Fatal(err)
	}
	if err := migrateProjectReceipts(store, state, original, true); err != nil {
		t.Fatal(err)
	}
	if _, err := store.ForThread("chat"); err == nil {
		t.Fatal("migration restored an explicitly revoked grant")
	}
}

func TestProjectNativeStartAndFilesRemainBoundAfterSelection(t *testing.T) {
	app := startupTestApp(t)
	app.projectSupported = true
	store, err := app.projectStoreLocked(app.companyURL)
	if err != nil {
		t.Fatal(err)
	}
	a, err := store.Register(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	b, err := store.Register(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if err = store.Bind("thread-a", a.ID); err != nil {
		t.Fatal(err)
	}
	if err = store.Bind("thr_target", b.ID); err != nil {
		t.Fatal(err)
	}
	epoch := app.runtimeEpoch
	data, err := app.projectStartParamsLocked("nonce", app.companyURL, json.RawMessage(`{"thread_id":"thread-a","project_id":"forged","project_root":"/arbitrary"}`))
	if err != nil {
		t.Fatal(err)
	}
	var params map[string]any
	_ = json.Unmarshal(data, &params)
	if params["project_id"] != a.ID || params["project_root"] != a.Path || app.runtimeEpoch != epoch {
		t.Fatal(params)
	}
	if _, err = app.projectStartParamsLocked("nonce", app.companyURL, json.RawMessage(`{"thread_id":"missing"}`)); err == nil {
		t.Fatal("unbound thread used current project")
	}
	original := filepath.Join(a.Path, "source.txt")
	_ = os.WriteFile(original, []byte("source"), 0600)
	content, err := companyprojects.ReadFile(a.Path, "source.txt")
	if err != nil {
		t.Fatal(err)
	}
	scope, err := app.artifactRootLocked(original)
	if err != nil || scope != a.Path {
		t.Fatal(scope, err)
	}
	result, err := app.transferRuntimeArtifact("nonce", app.companyURL, "import_attachment", forwardParams(t, map[string]string{"path": original, "sha256": content.SHA256}))
	if err != nil {
		t.Fatal(err)
	}
	file := result.(runtimeForwardFile)
	if !strings.HasPrefix(file.Path, filepath.Join(b.Path, "inbound")+string(os.PathSeparator)) {
		t.Fatal("attachment copied to wrong root", file.Path)
	}
	if _, err = os.Stat(original); err != nil {
		t.Fatal("source file moved", err)
	}
	if err = store.Update(a.ID, "", false, true); err != nil {
		t.Fatal(err)
	}
	if _, err = app.artifactRootLocked(original); err == nil {
		t.Fatal("revoked project readable")
	}
	if app.runtimePreviewScopeCurrent("nonce", app.companyURL, a.Path, epoch) {
		t.Fatal("stale authorization")
	}
	if !app.runtimePreviewScopeCurrent("nonce", app.companyURL, b.Path, epoch) {
		t.Fatal("other project lost authorization")
	}
}

func TestProjectRelocationAndRevocationRejectActiveOverlappingRoots(t *testing.T) {
	root := t.TempDir()
	child := filepath.Join(root, "child")
	if err := os.Mkdir(child, 0700); err != nil {
		t.Fatal(err)
	}
	catalog := companyprojects.Catalog{Projects: map[string]companyprojects.Project{"A": {ID: "A", Path: root}}, Threads: map[string]string{"thread-a": "A"}}
	for _, status := range []string{"running", "cancel_requested", "unknown"} {
		raw, _ := json.Marshal(map[string]any{"active_tasks": []map[string]string{{"thread_id": "thread-a", "status": status}}})
		if err := checkProjectIdle(raw, catalog, child); err == nil {
			t.Fatal("allowed relocation while", status)
		}
	}
	if err := checkProjectIdle(json.RawMessage(`{"active_tasks":[{"thread_id":"thread-a","status":"completed"}]}`), catalog, child); err != nil {
		t.Fatal(err)
	}
}
