package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"octop.desktop/internal/companyprojects"
	"octop.desktop/internal/companyruntime"
)

func projectContains(root, path string) bool {
	rel, err := filepath.Rel(root, path)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(os.PathSeparator))
}
func (a *App) runtimeAccountCurrent(nonce, base string, epoch uint64) bool {
	return a.workspaceNonce == nonce && a.companyURL == base && a.runtimeEpoch == epoch
}

// Caller holds companyMu. The catalog belongs to a company/employee installation,
// not to the short-lived login device ID or the currently selected project.
func (a *App) projectStoreLocked(base string) (*companyprojects.Store, error) {
	identity, _, err := a.company.RuntimeIdentity()
	if err != nil {
		return nil, err
	}
	scope := companyStateDir(base, identity.OwnerUserID)
	if a.projectStore != nil && a.projectScope == scope {
		return a.projectStore, nil
	}
	store, err := companyprojects.Open(filepath.Join(scope, "projects.json"))
	if err != nil {
		return nil, err
	}
	if err = migrateProjectReceipts(store, scope, a.workspacePath, a.workspaceGrant != nil); err != nil {
		return nil, err
	}
	a.projectStore, a.projectScope = store, scope
	return store, nil
}

func migrateProjectReceipts(store *companyprojects.Store, state, legacyRoot string, authorized bool) error {
	type receipt struct {
		Thread  string `json:"thread_id"`
		Path    string `json:"project_path"`
		Started int64  `json:"started_at_ns"`
		Project string `json:"project_id"`
	}
	latest := map[string]receipt{}
	files, err := filepath.Glob(filepath.Join(state, "tasks", "*", "task.json"))
	if err != nil {
		return err
	}
	for _, path := range files {
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		var item receipt
		if json.Unmarshal(data, &item) != nil || item.Thread == "" || item.Path == "" {
			return errors.New("project_receipt_invalid")
		}
		if item.Started == 0 {
			if info, err := os.Stat(path); err == nil {
				item.Started = info.ModTime().UnixNano()
			}
		}
		if old, ok := latest[item.Thread]; !ok || item.Started >= old.Started {
			latest[item.Thread] = item
		}
	}
	original := store.Snapshot().Current
	for thread, item := range latest {
		if item.Project != "" {
			// Never synthesize a new identity for a lost catalog or silently rebind it.
			if err := store.Bind(thread, item.Project); err != nil {
				return errors.New("project_catalog_missing")
			}
			continue
		}
		recorded := store.Snapshot().Threads[thread] != ""
		project, err := store.ImportLegacy(thread, item.Path)
		if err != nil {
			return err
		}
		// Preserve an existing native grant only inside its original canonical root.
		root, rootErr := filepath.EvalSymlinks(legacyRoot)
		if !recorded && !project.Authorized && authorized && rootErr == nil && projectContains(root, project.Path) {
			if _, err := os.Stat(project.Path); err == nil {
				if _, err = store.Register(project.Path); err != nil {
					return err
				}
			}
		}
	}
	return store.Select(original)
}

func projectRPC(ctx context.Context, process *companyruntime.Process, operation string, project companyprojects.Project, extra map[string]any) (json.RawMessage, error) {
	params := map[string]any{"operation": operation, "project_id": project.ID, "name": project.Name}
	for key, value := range extra {
		params[key] = value
	}
	return process.Request(ctx, "project_rpc", params)
}

type projectState struct {
	companyprojects.Catalog
	Supported bool `json:"supported"`
	Cancelled bool `json:"cancelled,omitempty"`
}

func (a *App) prepareProjectBrowser(nonce, base string) (*companyprojects.Browser, error) {
	a.companyMu.Lock()
	if a.workspaceNonce != nonce || a.companyURL != base {
		a.companyMu.Unlock()
		return nil, errors.New("session_changed")
	}
	identity, credential, err := a.company.RuntimeIdentity()
	if err != nil {
		a.companyMu.Unlock()
		return nil, err
	}
	if a.projectBrowser == nil {
		token, tokenErr := companyprojects.InstallationToken(companyStateDir(base, identity.OwnerUserID))
		if tokenErr != nil {
			a.companyMu.Unlock()
			return nil, tokenErr
		}
		a.projectBrowser, err = companyprojects.NewBrowser(base, credential, token)
		if err != nil {
			a.companyMu.Unlock()
			return nil, err
		}
	}
	browser, epoch := a.projectBrowser, a.runtimeEpoch
	a.companyMu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	supported, err := browser.Supported(ctx)
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	if !a.runtimeAccountCurrent(nonce, base, epoch) || a.projectBrowser != browser {
		return nil, errors.New("session_changed")
	}
	if err != nil {
		return nil, err
	}
	a.projectSupported = supported
	return browser, nil
}

func (a *App) performProjectAction(nonce, base, method string, raw json.RawMessage) (any, error) {
	var params struct {
		ProjectID      string `json:"project_id"`
		ThreadID       string `json:"thread_id"`
		AgentID        string `json:"agent_id"`
		Offset         int    `json:"offset"`
		Path           string `json:"path"`
		Query          string `json:"query"`
		Name           string `json:"name"`
		SourceThreadID string `json:"source_thread_id"`
		MessageID      string `json:"message_id"`
		Turns          int    `json:"assistant_turns_from_end"`
		Hide           bool   `json:"hide"`
		Revoke         bool   `json:"revoke"`
	}
	if json.Unmarshal(raw, &params) != nil {
		return nil, errors.New("invalid_project_request")
	}
	var process *companyruntime.Process
	var browser *companyprojects.Browser
	var err error
	switch method {
	case "project_status", "project_select", "project_files", "project_read", "project_threads", "project_continue":
		browser, err = a.prepareProjectBrowser(nonce, base)
	default:
		process, err = a.ensureRuntimeProcess(nonce, base, method, ensureCompanyPortable)
	}
	if err != nil {
		return nil, err
	}
	a.companyMu.Lock()
	epoch := a.runtimeEpoch
	if a.workspaceNonce != nonce || a.companyURL != base || (browser != nil && a.projectBrowser != browser) || (process != nil && a.companyRuntime != process) {
		a.companyMu.Unlock()
		return nil, errors.New("session_changed")
	}
	if !a.projectSupported {
		a.companyMu.Unlock()
		if method == "project_status" {
			return projectState{Supported: false}, nil
		}
		return nil, errors.New("project_runtime_required")
	}
	store, err := a.projectStoreLocked(base)
	a.companyMu.Unlock()
	if err != nil {
		return nil, err
	}
	check := func() error {
		a.companyMu.Lock()
		defer a.companyMu.Unlock()
		if !a.runtimeAccountCurrent(nonce, base, epoch) || a.projectStore != store {
			return errors.New("session_changed")
		}
		return nil
	}
	rpc := func(ctx context.Context, operation string, project companyprojects.Project, extra map[string]any) (json.RawMessage, error) {
		if err := check(); err != nil {
			return nil, err
		}
		if browser != nil {
			return browser.Project(ctx, operation, project, extra)
		}
		return projectRPC(ctx, process, operation, project, extra)
	}
	if method == "project_choose" || method == "project_relocate" {
		if !a.workspaceBusy.CompareAndSwap(false, true) {
			return nil, errors.New("请完成当前文件夹选择")
		}
		defer a.workspaceBusy.Store(false)
		title := "选择项目文件夹 · 授权本地任务使用当前系统用户权限"
		if method == "project_relocate" {
			title = "重新定位这个项目的原文件夹 · 历史成果仍保留原路径"
		}
		path, err := directorySelection(a.app.Dialog.OpenFile().SetTitle(title).CanChooseFiles(false).CanChooseDirectories(true).ResolvesAliases(true).AttachToWindow(a.window).PromptForSingleSelection())
		if err != nil {
			return nil, errors.New("directory_unavailable")
		}
		if method == "project_relocate" && path != "" {
			a.projectTaskMu.Lock()
			defer a.projectTaskMu.Unlock()
			snapshot := store.Snapshot()
			project, ok := snapshot.Projects[params.ProjectID]
			if !ok {
				return nil, errors.New("project_not_found")
			}
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			status, statusErr := process.Request(ctx, "status", map[string]any{})
			cancel()
			if statusErr != nil {
				return nil, statusErr
			}
			if err := checkProjectIdle(status, snapshot, project.Path, path); err != nil {
				return nil, err
			}
		}
		a.companyMu.Lock()
		if !a.runtimeAccountCurrent(nonce, base, epoch) || a.projectStore != store {
			a.companyMu.Unlock()
			return nil, errors.New("session_changed")
		}
		if path != "" {
			canonical, resolveErr := filepath.EvalSymlinks(path)
			home, homeErr := filepath.EvalSymlinks(octopHome())
			if resolveErr != nil || homeErr != nil {
				a.companyMu.Unlock()
				return nil, errors.New("directory_unavailable")
			}
			if projectContains(canonical, home) || projectContains(home, canonical) {
				a.companyMu.Unlock()
				return nil, errors.New("runtime_data_must_be_outside_project")
			}
			if method == "project_relocate" {
				_, err = store.Relocate(params.ProjectID, canonical)
			} else {
				_, err = store.Register(canonical)
			}
		}
		a.companyMu.Unlock()
		if err != nil {
			return nil, err
		}
		return projectState{Catalog: store.Snapshot(), Supported: true, Cancelled: path == ""}, nil
	}
	if method == "project_status" {
		return projectState{Catalog: store.Snapshot(), Supported: true}, check()
	}
	if method == "project_select" {
		a.companyMu.Lock()
		defer a.companyMu.Unlock()
		if !a.runtimeAccountCurrent(nonce, base, epoch) || a.projectStore != store {
			return nil, errors.New("session_changed")
		}
		if params.ThreadID != "" {
			params.ProjectID = store.Snapshot().Threads[params.ThreadID]
		}
		if params.ProjectID == "" {
			return nil, errors.New("thread_project_missing")
		}
		if err = store.Select(params.ProjectID); err != nil {
			return nil, err
		}
		if params.ThreadID != "" {
			if err = store.RememberThread(params.ThreadID); err != nil {
				return nil, err
			}
		}
		if params.AgentID != "" {
			if err = store.RememberExpert(params.ProjectID, params.AgentID); err != nil {
				return nil, err
			}
		}
		return projectState{Catalog: store.Snapshot(), Supported: true}, nil
	}
	snapshot := store.Snapshot()
	if params.ThreadID != "" && method != "project_create" && method != "project_threads" && method != "project_fork" && method != "project_continue" {
		params.ProjectID = snapshot.Threads[params.ThreadID]
	}
	if method == "project_fork" {
		params.ProjectID = snapshot.Threads[params.SourceThreadID]
	}
	project, ok := snapshot.Projects[params.ProjectID]
	if !ok {
		return nil, errors.New("thread_project_missing")
	}
	if method == "project_files" || method == "project_read" {
		project, err = store.ForID(project.ID)
		if err != nil {
			return nil, err
		}
		var result any
		if method == "project_files" {
			result, err = companyprojects.ListFiles(project.Path, params.Path, params.Query)
		} else {
			result, err = companyprojects.ReadFile(project.Path, params.Path)
		}
		if scopeErr := check(); scopeErr != nil {
			return nil, scopeErr
		}
		current, permissionErr := store.ForID(project.ID)
		if permissionErr != nil {
			return nil, permissionErr
		}
		if current.Path != project.Path || current.Revision != project.Revision {
			return nil, errors.New("项目目录或授权已变化，请重新打开文件")
		}
		return result, err
	}
	if method == "project_create" || method == "project_continue" {
		if _, err = store.ForID(project.ID); err != nil {
			return nil, err
		}
		if !forwardMediaID.MatchString(params.ThreadID) || !forwardMediaID.MatchString(params.AgentID) {
			return nil, errors.New("invalid_project_request")
		}
		if method == "project_continue" && (!forwardMediaID.MatchString(params.SourceThreadID) || params.SourceThreadID == params.ThreadID) {
			return nil, errors.New("invalid_project_request")
		}
	}
	if method == "project_update" {
		a.projectTaskMu.Lock()
		defer a.projectTaskMu.Unlock()
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	if err = check(); err != nil {
		return nil, err
	}
	if _, err = rpc(ctx, "register", project, nil); err != nil {
		return nil, err
	}
	if method == "project_update" {
		if params.Revoke {
			rawStatus, err := process.Request(ctx, "status", map[string]any{})
			if err != nil {
				return nil, err
			}
			if err := checkProjectIdle(rawStatus, snapshot, project.Path); err != nil {
				return nil, err
			}
		}
		name := strings.TrimSpace(params.Name)
		if len([]rune(name)) > 200 || strings.ContainsAny(name, "\r\n") {
			return nil, errors.New("invalid_project_name")
		}
		if name != "" {
			project.Name = name
			if _, err := projectRPC(ctx, process, "rename", project, nil); err != nil {
				return nil, err
			}
		}
		a.companyMu.Lock()
		defer a.companyMu.Unlock()
		if !a.runtimeAccountCurrent(nonce, base, epoch) || a.projectStore != store {
			return nil, errors.New("session_changed")
		}
		if err := store.Update(project.ID, name, params.Hide, params.Revoke); err != nil {
			return nil, err
		}
		return projectState{Catalog: store.Snapshot(), Supported: true}, nil
	}
	if method == "project_create" || method == "project_fork" || method == "project_continue" {
		operation := "create"
		extra := map[string]any{"thread_id": params.ThreadID, "agent_id": params.AgentID}
		if method == "project_continue" {
			operation = "continue"
			extra["source_thread_id"] = params.SourceThreadID
		}
		if method == "project_fork" {
			operation = "fork"
			extra = map[string]any{"thread_id": params.ThreadID, "source_thread_id": params.SourceThreadID}
			if params.MessageID != "" {
				extra["message_id"] = params.MessageID
			}
			if params.Turns > 0 {
				extra["assistant_turns_from_end"] = params.Turns
			}
			if project.Legacy {
				if _, err := projectRPC(ctx, process, "adopt", project, map[string]any{"thread_id": params.SourceThreadID}); err != nil {
					return nil, err
				}
			}
		}
		result, err := rpc(ctx, operation, project, extra)
		if err != nil {
			return nil, err
		}
		a.companyMu.Lock()
		defer a.companyMu.Unlock()
		if !a.runtimeAccountCurrent(nonce, base, epoch) || a.projectStore != store {
			return nil, errors.New("session_changed")
		}
		current, permissionErr := store.ForID(project.ID)
		if permissionErr != nil {
			return nil, permissionErr
		}
		if current.Path != project.Path || current.Revision != project.Revision {
			return nil, errors.New("directory_changed")
		}
		if err = store.Bind(params.ThreadID, project.ID); err != nil {
			return nil, err
		}
		if err = store.RememberThread(params.ThreadID); err != nil {
			return nil, err
		}
		if method == "project_fork" {
			var created struct {
				AgentID string `json:"agent_id"`
			}
			if json.Unmarshal(result, &created) != nil {
				return nil, errors.New("invalid_project_response")
			}
			params.AgentID = created.AgentID
		}
		if err = store.RememberExpert(project.ID, params.AgentID); err != nil {
			return nil, err
		}
		return result, nil
	}
	if method == "project_threads" {
		// Only recorded local receipts can migrate a legacy conversation. Retrying is
		// harmless, and the company verifies installation ownership independently.
		if project.Legacy {
			threads := []string{}
			for thread, id := range snapshot.Threads {
				if id == project.ID {
					threads = append(threads, thread)
				}
			}
			sort.Strings(threads)
			for _, thread := range threads {
				if _, err = rpc(ctx, "adopt", project, map[string]any{"thread_id": thread}); err != nil {
					return nil, err
				}
			}
		}
		result, err := rpc(ctx, "threads", project, map[string]any{"offset": params.Offset})
		if err != nil {
			return nil, err
		}
		var list struct {
			Items []struct {
				Thread  string `json:"thread_id"`
				Project string `json:"project_id"`
			} `json:"items"`
		}
		if json.Unmarshal(result, &list) != nil {
			return nil, errors.New("invalid_project_response")
		}
		a.companyMu.Lock()
		defer a.companyMu.Unlock()
		if !a.runtimeAccountCurrent(nonce, base, epoch) || a.projectStore != store {
			return nil, errors.New("session_changed")
		}
		for _, item := range list.Items {
			if item.Project != project.ID {
				return nil, errors.New("thread_project_conflict")
			}
			if err = store.Bind(item.Thread, project.ID); err != nil {
				return nil, err
			}
		}
		return result, nil
	}
	return nil, errors.New("invalid_project_request")
}

// The renderer supplies a thread ID, never a trusted directory. Capturing this
// binding before the task starts makes later sidebar switches irrelevant.
func (a *App) projectStartParamsLocked(nonce, base string, raw json.RawMessage) (json.RawMessage, error) {
	if a.workspaceNonce != nonce || a.companyURL != base {
		return nil, errors.New("session_changed")
	}
	var params map[string]any
	if json.Unmarshal(raw, &params) != nil {
		return nil, errors.New("invalid_project_request")
	}
	delete(params, "project_id")
	delete(params, "project_root")
	delete(params, "project_revision")
	if a.projectSupported {
		store, err := a.projectStoreLocked(base)
		if err != nil {
			return nil, err
		}
		thread, _ := params["thread_id"].(string)
		if store.Snapshot().Threads[thread] != "" {
			p, err := store.ForThread(thread)
			if err != nil {
				return nil, err
			}
			params["project_id"], params["project_root"], params["project_revision"] = p.ID, p.Path, p.Revision
		} else {
			return nil, errors.New("thread_project_missing")
		}
	}
	return json.Marshal(params)
}

// History previews stay bound to an authorized source root even after switching
// projects. The most specific known root wins, including an unavailable grant.
func (a *App) artifactRootLocked(path string) (string, error) {
	if a.projectSupported && a.projectStore == nil {
		if _, err := a.projectStoreLocked(a.companyURL); err != nil {
			return "", err
		}
	}
	if !filepath.IsAbs(path) {
		return "", errors.New("directory_unavailable")
	}
	if a.projectStore != nil {
		var selected companyprojects.Project
		for _, p := range a.projectStore.Snapshot().Projects {
			if projectContains(p.Path, path) && len(p.Path) > len(selected.Path) {
				selected = p
			}
		}
		if selected.ID != "" {
			p, err := a.projectStore.ForID(selected.ID)
			return p.Path, err
		}
	}
	if a.workspaceGrant != nil && projectContains(a.workspacePath, path) {
		return a.workspacePath, nil
	}
	return "", errors.New("project_authorization_required")
}

func checkProjectIdle(raw json.RawMessage, catalog companyprojects.Catalog, roots ...string) error {
	var status struct {
		Active []struct {
			Thread string `json:"thread_id"`
			Status string `json:"status"`
		} `json:"active_tasks"`
	}
	if json.Unmarshal(raw, &status) != nil {
		return errors.New("invalid_project_response")
	}
	for _, task := range status.Active {
		if task.Status != "running" && task.Status != "unknown" && task.Status != "cancel_requested" {
			continue
		}
		project, ok := catalog.Projects[catalog.Threads[task.Thread]]
		if !ok {
			return errors.New("project_already_running")
		}
		if canonical, err := filepath.EvalSymlinks(project.Path); err == nil {
			project.Path = canonical
		}
		for _, root := range roots {
			canonical, err := filepath.EvalSymlinks(root)
			if err != nil {
				canonical = root
			}
			if projectContains(project.Path, canonical) || projectContains(canonical, project.Path) {
				return errors.New("project_already_running")
			}
		}
	}
	return nil
}
