package main

import (
	"net/url"
	"testing"
)

func TestWindowLocaleEventAllowsOnlySupportedLanguages(t *testing.T) {
	for _, locale := range []Locale{LocaleZH, LocaleEN} {
		got, ok := parseWindowLocaleEvent("desktop:locale:" + string(locale))
		if !ok || got != locale {
			t.Fatalf("supported interface language rejected: %q", locale)
		}
	}
	for _, name := range []string{"zh", "locale:en", "desktop:locale:", "desktop:locale:en:other", "desktop:locale:zh-CN", "desktop:locale:../settings", "desktop:background:zh"} {
		if _, ok := parseWindowLocaleEvent(name); ok {
			t.Fatalf("malformed locale event accepted: %q", name)
		}
	}
}

func TestCompanyUIEntrySeedsLocaleBeforeSavedOrFreshLogin(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	a := &App{store: &settingsStore{cur: defaultSettings()}}
	t.Cleanup(func() {
		if a.companyUI != nil {
			a.companyUI.Close()
		}
	})
	for _, locale := range []Locale{LocaleZH, LocaleEN} {
		if err := a.store.saveLocale(locale); err != nil {
			t.Fatal(err)
		}
		for _, fragment := range []string{"resume", "fresh-login-nonce"} {
			entry, err := a.companyUIEntry("https://company.test", fragment)
			if err != nil {
				t.Fatal(err)
			}
			u, err := url.Parse(entry)
			if err != nil || u.Fragment != fragment || u.Query().Get("ui_locale") != string(locale) || len(u.Query()) != 1 {
				t.Fatal("bootstrap lost the selected locale or login handoff")
			}
		}
	}
}
