package main

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"runtime"

	"github.com/wailsapp/wails/v3/pkg/application"
	"octop.desktop/internal/company"
)

func (a *App) GetCompanyMode() string {
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	return a.companyURL
}

func (a *App) SupportsLocalMode() bool { return !companyOnly }

// ConfigureCompany is only callable from the bundled server/device panel.
// Passwords and account tokens never participate in this connection check.
func (a *App) ConfigureCompany(ctx context.Context, raw string) (string, error) {
	if err := a.requireCompanyPanel(ctx); err != nil {
		return "", err
	}
	client := company.NewClient()
	base, err := client.CheckServer(ctx, raw)
	if err != nil {
		return "", err
	}
	if err := a.selectCompany(base, client); err != nil {
		return "", err
	}
	a.showWindow()
	if a.companyWindow != nil {
		a.companyWindow.Hide()
	}
	return base, nil
}

func (a *App) selectCompany(base string, client *company.Client) error {
	if err := a.rememberCompany(base, client, false); err != nil {
		return err
	}
	a.showLogin(false)
	return nil
}

func (a *App) rememberCompany(base string, client *company.Client, newSession bool) error {
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	if err := a.store.saveConnection("company", base); err != nil {
		return err
	}
	if newSession || base != a.companyURL {
		a.stopCompanyRuntimeLocked()
		if base != a.companyURL && a.companyUI != nil {
			a.companyUI.Close()
			a.companyUI = nil
		}
		if a.company != nil {
			a.company.Close(true)
		}
		a.company, a.companyURL = client, base
		a.workspaceNonce = ""
		a.workspacePath, a.workspaceError, a.workspaceGrant = "", "", nil
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	a.bootID++
	stopOctop(a.cmd)
	a.cmd = nil
	return nil
}

func (a *App) UseLocalMode(ctx context.Context) error {
	if err := a.requireCompanyPanel(ctx); err != nil {
		return err
	}
	if companyOnly {
		return errors.New("企业客户端请连接公司服务器 / Connect this company client to your server")
	}
	a.companyMu.Lock()
	if err := a.store.saveConnection("local", ""); err != nil {
		a.companyMu.Unlock()
		return err
	}
	if a.company != nil {
		a.company.Close(true)
	}
	a.stopCompanyRuntimeLocked()
	if a.companyUI != nil {
		a.companyUI.Close()
		a.companyUI = nil
	}
	a.workspaceNonce = ""
	a.workspacePath, a.workspaceError, a.workspaceGrant = "", "", nil
	a.company, a.companyURL = company.NewClient(), ""
	a.mu.Lock()
	a.bootID++
	if a.window != nil {
		a.window.SetURL("/")
	}
	a.mu.Unlock()
	a.companyMu.Unlock()
	a.showWindow()
	if a.companyWindow != nil {
		a.companyWindow.Hide()
	}
	go a.boot()
	return nil
}

func (a *App) ShowCompany() {
	a.showWindow()
	if a.settingsWindow != nil {
		a.settingsWindow.Hide()
	}
	a.showLogin(true)
}

func (a *App) ShowDevicePanel() {
	if a.companyWindow != nil {
		a.companyWindow.Show()
		a.companyWindow.Focus()
	}
}

// Sensitive bindings are available only to the bundled, dedicated device panel.
// The remote company dashboard, external pages and windowless transports fail closed.
func (a *App) requireCompanyPanel(ctx context.Context) error {
	caller, ok := ctx.Value(application.WindowKey).(application.Window)
	if !ok || caller == nil || a.companyWindow == nil || caller.ID() != a.companyWindow.ID() {
		return errors.New("company device controls require the native device panel")
	}
	return nil
}

func (a *App) PairCompany(ctx context.Context, code string) error {
	if err := a.requireCompanyPanel(ctx); err != nil {
		return err
	}
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	name, _ := os.Hostname()
	if name == "" {
		name = "Octop desktop"
	}
	if len(name) > 80 {
		name = "Octop desktop"
	}
	return a.company.Pair(ctx, a.companyURL, code, name, runtime.GOOS)
}

func (a *App) CompanyStatus(ctx context.Context) (company.Status, error) {
	if err := a.requireCompanyPanel(ctx); err != nil {
		return company.Status{}, err
	}
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	return a.company.Status(), nil
}

func (a *App) DisconnectCompany(ctx context.Context) error {
	if err := a.requireCompanyPanel(ctx); err != nil {
		return err
	}
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	a.stopCompanyRuntimeLocked()
	a.company.Close(true)
	return nil
}

func (a *App) CompanyGrants(ctx context.Context) ([]company.Grant, error) {
	if err := a.requireCompanyPanel(ctx); err != nil {
		return nil, err
	}
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	return a.company.Grants(), nil
}
func (a *App) AddCompanyDirectory(ctx context.Context, writable, allowCompany bool) (company.Grant, error) {
	if err := a.requireCompanyPanel(ctx); err != nil {
		return company.Grant{}, err
	}
	a.companyMu.Lock()
	client := a.company
	a.companyMu.Unlock()
	path, err := a.app.Dialog.OpenFile().CanChooseFiles(false).CanChooseDirectories(true).ResolvesAliases(true).AttachToWindow(a.companyWindow).PromptForSingleSelection()
	if err != nil || path == "" {
		return company.Grant{}, err
	}
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	if client != a.company {
		return company.Grant{}, errors.New("服务器已切换，请重新选择目录 / Server changed; select the directory again")
	}
	return client.AddGrant(ctx, path, writable, allowCompany)
}
func (a *App) RevokeCompanyDirectory(ctx context.Context, id string) error {
	if err := a.requireCompanyPanel(ctx); err != nil {
		return err
	}
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	return a.company.RevokeGrant(ctx, id)
}
func (a *App) CompanyResults(ctx context.Context) ([]company.FileResult, error) {
	if err := a.requireCompanyPanel(ctx); err != nil {
		return nil, err
	}
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	return a.company.Results(), nil
}
func (a *App) RevealCompanyResult(ctx context.Context, id string) error {
	if err := a.requireCompanyPanel(ctx); err != nil {
		return err
	}
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	path, err := a.company.ResultPath(id)
	if err != nil {
		return err
	}
	if runtime.GOOS == "darwin" {
		return exec.Command("/usr/bin/open", "-R", path).Run()
	}
	if runtime.GOOS == "windows" {
		command := exec.Command("explorer.exe", "/select,"+path)
		if err := command.Start(); err != nil {
			return err
		}
		return command.Process.Release()
	}
	return errors.New("unsupported platform")
}
