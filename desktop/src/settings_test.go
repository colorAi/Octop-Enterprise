package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadSettingsMigratesLegacyPreventSleepMac(t *testing.T) {
	temp := t.TempDir()
	t.Setenv("OCTOP_HOME", temp)
	if err := os.WriteFile(
		filepath.Join(temp, "desktop-settings.json"),
		[]byte(`{"preventSleepMac":true}`),
		0o644,
	); err != nil {
		t.Fatal(err)
	}

	if !loadSettings().PreventSleep {
		t.Fatal("legacy preventSleepMac should migrate to preventSleep")
	}
}

func TestSettingsConnectionFirstRunAndLegacy(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	t.Setenv("OCTOP_COMPANY_URL", "")
	if loadSettings().ConnectionMode != "company" {
		t.Fatal("fresh install must ask for a server")
	}
	store := &settingsStore{cur: loadSettings()}
	if err := store.save(store.get()); err != nil {
		t.Fatal(err)
	}
	if loadSettings().ConnectionMode != "company" {
		t.Fatal("saving preferences before connecting must not select local mode")
	}
	if err := os.WriteFile(settingsPath(), []byte(`{"locale":"zh"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if loadSettings().ConnectionMode != "local" {
		t.Fatal("existing independent installation must stay local")
	}
	t.Setenv("OCTOP_COMPANY_URL", "https://managed.company.test")
	if loadSettings().ConnectionMode != "company" {
		t.Fatal("legacy deployment configuration should still connect")
	}
}

func TestSettingsPreferencesCannotOverwriteConnection(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	store := &settingsStore{cur: defaultSettings()}
	stale := store.get()
	if err := store.saveConnection("company", "https://octop.internal:8443"); err != nil {
		t.Fatal(err)
	}
	stale.CompanyURL, stale.ConnectionMode, stale.Locale = "https://untrusted.test", "local", LocaleZH
	if err := store.save(stale); err != nil {
		t.Fatal(err)
	}
	saved := loadSettings()
	if saved.CompanyURL != "https://octop.internal:8443" || saved.ConnectionMode != "company" || saved.Locale != LocaleZH {
		t.Fatal("ordinary settings overwrote the trusted server", saved)
	}
}

func TestSaveLoginLocalePreservesConnectionAndPreferences(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	settings := defaultSettings()
	settings.CompanyURL = "https://company.test"
	settings.CompanyRemember = true
	settings.PreventSleep = true
	app := &App{store: &settingsStore{cur: settings}}
	if _, err := app.SaveLoginLocale(LocaleEN); err != nil {
		t.Fatal(err)
	}
	want := settings
	want.Locale = LocaleEN
	if got := loadSettings(); got != want {
		t.Fatal("login locale altered other preferences")
	}
	if _, err := app.SaveLoginLocale(Locale("invalid")); err == nil {
		t.Fatal("invalid locale accepted")
	}
	if got := loadSettings(); got != want {
		t.Fatal("invalid locale changed saved preferences")
	}
}
