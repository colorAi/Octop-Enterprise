package main

import (
	"net/url"
	"octop.desktop/internal/companyui"
	"path/filepath"
)

// Caller holds companyMu; server identity remains the real company origin.
func (a *App) companyPageOriginLocked() string {
	if a.companyUI != nil {
		return a.companyUI.Origin
	}
	return a.companyURL
}

func (a *App) companyUIEntry(base, fragment string) (string, error) {
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	if a.companyUI == nil || a.companyUI.Base != base {
		bundle, err := assets.ReadFile("assets/company-dashboard.zip")
		if err != nil {
			return "", err
		}
		next, err := companyui.Start(base, bundle, filepath.Join(octopHome(), "company-ui"))
		if err != nil {
			return "", err
		}
		if a.companyUI != nil {
			a.companyUI.Close()
		}
		a.companyUI = next
	}
	entry, err := url.Parse(a.companyUI.Entry(fragment))
	if err != nil {
		return "", err
	}
	query := entry.Query()
	query.Set("ui_locale", string(a.store.get().Locale))
	entry.RawQuery = query.Encode()
	return entry.String(), nil
}
