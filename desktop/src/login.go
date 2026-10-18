package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"time"

	"github.com/wailsapp/wails/v3/pkg/application"
	"octop.desktop/internal/company"
)

type loginHandoff struct {
	Origin   string `json:"origin"`
	Nonce    string `json:"nonce"`
	Token    string `json:"token"`
	Remember bool   `json:"remember"`
	expires  time.Time
}

func (a *App) requireLoginPanel(ctx context.Context) error {
	caller, ok := ctx.Value(application.WindowKey).(application.Window)
	a.loginMu.Lock()
	defer a.loginMu.Unlock()
	if !ok || caller == nil || a.window == nil || caller.ID() != a.window.ID() || !a.loginVisible {
		return errors.New("login requires the bundled employee login page")
	}
	return nil
}

func (a *App) GetLoginConfig(ctx context.Context, address string) (company.CaptchaConfig, error) {
	if err := a.requireLoginPanel(ctx); err != nil {
		return company.CaptchaConfig{}, err
	}
	return company.NewClient().LoginConfig(ctx, address)
}

func (a *App) SaveLoginAddress(ctx context.Context, address string) error {
	if err := a.requireLoginPanel(ctx); err != nil {
		return err
	}
	base, err := company.ServerAddress(address)
	if err != nil {
		return err
	}
	return a.store.saveConnection("company", base)
}

// The password stays in this request; it is never saved, logged or placed in an
// event name. The returned session stays native until the selected origin loads.
func (a *App) LoginCompany(ctx context.Context, input company.LoginInput) error {
	if err := a.requireLoginPanel(ctx); err != nil {
		return err
	}
	if !a.loginBusy.CompareAndSwap(false, true) {
		return errors.New("正在登录，请稍候 / Sign in already in progress")
	}
	defer a.loginBusy.Store(false)
	client := company.NewClient()
	base, result, err := client.Login(ctx, input)
	if err != nil {
		return err
	}
	// A fresh account session must never inherit another login's local grants,
	// including when both accounts use the same company server.
	if err = a.rememberCompany(base, client, true); err != nil {
		return err
	}
	if err = a.store.saveCompanyRemember(input.Remember); err != nil {
		return err
	}
	var nonce [24]byte
	if _, err = rand.Read(nonce[:]); err != nil {
		return err
	}
	target, err := a.companyUIEntry(base, hex.EncodeToString(nonce[:]))
	if err != nil {
		return err
	}
	a.companyMu.Lock()
	pageOrigin := a.companyPageOriginLocked()
	a.companyMu.Unlock()
	a.loginMu.Lock()
	a.loginVisible = false
	a.loginSession = &loginHandoff{Origin: pageOrigin, Nonce: hex.EncodeToString(nonce[:]), Token: result.AccessToken, Remember: input.Remember, expires: time.Now().Add(30 * time.Second)}
	pending := a.loginSession
	a.loginMu.Unlock()
	a.showDashboard(target)
	time.AfterFunc(30*time.Second, func() {
		a.loginMu.Lock()
		pendingStillActive := a.loginSession == pending
		if pendingStillActive {
			a.loginSession = nil
		}
		a.loginMu.Unlock()
		if pendingStillActive {
			a.showLogin(false)
		}
	})
	return nil
}

func (a *App) showLogin(open bool) {
	a.companyMu.Lock()
	a.stopCompanyRuntimeLocked()
	if a.company != nil && a.company.Status().State != "unpaired" {
		a.company.Close(true)
		a.company = company.NewClient()
	}
	a.workspaceNonce = ""
	a.workspacePath, a.workspaceError, a.workspaceGrant = "", "", nil
	a.companyMu.Unlock()
	a.loginMu.Lock()
	a.loginVisible = true
	a.loginSession = nil
	a.resumeOrigin = ""
	a.loginMu.Unlock()
	if a.window != nil {
		target := "/login.html"
		if open {
			target += "?server=1"
		}
		a.window.SetURL(target)
		a.showWindow()
	}
}

func (a *App) resumeCompanyLogin(raw string) {
	base, err := company.ServerAddress(raw)
	if err != nil {
		return
	}
	target, err := a.companyUIEntry(base, "resume")
	if err != nil {
		return
	}
	a.loginMu.Lock()
	a.resumeOrigin = base
	a.loginVisible = false
	a.loginMu.Unlock()
	a.showDashboard(target)
	// The bundled form remains available when a saved server is offline.
	go func() {
		time.Sleep(8 * time.Second)
		a.loginMu.Lock()
		pending := a.resumeOrigin == base
		a.loginMu.Unlock()
		if pending {
			a.showLogin(false)
		}
	}()
}

func (a *App) installLoginHandoff() {
	a.loginMu.Lock()
	defer a.loginMu.Unlock()
	if a.window == nil {
		return
	}
	// The resume page acknowledges through the early native bridge and verifies
	// the session itself. A second redirect here can race that verification.
	if a.loginSession == nil {
		return
	}
	if time.Now().After(a.loginSession.expires) {
		a.loginSession = nil
		return
	}
	source, err := assets.ReadFile("assets/login-handoff.js")
	if err != nil {
		return
	}
	data, _ := json.Marshal(a.loginSession)
	a.window.ExecJS("(" + string(source) + ")(" + string(data) + ")")
}
