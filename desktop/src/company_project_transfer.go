package main

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"time"

	"octop.desktop/internal/companyprojects"
)

type projectSyncResult struct {
	Count int          `json:"synced_count"`
	State projectState `json:"state"`
}

func (a *App) syncProjectConversations(nonce, base string, ids []string) (projectSyncResult, error) {
	var result projectSyncResult
	if len(ids) == 0 || len(ids) > 100 {
		return result, errors.New("project_sync_select")
	}
	browser, err := a.prepareProjectBrowser(nonce, base)
	if err != nil {
		return result, err
	}
	a.companyMu.Lock()
	if a.workspaceNonce != nonce || a.companyURL != base || a.projectBrowser != browser {
		a.companyMu.Unlock()
		return result, errors.New("session_changed")
	}
	store, err := a.projectStoreLocked(base)
	epoch := a.runtimeEpoch
	a.companyMu.Unlock()
	if err != nil {
		return result, err
	}

	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	if err := browser.RequireAccountProjects(ctx); err != nil {
		return result, err
	}
	ids = slices.Clone(ids)
	sort.Strings(ids)
	bindings := store.Snapshot().Threads
	for _, id := range slices.Compact(ids) {
		project, err := store.ForID(id)
		if err != nil {
			return result, err
		}
		name, threads, err := browser.ProjectConversations(ctx, project, bindings)
		if err != nil {
			return result, err
		}
		a.companyMu.Lock()
		if !a.runtimeAccountCurrent(nonce, base, epoch) || a.projectStore != store {
			a.companyMu.Unlock()
			return result, errors.New("session_changed")
		}
		err = store.SyncConversations(project, name, threads)
		a.companyMu.Unlock()
		if err != nil {
			return result, err
		}
		result.Count += len(threads)
	}
	result.State = projectState{Catalog: store.Snapshot(), Supported: true}
	return result, nil
}

func (a *App) performAccountProjectAction(nonce, base, method string, raw json.RawMessage) (any, error) {
	var params struct {
		ProjectID  string   `json:"project_id"`
		ProjectIDs []string `json:"project_ids"`
	}
	if json.Unmarshal(raw, &params) != nil {
		return nil, errors.New("invalid_project_request")
	}
	if method == "project_sync" {
		return a.syncProjectConversations(nonce, base, params.ProjectIDs)
	}
	browser, err := a.prepareProjectBrowser(nonce, base)
	if err != nil {
		return nil, err
	}
	a.companyMu.Lock()
	epoch := a.runtimeEpoch
	a.companyMu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	projects, err := browser.AccountProjects(ctx)
	if err != nil {
		return nil, err
	}
	a.companyMu.Lock()
	current := a.runtimeAccountCurrent(nonce, base, epoch) && a.projectBrowser == browser
	a.companyMu.Unlock()
	if !current {
		return nil, errors.New("session_changed")
	}
	if method == "project_account_list" {
		return projects, nil
	}
	match := slices.IndexFunc(projects, func(p companyprojects.AccountProject) bool {
		return p.ID == params.ProjectID
	})
	if match < 0 {
		return nil, errors.New("project_not_owned")
	}
	if !a.workspaceBusy.CompareAndSwap(false, true) {
		return nil, errors.New("请完成当前文件夹选择")
	}
	defer a.workspaceBusy.Store(false)
	folder, err := directorySelection(a.app.Dialog.OpenFile().SetTitle("选择此项目在本机的文件夹 · 授权本地任务使用").CanChooseFiles(false).CanChooseDirectories(true).ResolvesAliases(true).AttachToWindow(a.window).PromptForSingleSelection())
	if err != nil {
		return nil, err
	}
	if folder == "" {
		return map[string]bool{"cancelled": true}, nil
	}
	folder, err = filepath.EvalSymlinks(folder)
	if err != nil {
		return nil, err
	}
	if err := validateProjectDestination(folder); err != nil {
		return nil, err
	}
	a.companyMu.Lock()
	if !a.runtimeAccountCurrent(nonce, base, epoch) || a.projectBrowser != browser {
		a.companyMu.Unlock()
		return nil, errors.New("session_changed")
	}
	store, err := a.projectStoreLocked(base)
	if err == nil {
		project := projects[match]
		err = store.Attach(project.ID, project.Name, folder)
	}
	a.companyMu.Unlock()
	if err != nil {
		return nil, err
	}
	return a.importedProjectResult(nonce, base, epoch, store, folder, []string{params.ProjectID})
}

func validateProjectDestination(path string) error {
	state, err := filepath.EvalSymlinks(octopHome())
	if err != nil {
		return err
	}
	if projectContains(path, state) || projectContains(state, path) {
		return errors.New("runtime_data_must_be_outside_project")
	}
	return nil
}

func (a *App) importedProjectResult(nonce, base string, epoch uint64, store *companyprojects.Store, path string, ids []string) (any, error) {
	var result projectSyncResult
	var err error
	a.companyMu.Lock()
	current := a.runtimeAccountCurrent(nonce, base, epoch) && a.projectStore == store
	online := a.company.Status().State == "online"
	a.companyMu.Unlock()
	if !current {
		return nil, errors.New("session_changed")
	}
	if online {
		result, err = a.syncProjectConversations(nonce, base, ids)
	}
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	if !a.runtimeAccountCurrent(nonce, base, epoch) || a.projectStore != store {
		return nil, errors.New("session_changed")
	}
	catalog := store.Snapshot()
	pending := slices.ContainsFunc(ids, func(id string) bool { return catalog.Projects[id].SyncPending })
	reply := map[string]any{
		"path": path, "project_ids": ids, "synced_count": result.Count,
		"sync_pending": pending || err != nil, "state": projectState{Catalog: catalog, Supported: true},
	}
	if err != nil {
		reply["sync_error"] = runtimeErrorText(err.Error())
	}
	return reply, nil
}

func (a *App) performProjectTransfer(nonce, base, method string, raw json.RawMessage) (any, error) {
	var params struct {
		ProjectIDs []string `json:"project_ids"`
	}
	if json.Unmarshal(raw, &params) != nil {
		return nil, errors.New("invalid_project_request")
	}
	if !a.workspaceBusy.CompareAndSwap(false, true) {
		return nil, errors.New("请完成当前文件夹选择")
	}
	defer a.workspaceBusy.Store(false)
	a.companyMu.Lock()
	if a.workspaceNonce != nonce || a.companyURL != base {
		a.companyMu.Unlock()
		return nil, errors.New("session_changed")
	}
	store, err := a.projectStoreLocked(base)
	identity := a.company.Status()
	epoch := a.runtimeEpoch
	a.companyMu.Unlock()
	if err != nil {
		return nil, err
	}
	current := func() bool {
		status := a.company.Status()
		return a.runtimeAccountCurrent(nonce, base, epoch) && a.projectStore == store &&
			status.OwnerUserID == identity.OwnerUserID && status.ExpiresAt > time.Now().Unix()
	}
	check := func() error {
		a.companyMu.Lock()
		defer a.companyMu.Unlock()
		if !current() {
			return errors.New("session_changed")
		}
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	done := make(chan struct{})
	defer close(done)
	go func() {
		ticker := time.NewTicker(200 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-done:
				return
			case <-ticker.C:
				if check() != nil {
					cancel()
					return
				}
			}
		}
	}()

	if method == "project_export" {
		if len(params.ProjectIDs) == 0 || len(params.ProjectIDs) > 100 {
			return nil, errors.New("project_export_select")
		}
		browser, err := a.prepareProjectBrowser(nonce, base)
		if err != nil {
			return nil, err
		}
		folder, err := directorySelection(a.app.Dialog.OpenFile().SetTitle("选择迁移包保存文件夹").CanChooseFiles(false).CanChooseDirectories(true).ResolvesAliases(true).AttachToWindow(a.window).PromptForSingleSelection())
		if err != nil {
			return nil, err
		}
		if folder == "" {
			return map[string]bool{"cancelled": true}, nil
		}
		folder, err = filepath.EvalSymlinks(folder)
		if err != nil {
			return nil, err
		}
		a.projectTaskMu.Lock()
		defer a.projectTaskMu.Unlock()
		if err := check(); err != nil {
			return nil, err
		}
		catalog := store.Snapshot()
		manifest := companyprojects.ArchiveManifest{Server: base, Owner: identity.OwnerUserID, ID: rand.Text()}
		sort.Strings(params.ProjectIDs)
		for _, id := range slices.Compact(params.ProjectIDs) {
			p, err := store.ForID(id)
			if err != nil {
				return nil, err
			}
			if projectContains(p.Path, folder) {
				return nil, errors.New("project_archive_output_inside_project")
			}
			manifest.Projects = append(manifest.Projects, p)
		}
		a.companyMu.Lock()
		process := a.companyRuntime
		a.companyMu.Unlock()
		if process != nil && process.Alive() {
			status, err := process.Request(ctx, "status", map[string]any{})
			if err != nil {
				return nil, err
			}
			for _, p := range manifest.Projects {
				if err := checkProjectIdle(status, catalog, p.Path); err != nil {
					return nil, err
				}
			}
		}
		for _, p := range manifest.Projects {
			if _, err := browser.Project(ctx, "register", p, nil); err != nil {
				return nil, err
			}
			if err := browser.AdoptLegacyConversations(ctx, p, catalog.Threads); err != nil {
				return nil, err
			}
		}
		filename := filepath.Join(folder, "Octop-projects-"+time.Now().Format("20060102-150405")+"-"+manifest.ID[:8]+".zip")
		if err := companyprojects.ExportArchive(ctx, filename, manifest); err != nil {
			return nil, err
		}
		if err := check(); err != nil {
			_ = os.Remove(filename)
			return nil, err
		}
		return map[string]any{"path": filename}, nil
	}

	filename, err := directorySelection(a.app.Dialog.OpenFile().SetTitle("选择 Octop 项目迁移包（ZIP）").CanChooseFiles(true).CanChooseDirectories(false).ResolvesAliases(true).AttachToWindow(a.window).PromptForSingleSelection())
	if err != nil {
		return nil, err
	}
	if filename == "" {
		return map[string]bool{"cancelled": true}, nil
	}
	manifest, err := companyprojects.ReadArchive(filename)
	if err != nil {
		return nil, err
	}
	if strings.TrimRight(manifest.Server, "/") != strings.TrimRight(base, "/") || manifest.Owner != identity.OwnerUserID {
		return nil, errors.New("project_archive_not_owned")
	}
	parent, err := directorySelection(a.app.Dialog.OpenFile().SetTitle("选择导入位置 · 授权本地任务使用此处的项目文件").CanChooseFiles(false).CanChooseDirectories(true).ResolvesAliases(true).AttachToWindow(a.window).PromptForSingleSelection())
	if err != nil {
		return nil, err
	}
	if parent == "" {
		return map[string]bool{"cancelled": true}, nil
	}
	parent, err = filepath.EvalSymlinks(parent)
	if err != nil {
		return nil, err
	}
	if err := check(); err != nil {
		return nil, err
	}
	catalog := store.Snapshot()
	destination := filepath.Join(parent, "octop-migration-"+manifest.ID)
	ids := make([]string, 0, len(manifest.Projects))
	for _, p := range manifest.Projects {
		if old, ok := catalog.Projects[p.ID]; ok && old.Path != filepath.Join(destination, "projects", p.ID) {
			return nil, errors.New("project_archive_project_exists")
		}
		ids = append(ids, p.ID)
	}
	if err := validateProjectDestination(destination); err != nil {
		return nil, err
	}
	directory, err := companyprojects.ExtractArchive(ctx, filename, parent, manifest)
	if err != nil {
		return nil, err
	}
	a.companyMu.Lock()
	if !current() {
		a.companyMu.Unlock()
		return nil, errors.New("session_changed")
	}
	err = store.ImportCatalog(directory, manifest)
	a.companyMu.Unlock()
	if err != nil {
		return nil, err
	}
	return a.importedProjectResult(nonce, base, epoch, store, directory, ids)
}
