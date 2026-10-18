package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
	"octop.desktop/internal/company"
)

func TestWorkspaceSettingsScopedToCompanyAndOwner(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	folder := filepath.Join(t.TempDir(), "工作资料")
	if err := saveWorkspace("http://company.test", 7, folder); err != nil {
		t.Fatal(err)
	}
	if readWorkspace("http://company.test", 7) != folder {
		t.Fatal("saved folder was lost")
	}
	if readWorkspace("http://company.test", 8) != "" || readWorkspace("http://other.test", 7) != "" {
		t.Fatal("folder leaked across accounts or servers")
	}
	if err := saveWorkspace("http://company.test", 7, ""); err != nil {
		t.Fatal(err)
	}
	if readWorkspace("http://company.test", 7) != "" {
		t.Fatal("removed folder was restored")
	}
}

func TestDefaultWorkspacePrefersWindowsInstallAndFallsBackWhenUnwritable(t *testing.T) {
	install, home := t.TempDir(), t.TempDir()
	folder, err := createDefaultWorkspace(install, home, "windows", "account-a")
	if err != nil || folder != filepath.Join(install, "Workspace", "account-a") {
		t.Fatal(folder, err)
	}
	blocked := filepath.Join(t.TempDir(), "not-a-directory")
	if err := os.WriteFile(blocked, []byte("blocked"), 0600); err != nil {
		t.Fatal(err)
	}
	fallback, err := createDefaultWorkspace(blocked, home, "windows", "account-b")
	if err != nil || fallback != filepath.Join(home, "Octop Workspace", "account-b") {
		t.Fatal(fallback, err)
	}
	if _, err := os.Stat(folder); err != nil {
		t.Fatal("default project was not created", err)
	}
	mac, err := createDefaultWorkspace(install, home, "darwin", "account-a")
	if err != nil || mac == folder || mac != filepath.Join(home, "Octop Workspace", "account-a") {
		t.Fatal("macOS project must stay outside the application bundle", mac, err)
	}
}

func TestWorkspaceClearAndUnavailableChoicesDoNotCreateNewDefaults(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	const base = "https://company.test"
	if err := saveWorkspace(base, 7, ""); err != nil {
		t.Fatal(err)
	}
	if folder, err := savedOrDefaultWorkspace(base, 7); err != nil || folder != "" {
		t.Fatal("explicitly cleared project was recreated", folder, err)
	}
	missing := filepath.Join(t.TempDir(), "missing")
	if err := saveWorkspace(base, 7, missing); err != nil {
		t.Fatal(err)
	}
	if folder, err := savedOrDefaultWorkspace(base, 7); err != nil || folder != missing {
		t.Fatal("saved project was silently replaced", folder, err)
	}
}

func TestWorkspaceBridgeRejectsWrongNonceAndArbitraryPaths(t *testing.T) {
	for _, name := range []string{
		"desktop:workspace:wrong:1:status:",
		"desktop:workspace:nonce:1:choose:/tmp",
		"desktop:workspace:nonce:1:execute:whoami",
		"desktop:workspace:nonce:1:pair:account-token",
		"desktop:workspace:nonce:1:clear:folder",
	} {
		if _, _, _, ok := parseWorkspaceEvent(name, "nonce"); ok {
			t.Fatalf("accepted %q", name)
		}
	}
	if _, action, code, ok := parseWorkspaceEvent("desktop:workspace:nonce:1:pair:octop-pair.test", "nonce"); !ok || action != "pair" || code != "octop-pair.test" {
		t.Fatal("valid pairing code rejected")
	}
}

func TestWorkspaceReconnectRestoresOnlyMatchingOwner(t *testing.T) {
	if runtime.GOOS != "darwin" && runtime.GOOS != "windows" {
		t.Skip("company desktop platforms")
	}
	t.Setenv("OCTOP_HOME", t.TempDir())
	t.Setenv("HOME", t.TempDir())
	t.Setenv("USERPROFILE", t.TempDir())
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer conn.CloseNow()
		ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
		defer cancel()
		var hello map[string]any
		if wsjson.Read(ctx, conn, &hello) != nil {
			return
		}
		owner := 7
		if hello["credential"] == "octop-pair.other" {
			owner = 8
		}
		_ = wsjson.Write(ctx, conn, map[string]any{"type": "ready", "device_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "owner_user_id": owner, "protocol_version": 2, "heartbeat_seconds": 15, "expires_at": time.Now().Add(time.Hour).Unix(), "credential": "octop-device.test"})
		for {
			var frame map[string]any
			if wsjson.Read(ctx, conn, &frame) != nil || frame["type"] == "revoke" {
				return
			}
		}
	}))
	defer server.Close()
	folder := t.TempDir()
	if err := saveWorkspace(server.URL, 7, folder); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		code     string
		restored bool
	}{{"octop-pair.same", true}, {"octop-pair.other", false}} {
		app := &App{company: company.NewClient(), companyURL: server.URL, workspaceNonce: "nonce"}
		if err := app.performWorkspaceAction("nonce", server.URL, "pair", tc.code); err != nil {
			t.Fatal(err)
		}
		if app.workspaceGrant == nil || (!tc.restored && (app.workspacePath == "" || app.workspacePath == folder)) {
			t.Fatal("new owner did not receive an independent default project")
		}
		if tc.restored {
			if app.workspacePath != folder || !app.workspaceGrant.Writable || !app.workspaceGrant.AllowCompany {
				t.Fatal("work folder permissions missing")
			}
			if err := app.performWorkspaceAction("nonce", server.URL, "clear", ""); err != nil {
				t.Fatal(err)
			}
			if len(app.company.Grants()) != 0 || readWorkspace(server.URL, 7) != "" {
				t.Fatal("remove did not revoke and forget folder")
			}
			_ = saveWorkspace(server.URL, 7, folder)
		}
		app.company.Close(true)
	}
	missing := filepath.Join(folder, "removed-folder")
	_ = saveWorkspace(server.URL, 7, missing)
	app := &App{company: company.NewClient(), companyURL: server.URL, workspaceNonce: "nonce"}
	if err := app.performWorkspaceAction("nonce", server.URL, "pair", "octop-pair.same"); err != nil {
		t.Fatal(err)
	}
	defer app.company.Close(true)
	if app.workspaceError != "directory_unavailable" || app.workspaceGrant != nil || app.workspacePath != missing {
		t.Fatal("missing directory was silently accepted or forgotten")
	}
}

func TestCompanyDefaultsToChinese(t *testing.T) {
	if companyOnly && defaultSettings().Locale != LocaleZH {
		t.Fatal("employee install must default to Chinese")
	}
}
