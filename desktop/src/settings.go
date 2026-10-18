package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"sync"
)

type Locale string

const (
	LocaleZH Locale = "zh"
	LocaleEN Locale = "en"
)

// Settings is persisted at ~/.octop/desktop-settings.json
type Settings struct {
	Locale          Locale `json:"locale"`
	Autostart       bool   `json:"autostart"`
	MinimizeToTray  bool   `json:"minimizeToTray"`
	PreventSleep    bool   `json:"preventSleep"`
	Port            int    `json:"port,omitempty"`
	ConnectionMode  string `json:"connectionMode,omitempty"`
	CompanyURL      string `json:"companyUrl,omitempty"`
	CompanyRemember bool   `json:"companyRemember,omitempty"`
}

func defaultSettings() Settings {
	locale := LocaleEN
	if companyOnly {
		locale = LocaleZH
	}
	return Settings{
		Locale:         locale,
		Autostart:      false,
		MinimizeToTray: true,
		PreventSleep:   false,
		Port:           8088,
		ConnectionMode: "company",
	}
}

func octopHome() string {
	if v := os.Getenv("OCTOP_HOME"); v != "" {
		return v
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ".octop"
	}
	return filepath.Join(home, ".octop")
}

func portableDir() string {
	return filepath.Join(octopHome(), "portable")
}

func settingsPath() string {
	return filepath.Join(octopHome(), "desktop-settings.json")
}

type settingsStore struct {
	mu  sync.Mutex
	cur Settings
}

func loadSettings() Settings {
	s := defaultSettings()
	data, err := os.ReadFile(settingsPath())
	if err != nil {
		return s
	}
	_ = json.Unmarshal(data, &s)
	var legacy struct {
		PreventSleepMac bool    `json:"preventSleepMac"`
		ConnectionMode  *string `json:"connectionMode"`
	}
	_ = json.Unmarshal(data, &legacy)
	// Preserve independent mode only for files written before the connection UI.
	if legacy.ConnectionMode == nil {
		s.ConnectionMode = "local"
		if companyServerURL(s) != "" {
			s.ConnectionMode = "company"
		}
	}
	if !s.PreventSleep {
		s.PreventSleep = legacy.PreventSleepMac
	}
	if s.Port == 0 {
		s.Port = 8088
	}
	if s.Locale != LocaleZH {
		s.Locale = LocaleEN
	}
	return s
}

func (st *settingsStore) get() Settings {
	st.mu.Lock()
	defer st.mu.Unlock()
	return st.cur
}

func (st *settingsStore) save(next Settings) error {
	st.mu.Lock()
	defer st.mu.Unlock()
	// Ordinary preference bindings cannot change the trusted server target.
	next.ConnectionMode, next.CompanyURL = st.cur.ConnectionMode, st.cur.CompanyURL
	next.CompanyRemember = st.cur.CompanyRemember
	return st.saveLocked(next)
}

func (st *settingsStore) saveLocale(locale Locale) error {
	st.mu.Lock()
	defer st.mu.Unlock()
	next := st.cur
	next.Locale = locale
	return st.saveLocked(next)
}

func (st *settingsStore) saveConnection(mode, base string) error {
	st.mu.Lock()
	defer st.mu.Unlock()
	next := st.cur
	if next.CompanyURL != base {
		next.CompanyRemember = false
	}
	next.ConnectionMode, next.CompanyURL = mode, base
	return st.saveLocked(next)
}

func (st *settingsStore) saveCompanyRemember(remember bool) error {
	st.mu.Lock()
	defer st.mu.Unlock()
	next := st.cur
	next.CompanyRemember = remember
	return st.saveLocked(next)
}

func (st *settingsStore) saveLocked(next Settings) error {
	if next.Port == 0 {
		next.Port = 8088
	}
	if err := os.MkdirAll(octopHome(), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(next, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(settingsPath(), data, 0o644); err != nil {
		return err
	}
	st.cur = next
	return nil
}

func greenPlat() string {
	osName := runtime.GOOS
	arch := runtime.GOARCH
	switch osName {
	case "darwin":
		osName = "darwin"
	case "windows":
		osName = "windows"
	default:
		osName = "linux"
	}
	switch arch {
	case "arm64":
		arch = "arm64"
	default:
		arch = "amd64"
	}
	return osName + "-" + arch
}
