package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"github.com/wailsapp/wails/v3/pkg/application"
	"log"
	"strings"
)

type connectionEventListener struct{ app *App }

// Workspace requests are scoped to a nonce delivered only to the selected origin.
// The only credential accepted is a short-lived, single-use device pairing code;
// account tokens, passwords, file paths and general native calls are not accepted.
func (l *connectionEventListener) DispatchWailsEvent(event *application.CustomEvent) {
	if event == nil || l.app.window == nil || event.Sender != l.app.window.Name() {
		return
	}
	if color, ok := parseWindowBackgroundEvent(event.Name); ok {
		l.app.window.SetBackgroundColour(color)
		return
	}
	if locale, ok := parseWindowLocaleEvent(event.Name); ok {
		if _, err := l.app.SaveLoginLocale(locale); err != nil {
			log.Printf("save interface language: %v", err)
		}
		return
	}
	if strings.HasPrefix(event.Name, runtimeEventPrefix) {
		l.app.dispatchRuntimeEvent(event.Name)
		return
	}
	if strings.HasPrefix(event.Name, workspaceEventPrefix) {
		l.app.dispatchWorkspaceEvent(event.Name)
		return
	}
	if strings.HasPrefix(event.Name, "desktop:login-complete:") {
		l.app.loginMu.Lock()
		if session := l.app.loginSession; session != nil && event.Name == "desktop:login-complete:"+session.Nonce {
			l.app.loginSession = nil
		}
		l.app.loginMu.Unlock()
		return
	}
	if event.Name == "desktop:login-resumed" {
		l.app.loginMu.Lock()
		l.app.resumeOrigin = ""
		l.app.loginMu.Unlock()
		return
	}
	if event.Name == "desktop:show-login" {
		l.app.showLogin(true)
	}
}

func (a *App) installConnectionPanel(open bool) {
	if a.window == nil {
		return
	}
	source, err := assets.ReadFile("assets/connection-panel.js")
	if err != nil {
		return
	}
	a.companyMu.Lock()
	if a.workspaceNonce == "" {
		var nonce [24]byte
		if _, err := rand.Read(nonce[:]); err != nil {
			a.companyMu.Unlock()
			return
		}
		a.workspaceNonce = hex.EncodeToString(nonce[:])
	}
	data, _ := json.Marshal(map[string]any{"address": a.companyPageOriginLocked(), "nonce": a.workspaceNonce})
	a.companyMu.Unlock()
	a.window.ExecJS("(" + string(source) + ")(" + string(data) + ")")
}
