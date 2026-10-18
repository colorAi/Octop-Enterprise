package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"octop.desktop/internal/company"
)

const workspaceEventPrefix = "desktop:workspace:"

// Persist only the native folder choice, scoped to the authenticated server and
// owner. Device credentials and grant IDs are freshly issued for every session.
func workspaceSettingsPath(base string, owner int64) string {
	sum := sha256.Sum256([]byte(fmt.Sprintf("%s\x00%d", base, owner)))
	return filepath.Join(octopHome(), "workspaces", hex.EncodeToString(sum[:])+".json")
}

func readWorkspace(base string, owner int64) string {
	var saved struct {
		Path string `json:"path"`
	}
	data, err := os.ReadFile(workspaceSettingsPath(base, owner))
	if err != nil || json.Unmarshal(data, &saved) != nil {
		return ""
	}
	return saved.Path
}

func workspaceRuntimeAuthorized(base string, owner int64) bool {
	var saved struct {
		RuntimeAuthorized bool `json:"runtime_authorized"`
	}
	data, err := os.ReadFile(workspaceSettingsPath(base, owner))
	return err == nil && json.Unmarshal(data, &saved) == nil && saved.RuntimeAuthorized
}

func saveWorkspace(base string, owner int64, path string) error {
	name := workspaceSettingsPath(base, owner)
	if err := os.MkdirAll(filepath.Dir(name), 0o700); err != nil {
		return err
	}
	data, _ := json.Marshal(struct {
		Path              string `json:"path"`
		RuntimeAuthorized bool   `json:"runtime_authorized"`
	}{path, path != ""})
	return os.WriteFile(name, data, 0o600)
}

// Keep each company's account separate, including when several employees use
// the same Windows login. The project never contains the client's private data.
func createDefaultWorkspace(installDir, home, platform, scope string) (string, error) {
	candidates := []string{}
	if platform == "windows" && installDir != "" {
		candidates = append(candidates, filepath.Join(installDir, "Workspace", scope))
	}
	if home != "" {
		candidates = append(candidates, filepath.Join(home, "Octop Workspace", scope))
	}
	for _, folder := range candidates {
		if err := os.MkdirAll(folder, 0o700); err != nil {
			continue
		}
		probe, err := os.CreateTemp(folder, ".octop-write-check-*")
		if err != nil {
			continue
		}
		probe.Close()
		os.Remove(probe.Name())
		return folder, nil
	}
	return "", errors.New("directory_unavailable")
}

func savedOrDefaultWorkspace(base string, owner int64) (string, error) {
	name := workspaceSettingsPath(base, owner)
	if _, err := os.Stat(name); !os.IsNotExist(err) {
		// An explicit clear stays cleared; unavailable saved folders are not
		// silently replaced on reconnect.
		return readWorkspace(base, owner), err
	}
	executable, _ := os.Executable()
	home, _ := os.UserHomeDir()
	scope := strings.TrimSuffix(filepath.Base(name), ".json")[:12]
	folder, err := createDefaultWorkspace(filepath.Dir(executable), home, runtime.GOOS, scope)
	if err != nil {
		return "", err
	}
	if err := saveWorkspace(base, owner, folder); err != nil {
		return "", errors.New("save_failed")
	}
	return folder, nil
}

type workspaceState struct {
	State    string         `json:"state"`
	DeviceID string         `json:"device_id"`
	Owner    int64          `json:"owner_user_id"`
	Path     string         `json:"path"`
	Grant    *company.Grant `json:"grant"`
	Error    string         `json:"error,omitempty"`
}

func (a *App) workspaceStateLocked() workspaceState {
	s := a.company.Status()
	return workspaceState{s.State, s.DeviceID, s.OwnerUserID, a.workspacePath, a.workspaceGrant, a.workspaceError}
}

func parseWorkspaceEvent(name, nonce string) (id, action, code string, ok bool) {
	if nonce == "" || len(name) > 512 {
		return
	}
	parts := strings.Split(strings.TrimPrefix(name, workspaceEventPrefix), ":")
	if !strings.HasPrefix(name, workspaceEventPrefix) || len(parts) != 4 || parts[0] != nonce {
		return
	}
	if len(parts[1]) == 0 || len(parts[1]) > 32 || strings.IndexFunc(parts[1], func(r rune) bool { return r < '0' || r > '9' }) >= 0 {
		return
	}
	id, action = parts[1], parts[2]
	switch action {
	case "status", "choose", "clear", "disconnect":
		ok = parts[3] == ""
	case "pair":
		code, _ = url.QueryUnescape(parts[3])
		ok = strings.HasPrefix(code, "octop-pair.") && len(code) <= 128
	}
	return
}

func (a *App) dispatchWorkspaceEvent(name string) {
	a.companyMu.Lock()
	nonce, base := a.workspaceNonce, a.companyURL
	id, action, code, ok := parseWorkspaceEvent(name, nonce)
	a.companyMu.Unlock()
	if !ok || base == "" {
		return
	}
	// Native directory dialogs run asynchronously; status remains available while
	// one is open. No script can supply a filesystem path to this bridge.
	go func() {
		var err error
		if action != "status" {
			if !a.workspaceBusy.CompareAndSwap(false, true) {
				err = errors.New("busy")
			} else {
				defer a.workspaceBusy.Store(false)
				err = a.performWorkspaceAction(nonce, base, action, code)
			}
		}
		a.companyMu.Lock()
		defer a.companyMu.Unlock()
		if a.workspaceNonce != nonce || a.companyURL != base {
			return
		}
		payload := map[string]any{"id": id, "state": a.workspaceStateLocked()}
		if err != nil {
			payload["error"] = err.Error()
		}
		data, _ := json.Marshal(payload)
		a.window.ExecJS(`(function(origin,nonce,result){if(location.origin===origin)window.__octopWorkspaceReply?.(nonce,result);})(` + jsonString(a.companyPageOriginLocked()) + `,` + jsonString(nonce) + `,` + string(data) + `)`)
	}()
}

func (a *App) performWorkspaceAction(nonce, base, action, code string) error {
	a.companyMu.Lock()
	if a.workspaceNonce != nonce || a.companyURL != base {
		a.companyMu.Unlock()
		return errors.New("session_changed")
	}
	client := a.company
	if action == "disconnect" || action == "pair" {
		a.stopCompanyRuntimeLocked()
		client.Close(true)
		client = company.NewClient()
		a.company = client
		a.workspacePath, a.workspaceError, a.workspaceGrant = "", "", nil
	}
	a.companyMu.Unlock()
	if action == "disconnect" {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	if action == "pair" {
		name, _ := os.Hostname()
		if name == "" || len(name) > 80 {
			name = "Octop desktop"
		}
		if err := client.Pair(ctx, base, code, name, runtime.GOOS); err != nil {
			return errors.New("connection_failed")
		}
		a.companyMu.Lock()
		defer a.companyMu.Unlock()
		if a.company != client || a.workspaceNonce != nonce {
			client.Close(true)
			return errors.New("session_changed")
		}
		path, err := savedOrDefaultWorkspace(base, client.Status().OwnerUserID)
		a.workspacePath = path
		if err != nil {
			a.workspaceError = err.Error()
		} else if path != "" && !workspaceRuntimeAuthorized(base, client.Status().OwnerUserID) {
			a.workspaceError = "runtime_authorization_required"
		} else if path != "" {
			grant, err := client.AddGrant(ctx, path, true, true)
			if err != nil {
				a.workspaceError = "directory_unavailable"
			} else {
				a.workspaceGrant = &grant
			}
		}
		return nil
	}
	if client.Status().State != "online" {
		return errors.New("device_offline")
	}
	path := ""
	if action == "choose" {
		var err error
		title := "授权本地任务 · 终端使用当前系统用户权限（工作目录非沙箱）"
		if a.store.get().Locale == LocaleEN {
			title = "Authorize local tasks · Terminal uses your OS account permissions (not sandboxed)"
		}
		path, err = a.app.Dialog.OpenFile().SetTitle(title).CanChooseFiles(false).CanChooseDirectories(true).ResolvesAliases(true).AttachToWindow(a.window).PromptForSingleSelection()
		if err != nil {
			return errors.New("directory_unavailable")
		}
		if path == "" {
			return nil
		}
	}
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	if a.company != client || a.workspaceNonce != nonce {
		return errors.New("session_changed")
	}
	if client.Status().State != "online" {
		return errors.New("device_offline")
	}
	// Dialogs can remain open longer than network requests.
	ctx, cancel = context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	var grant *company.Grant
	if path != "" {
		g, err := client.AddGrant(ctx, path, true, true)
		if err != nil {
			return errors.New("directory_unavailable")
		}
		grant = &g
	}
	if err := saveWorkspace(base, client.Status().OwnerUserID, path); err != nil {
		if grant != nil {
			_ = client.RevokeGrant(ctx, grant.ID)
		}
		return errors.New("save_failed")
	}
	a.stopCompanyRuntimeLocked()
	old := a.workspaceGrant
	a.workspacePath, a.workspaceGrant, a.workspaceError = path, grant, ""
	if old != nil {
		_ = client.RevokeGrant(ctx, old.ID)
	}
	return nil
}
