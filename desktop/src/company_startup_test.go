package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
	"octop.desktop/internal/company"
)

func startupTestApp(t *testing.T) *App {
	t.Helper()
	t.Setenv("OCTOP_HOME", t.TempDir())
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer conn.CloseNow()
		var hello map[string]any
		if wsjson.Read(r.Context(), conn, &hello) != nil {
			return
		}
		if wsjson.Write(r.Context(), conn, map[string]any{"type": "ready", "device_id": strings.Repeat("b", 32), "owner_user_id": 7, "protocol_version": 2, "heartbeat_seconds": 15, "expires_at": time.Now().Add(time.Hour).Unix(), "credential": "octop-device.test"}) != nil {
			return
		}
		for {
			var frame map[string]any
			if wsjson.Read(r.Context(), conn, &frame) != nil {
				return
			}
		}
	}))
	client := company.NewClient()
	t.Cleanup(func() { client.Close(false); server.Close() })
	if err := client.Pair(context.Background(), server.URL, "octop-pair.test", "test", runtime.GOOS); err != nil {
		t.Fatal(err)
	}
	return &App{company: client, companyURL: server.URL, workspaceNonce: "nonce", workspacePath: t.TempDir(), workspaceGrant: &company.Grant{}}
}

func TestRuntimePreparationDoesNotBlockImagePreview(t *testing.T) {
	a := startupTestApp(t)
	path, digest := previewFixture(t, a.workspacePath, "image.png", previewPNG(t, 8, 8))
	entered, release, done := make(chan struct{}), make(chan struct{}), make(chan error, 1)
	defer close(release)
	go func() {
		_, err := a.ensureRuntimeProcess("nonce", a.companyURL, "status", func() (string, error) { close(entered); <-release; return "", errors.New("test preparation stopped") })
		done <- err
	}()
	<-entered
	preview := make(chan error, 1)
	params, _ := json.Marshal(map[string]string{"path": path, "sha256": digest})
	go func() {
		_, err := a.performRuntimeAction("nonce", a.companyURL, "preview_image", params)
		preview <- err
	}()
	select {
	case err := <-preview:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("image preview blocked behind runtime verification")
	}
}

func TestRuntimePreparationDiscardsRevokedDirectory(t *testing.T) {
	a := startupTestApp(t)
	entered, release, done := make(chan struct{}), make(chan struct{}), make(chan error, 1)
	base := a.companyURL
	root := t.TempDir()
	go func() {
		_, err := a.ensureRuntimeProcess("nonce", base, "status", func() (string, error) { close(entered); <-release; return root, nil })
		done <- err
	}()
	<-entered
	a.companyMu.Lock()
	a.stopCompanyRuntimeLocked()
	a.workspaceGrant = nil
	a.companyMu.Unlock()
	close(release)
	select {
	case err := <-done:
		if err == nil || !strings.Contains(err.Error(), "启动已取消") {
			t.Fatalf("stale preparation was not cancelled: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("revoked preparation did not finish")
	}
	if a.companyRuntime != nil {
		t.Fatal("revoked account started a worker")
	}
}

func validRuntimeFixture(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	names := []string{"launch.py", "packages/octop/company_runtime.py", "packages/octop_harness/__init__.py", "packages/openpyxl/__init__.py", "packages/docx/__init__.py", "packages/pptx/__init__.py", "packages/pypdf/__init__.py", "packages/PIL/__init__.py", "packages/pandas/__init__.py"}
	py, _ := filepath.Rel(root, pythonExe(root))
	names = append(names, filepath.ToSlash(py))
	suffix := ""
	if runtime.GOOS == "windows" {
		suffix = ".exe"
	}
	names = append(names, "tools/ffmpeg"+suffix, "tools/ffprobe"+suffix)
	manifest := companyManifest{Protocol: 1, Platform: runtime.GOOS, Arch: runtime.GOARCH, Files: map[string]string{}}
	for _, name := range names {
		p := filepath.Join(root, name)
		os.MkdirAll(filepath.Dir(p), 0700)
		data := []byte(strings.Repeat("x", 2048))
		if err := os.WriteFile(p, data, 0755); err != nil {
			t.Fatal(err)
		}
		h := sha256.Sum256(data)
		manifest.Files[name] = hex.EncodeToString(h[:])
	}
	data, _ := json.Marshal(manifest)
	if err := os.WriteFile(filepath.Join(root, "company-runtime-manifest.json"), data, 0600); err != nil {
		t.Fatal(err)
	}
	return root
}

func TestRuntimeSingleWalkStillChecksAllContentsAndEntries(t *testing.T) {
	for _, kind := range []string{"valid", "changed", "missing", "unexpected", "symlink"} {
		t.Run(kind, func(t *testing.T) {
			root := validRuntimeFixture(t)
			target := filepath.Join(root, "launch.py")
			switch kind {
			case "changed":
				os.WriteFile(target, []byte(strings.Repeat("y", 2048)), 0755)
			case "missing":
				os.Remove(target)
			case "unexpected":
				os.WriteFile(filepath.Join(root, "extra.py"), []byte("x"), 0600)
			case "symlink":
				os.Remove(target)
				if err := os.Symlink(filepath.Join(root, "tools", "ffmpeg"), target); err != nil {
					t.Skip(err)
				}
			}
			err := validateCompanyRuntime(root)
			if (err == nil) != (kind == "valid") {
				t.Fatalf("%s: %v", kind, err)
			}
		})
	}
}

func BenchmarkCompanyRuntimeValidation(b *testing.B) {
	root := os.Getenv("OCTOP_BENCH_RUNTIME")
	if root == "" {
		b.Skip("set OCTOP_BENCH_RUNTIME to an installed runtime")
	}
	for _, workers := range []int{1, 4} {
		b.Run(fmt.Sprintf("workers-%d", workers), func(b *testing.B) {
			for b.Loop() {
				if err := validateCompanyRuntimeWorkers(root, workers); err != nil {
					b.Fatal(err)
				}
			}
		})
	}
}

func TestCompanyPortablePreparationSharedDuringLogin(t *testing.T) {
	root, home := validRuntimeFixture(t), t.TempDir()
	archive := filepath.Join(t.TempDir(), "tools.zip")
	if err := os.WriteFile(archive, []byte("first"), 0600); err != nil {
		t.Fatal(err)
	}
	var cache companyPortableCache
	var calls atomic.Int32
	entered, release := make(chan struct{}), make(chan struct{})
	prepare := func(_, _ string) (string, error) {
		if calls.Add(1) == 1 {
			close(entered)
			<-release
		}
		return root, nil
	}
	results := make(chan error, 8)
	var group sync.WaitGroup
	for range 8 {
		group.Go(func() {
			got, err := cache.prepare(archive, home, prepare)
			if err == nil && got != root {
				err = errors.New("different prepared runtime")
			}
			results <- err
		})
	}
	<-entered
	close(release)
	group.Wait()
	close(results)
	for err := range results {
		if err != nil {
			t.Fatal(err)
		}
	}
	if calls.Load() != 1 {
		t.Fatal("login and worker repeated preparation", calls.Load())
	}
	if err := os.WriteFile(archive, []byte("changed archive"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := cache.prepare(archive, home, prepare); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 2 {
		t.Fatal("changed archive did not invalidate preparation")
	}
}

func TestCompanyPortablePreparationRetriesFailures(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "tools.zip")
	if err := os.WriteFile(archive, []byte("tools"), 0600); err != nil {
		t.Fatal(err)
	}
	root := validRuntimeFixture(t)
	var cache companyPortableCache
	calls := 0
	prepare := func(_, _ string) (string, error) {
		calls++
		if calls == 1 {
			return "", errors.New("interrupted extraction")
		}
		return root, nil
	}
	if _, err := cache.prepare(archive, "home", prepare); err == nil {
		t.Fatal("missing failure")
	}
	for range 2 {
		if _, err := cache.prepare(archive, "home", prepare); err != nil {
			t.Fatal(err)
		}
	}
	if calls != 2 {
		t.Fatal("failed preparation was cached or success repeated", calls)
	}
}
