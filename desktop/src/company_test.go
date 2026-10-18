package main

import (
	"context"
	"os"
	"testing"

	"octop.desktop/internal/company"
)

func TestCompanyBindingsRejectWindowlessCallers(t *testing.T) {
	app := &App{company: company.NewClient()}
	ctx := context.Background()
	if _, err := app.GetLoginConfig(ctx, "http://company.test"); err == nil {
		t.Fatal("login config allowed without bundled main window")
	}
	if err := app.LoginCompany(ctx, company.LoginInput{}); err == nil {
		t.Fatal("login allowed without bundled main window")
	}
	if err := app.SaveLoginAddress(ctx, "http://company.test"); err == nil {
		t.Fatal("address save allowed without bundled main window")
	}
	if _, err := app.ConfigureCompany(ctx, "https://company.test"); err == nil {
		t.Fatal("server changed without native panel")
	}
	if err := app.UseLocalMode(ctx); err == nil {
		t.Fatal("local mode enabled without native panel")
	}
	if err := app.PairCompany(ctx, "octop-pair.test"); err == nil {
		t.Fatal("pair allowed without native panel")
	}
	if _, err := app.CompanyStatus(ctx); err == nil {
		t.Fatal("status allowed without native panel")
	}
	if err := app.DisconnectCompany(ctx); err == nil {
		t.Fatal("revoke allowed without native panel")
	}
	if _, err := app.CompanyGrants(ctx); err == nil {
		t.Fatal("grants allowed without native panel")
	}
	if _, err := app.AddCompanyDirectory(ctx, true, true); err == nil {
		t.Fatal("directory picker allowed without native panel")
	}
	if err := app.RevokeCompanyDirectory(ctx, "id"); err == nil {
		t.Fatal("directory revoke allowed without native panel")
	}
	if _, err := app.CompanyResults(ctx); err == nil {
		t.Fatal("receipts allowed without native panel")
	}
	if err := app.RevealCompanyResult(ctx, "id"); err == nil {
		t.Fatal("reveal allowed without native panel")
	}
}

func TestCompanyCompiledURLAndEnvironmentOverride(t *testing.T) {
	previous := companyDefaultURL
	companyDefaultURL = "https://octop.company.example"
	t.Cleanup(func() { companyDefaultURL = previous })
	original, exists := os.LookupEnv("OCTOP_COMPANY_URL")
	_ = os.Unsetenv("OCTOP_COMPANY_URL")
	t.Cleanup(func() {
		if exists {
			_ = os.Setenv("OCTOP_COMPANY_URL", original)
		} else {
			_ = os.Unsetenv("OCTOP_COMPANY_URL")
		}
	})
	if companyServerURL(Settings{}) != companyDefaultURL {
		t.Fatal("compiled company URL ignored")
	}
	t.Setenv("OCTOP_COMPANY_URL", "https://test.company.example")
	if companyServerURL(Settings{}) != "https://test.company.example" {
		t.Fatal("environment override ignored")
	}
	if companyServerURL(Settings{CompanyURL: "https://saved.company.test"}) != "https://saved.company.test" {
		t.Fatal("saved server must take precedence over deployment defaults")
	}
}

func TestCompanyServerSwitchPersistsAndReplacesDeviceClient(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	old := company.NewClient()
	app := &App{store: &settingsStore{cur: defaultSettings()}, company: old, companyURL: "https://old.company.test"}
	next := company.NewClient()
	if err := app.selectCompany("https://new.company.test", next); err != nil {
		t.Fatal(err)
	}
	if app.company != next || old.Status().State != "unpaired" || app.GetCompanyMode() != "https://new.company.test" {
		t.Fatal("old server device state survived the switch")
	}
	saved := loadSettings()
	if saved.CompanyURL != app.GetCompanyMode() || saved.ConnectionMode != "company" {
		t.Fatal("server selection not persisted")
	}
	if err := app.selectCompany(saved.CompanyURL, company.NewClient()); err != nil || app.company != next {
		t.Fatal("reopening the same server discarded the device")
	}
}

func TestCompanyServerSaveFailureLeavesCurrentConnection(t *testing.T) {
	root := t.TempDir()
	t.Setenv("OCTOP_HOME", root+"/blocked")
	if err := os.WriteFile(root+"/blocked", []byte("file"), 0o600); err != nil {
		t.Fatal(err)
	}
	old := company.NewClient()
	app := &App{store: &settingsStore{cur: defaultSettings()}, company: old, companyURL: "https://old.company.test", bootID: 1}
	if err := app.selectCompany("https://new.company.test", company.NewClient()); err == nil {
		t.Fatal("unexpected successful write")
	}
	if app.company != old || app.GetCompanyMode() != "https://old.company.test" || app.bootID != 1 {
		t.Fatal("failed save changed running server")
	}
}

func TestCompanyNewLoginReplacesDeviceEvenOnSameServer(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	old := company.NewClient()
	app := &App{store: &settingsStore{cur: defaultSettings()}, company: old, companyURL: "http://company.test"}
	next := company.NewClient()
	if err := app.rememberCompany("http://company.test", next, true); err != nil {
		t.Fatal(err)
	}
	if app.company != next {
		t.Fatal("new login inherited the previous account's device")
	}
}

func TestCompanyRememberedLoginDoesNotFollowServerChange(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	store := &settingsStore{cur: defaultSettings()}
	if err := store.saveConnection("company", "http://first.test"); err != nil {
		t.Fatal(err)
	}
	if err := store.saveCompanyRemember(true); err != nil {
		t.Fatal(err)
	}
	if !loadSettings().CompanyRemember {
		t.Fatal("remember preference was not saved")
	}
	if err := store.saveConnection("company", "http://second.test"); err != nil {
		t.Fatal(err)
	}
	if loadSettings().CompanyRemember {
		t.Fatal("old login followed a different server")
	}
}
