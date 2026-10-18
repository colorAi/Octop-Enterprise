package main

import (
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"runtime"
	"sync"
	"sync/atomic"
	"time"

	"octop.desktop/internal/company"
	"octop.desktop/internal/companyruntime"
	"octop.desktop/internal/companyui"

	"github.com/wailsapp/wails/v3/pkg/application"
	"github.com/wailsapp/wails/v3/pkg/events"
)

//go:embed assets/* assets/login.bundle.js assets/login.bundle.css
var assets embed.FS

const trayDoubleClick = 400 * time.Millisecond

// Optional deployment default; user-saved server settings take precedence.
var companyDefaultURL string

func companyServerURL(s Settings) string {
	if s.CompanyURL != "" {
		return s.CompanyURL
	}
	if value, exists := os.LookupEnv("OCTOP_COMPANY_URL"); exists {
		return value
	}
	return companyDefaultURL
}

// App is the Wails service bound to the shell UI.
type App struct {
	app              *application.App
	window           *application.WebviewWindow
	settingsWindow   *application.WebviewWindow
	tray             *application.SystemTray
	store            *settingsStore
	sleep            *sleepGuard
	cmd              *exec.Cmd
	mu               sync.Mutex
	quitting         bool
	company          *company.Client
	companyURL       string
	companyUI        *companyui.Server
	companyWindow    *application.WebviewWindow
	companyMu        sync.Mutex
	runtimeStartMu   sync.Mutex // Serializes worker startup without blocking previews or account changes.
	bootID           uint64
	loginMu          sync.Mutex
	loginVisible     bool
	loginSession     *loginHandoff
	resumeOrigin     string
	loginBusy        atomic.Bool
	workspaceNonce   string
	workspacePath    string
	workspaceGrant   *company.Grant
	workspaceError   string
	workspaceBusy    atomic.Bool
	companyRuntime   *companyruntime.Process
	runtimeArtifacts map[string]bool
	runtimeEpoch     uint64

	trayClickMu    sync.Mutex
	lastTrayClick  time.Time
	trayClickTimer *time.Timer
}

func (a *App) ServiceName() string { return "desktop" }

func (a *App) ServiceStartup(context.Context, application.ServiceOptions) error { return nil }

func (a *App) ServiceShutdown() error {
	a.companyMu.Lock()
	a.stopCompanyRuntimeLocked()
	if a.companyUI != nil {
		a.companyUI.Close()
		a.companyUI = nil
	}
	if a.company != nil {
		a.company.Close(true)
	}
	a.companyMu.Unlock()
	a.sleep.stop()
	a.mu.Lock()
	cmd := a.cmd
	a.cmd = nil
	a.mu.Unlock()
	stopOctop(cmd)
	return nil
}

func (a *App) GetSettings() Settings {
	return a.store.get()
}

// SaveLoginLocale is available before authentication and only changes local UI language.
func (a *App) SaveLoginLocale(locale Locale) (Settings, error) {
	if locale != LocaleZH && locale != LocaleEN {
		return a.store.get(), fmt.Errorf("unsupported locale")
	}
	if err := a.store.saveLocale(locale); err != nil {
		return a.store.get(), err
	}
	a.applyDesktopBranding(locale)
	return a.store.get(), nil
}

func (a *App) GetSettingsStatus() (Settings, error) {
	s := a.store.get()
	if a.app == nil {
		return s, nil
	}
	status, err := a.app.Autostart.Status()
	if err != nil {
		return s, fmt.Errorf("read autostart status: %w", err)
	}
	if s.Autostart != status.Enabled {
		s.Autostart = status.Enabled
		if err := a.store.save(s); err != nil {
			return s, err
		}
	}
	return s, nil
}

func (a *App) SaveSettings(next Settings) (Settings, error) {
	cur, err := a.GetSettingsStatus()
	if err != nil {
		return cur, err
	}
	autostart, err := a.setAutostart(next.Autostart)
	if err != nil {
		return cur, err
	}
	if err := a.sleep.set(next.PreventSleep); err != nil {
		if _, rollbackErr := a.setAutostart(cur.Autostart); rollbackErr != nil {
			log.Printf("rollback autostart after sleep prevention failure: %v", rollbackErr)
		}
		return cur, err
	}
	next.Autostart = autostart
	if err := a.store.save(next); err != nil {
		return cur, err
	}
	saved := a.store.get()
	a.applyDashboardPrefs(saved)
	return saved, nil
}

func (a *App) ShowMain() {
	a.showWindow()
}

func (a *App) HideSettings() {
	if a.settingsWindow == nil {
		return
	}
	a.settingsWindow.Hide()
}

func (a *App) Quit() {
	a.requestQuit()
}

func (a *App) setAutostart(on bool) (bool, error) {
	if a.app == nil {
		return false, fmt.Errorf("autostart is unavailable before the application starts")
	}
	if on {
		if err := a.app.Autostart.Enable(); err != nil {
			return false, fmt.Errorf("enable autostart: %w", err)
		}
	} else if err := a.app.Autostart.Disable(); err != nil {
		return false, fmt.Errorf("disable autostart: %w", err)
	}
	status, err := a.app.Autostart.Status()
	if err != nil {
		return false, fmt.Errorf("read autostart status: %w", err)
	}
	if status.Enabled != on {
		return status.Enabled, fmt.Errorf("autostart state did not update")
	}
	return status.Enabled, nil
}

func (a *App) applyDashboardPrefs(s Settings) {
	a.applyDesktopBranding(s.Locale)
	if a.window == nil {
		return
	}
	js := fmt.Sprintf(
		`(function(){try{localStorage.setItem('octop:ui-locale',%s);}catch(e){}})();`,
		jsonString(string(s.Locale)),
	)
	a.window.ExecJS(js)
}

func jsonString(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

func (a *App) setStatus(msg string) {
	if a.app == nil {
		return
	}
	a.app.Event.Emit("desktop:status", msg)
}

func (a *App) boot() {
	a.mu.Lock()
	a.bootID++
	bootID := a.bootID
	a.mu.Unlock()
	locale := LocaleEN
	if a.store != nil {
		locale = a.store.get().Locale
	}
	s := a.store.get()
	if companyOnly || s.ConnectionMode != "local" {
		// The bundled employee form is immediately usable, including offline
		// server editing. There is no preliminary connection screen.
		if s.CompanyRemember {
			a.resumeCompanyLogin(companyServerURL(s))
		}
		return
	}
	if url := os.Getenv("OCTOP_DESKTOP_URL"); url != "" && !companyOnly {
		a.setStatus(desktopText(locale, copyStatusConnecting))
		if err := waitHealth(locale, url, 60*time.Second); err != nil {
			a.setStatus(err.Error())
			return
		}
		a.showBootDashboard(bootID, url)
		return
	}
	a.setStatus(desktopText(locale, copyStatusCheckingRuntime))
	if err := ensurePortable(locale, a.setStatus); err != nil {
		a.setStatus(err.Error())
		return
	}
	root := portableDir()
	a.mu.Lock()
	if a.bootID != bootID {
		a.mu.Unlock()
		return
	}
	stopOctop(a.cmd)
	cmd, err := startOctop(root, s.Port)
	a.cmd = cmd
	a.mu.Unlock()
	if err != nil {
		a.setStatus(err.Error())
		return
	}
	base := dashboardURL(s.Port)
	a.setStatus(desktopText(locale, copyStatusStartingService))
	if err := waitHealth(locale, base, 2*time.Minute); err != nil {
		a.setStatus(err.Error())
		return
	}
	a.showBootDashboard(bootID, base)
}

// A slow local startup must not navigate over a newly selected company server.
func (a *App) showBootDashboard(id uint64, base string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.bootID == id {
		a.showDashboard(base)
	}
}

func (a *App) showDashboard(base string) {
	if a.window == nil {
		return
	}
	a.window.SetURL(base)
	a.scheduleDragOverlay()
	s := a.store.get()
	go func() {
		time.Sleep(800 * time.Millisecond)
		a.applyDashboardPrefs(a.store.get())
	}()
	a.setStatus(desktopText(s.Locale, copyStatusReady))
}

func (a *App) hideToTray() {
	if a.window == nil {
		return
	}
	a.window.Hide()
}

func (a *App) showWindow() {
	if a.window == nil {
		return
	}
	if a.window.IsMinimised() {
		a.window.UnMinimise()
	}
	a.window.Show()
	a.window.Focus()
}

func (a *App) toggleMainWindow() {
	if a.window == nil {
		return
	}
	if a.window.IsVisible() && !a.window.IsMinimised() {
		a.hideToTray()
		return
	}
	a.showWindow()
}

func (a *App) onTrayLeftClick() {
	a.trayClickMu.Lock()
	defer a.trayClickMu.Unlock()
	if a.trayClickTimer != nil {
		a.trayClickTimer.Stop()
		a.trayClickTimer = nil
	}
	now := time.Now()
	if !a.lastTrayClick.IsZero() && now.Sub(a.lastTrayClick) < trayDoubleClick {
		a.lastTrayClick = time.Time{}
		go a.toggleMainWindow()
		return
	}
	a.lastTrayClick = now
	a.trayClickTimer = time.AfterFunc(trayDoubleClick, func() {
		a.trayClickMu.Lock()
		a.trayClickTimer = nil
		a.trayClickMu.Unlock()
		a.showWindow()
	})
}

func (a *App) installDragOverlay() {
	if a.window == nil || !mainWindowFrameless(runtime.GOOS) {
		return
	}
	a.window.ExecJS(dragOverlayJS())
}

func (a *App) scheduleDragOverlay() {
	go func() {
		for range 40 {
			time.Sleep(250 * time.Millisecond)
			a.installNativeWindowChrome()
			a.installWindowTheme()
			a.installDragOverlay()
			a.installExternalLinks()
			a.installConnectionPanel(false)
			a.installLoginHandoff()
		}
	}()
}

func (a *App) requestQuit() {
	a.mu.Lock()
	a.quitting = true
	a.mu.Unlock()
	if a.app != nil {
		a.app.Quit()
	}
}

func main() {
	store := &settingsStore{cur: loadSettings()}
	if companyOnly {
		// Tool verification needs no login or company credentials. Start it
		// alongside the login window; the worker joins the same preparation.
		go func() { _, _ = ensureCompanyPortable() }()
	}
	api := &App{
		store:   store,
		sleep:   &sleepGuard{},
		company: company.NewClient(),
	}
	if companyOnly || store.get().ConnectionMode != "local" {
		api.companyURL, _ = company.BaseURL(companyServerURL(store.get()))
	}

	app := application.New(application.Options{
		Name:        "Octop",
		Description: "Octop desktop",
		Flags: map[string]any{
			"octopNativeTitlebar": !mainWindowFrameless(runtime.GOOS),
		},
		Services: []application.Service{
			application.NewService(api),
		},
		Assets: application.AssetOptions{
			Handler: application.BundledAssetFileServer(assets),
		},
		Windows: application.WindowsOptions{
			DisableQuitOnLastWindowClosed: true,
		},
		Linux: application.LinuxOptions{
			DisableQuitOnLastWindowClosed: true,
		},
		Mac: application.MacOptions{
			ApplicationShouldTerminateAfterLastWindowClosed: false,
		},
	})
	api.app = app
	attachOpenURLEventListener(app, api.OpenExternal)
	attachDesktopEventListener(app, &connectionEventListener{app: api})
	app.Event.OnApplicationEvent(events.Common.ApplicationStarted, func(_ *application.ApplicationEvent) {
		applyAppIcon(app)
		if api.window != nil {
			api.window.SetBackgroundColour(desktopBackgroundColour(app.Env.IsDarkMode()))
		}
	})

	initialURL := "/"
	if companyOnly || store.get().ConnectionMode != "local" {
		initialURL = "/login.html"
		api.loginVisible = true
	}
	win := app.Window.NewWithOptions(application.WebviewWindowOptions{
		Name:                 "main",
		Title:                desktopProductName(store.get().Locale),
		Width:                1200,
		Height:               800,
		URL:                  initialURL,
		Frameless:            mainWindowFrameless(runtime.GOOS),
		AllowSimpleEventEmit: true,
		BackgroundColour:     desktopBackgroundColour(app.Env.IsDarkMode()),
		Mac: application.MacWindow{
			TitleBar: application.MacTitleBarDefault,
			WebviewPreferences: application.MacWebviewPreferences{
				// Available before page scripts run, including remote dashboard navigation.
				ApplicationNameForUserAgent: "wails.io OctopNativeTitlebar/1",
			},
		},
	})
	api.window = win
	app.Event.On("desktop:show-device", func(event *application.CustomEvent) {
		if event.Sender == win.Name() {
			api.ShowDevicePanel()
		}
	})
	app.Event.On("desktop:toggle-maximise", func(_ *application.CustomEvent) {
		win.ToggleMaximise()
	})
	app.Event.On("desktop:minimise", func(_ *application.CustomEvent) {
		win.Minimise()
	})
	app.Event.On("desktop:close", func(_ *application.CustomEvent) {
		api.hideToTray()
	})
	var initialBoot sync.Once
	installDragOverlay := func(_ *application.WindowEvent) {
		api.scheduleDragOverlay()
		// Wait for the native windows and bundled page before navigating or showing
		// setup. Calls made before app.Run can be overwritten by window creation.
		initialBoot.Do(func() { go api.boot() })
	}
	win.OnWindowEvent(events.Mac.WebViewDidFinishNavigation, installDragOverlay)
	win.OnWindowEvent(events.Windows.WebViewNavigationCompleted, installDragOverlay)
	win.OnWindowEvent(events.Linux.WindowLoadFinished, installDragOverlay)
	settingsWin := app.Window.NewWithOptions(application.WebviewWindowOptions{
		Title:            desktopSettingsTitle(store.get().Locale),
		Width:            settingsWindowWidth,
		Height:           settingsWindowOuterHeight(),
		URL:              "/?settings=1",
		Hidden:           true,
		Frameless:        true,
		AlwaysOnTop:      true,
		DisableResize:    true,
		BackgroundColour: application.NewRGB(255, 255, 255),
		Windows: application.WindowsWindow{
			HiddenOnTaskbar: true,
		},
	})
	api.settingsWindow = settingsWin
	api.companyWindow = app.Window.NewWithOptions(application.WebviewWindowOptions{
		Title: desktopPermissionsTitle(store.get().Locale),
		Width: 580, Height: 760, URL: "/company.html", Hidden: true,
	})
	api.companyWindow.RegisterHook(events.Common.WindowClosing, func(e *application.WindowEvent) {
		e.Cancel()
		api.companyWindow.Hide()
	})
	app.Event.RegisterApplicationEventHook(events.Mac.ApplicationShouldHandleReopen, func(event *application.ApplicationEvent) {
		event.Cancel()
		restoreMainAfterDockClick(api.window, api.settingsWindow)
	})

	win.RegisterHook(events.Common.WindowClosing, func(e *application.WindowEvent) {
		api.mu.Lock()
		quit := api.quitting
		api.mu.Unlock()
		if quit {
			return
		}
		e.Cancel()
		api.hideToTray()
	})
	win.OnWindowEvent(events.Common.WindowMinimise, func(_ *application.WindowEvent) {
		if api.store.get().MinimizeToTray {
			api.hideToTray()
		}
	})
	settingsWin.RegisterHook(events.Common.WindowClosing, func(e *application.WindowEvent) {
		e.Cancel()
		settingsWin.Hide()
	})
	settingsWin.OnWindowEvent(events.Common.WindowLostFocus, func(_ *application.WindowEvent) {
		settingsWin.Hide()
	})

	tray := app.SystemTray.New()
	api.tray = tray
	applyTrayIcon(tray)
	tray.SetTooltip(desktopProductName(store.get().Locale))
	tray.AttachWindow(settingsWin).WindowOffset(6)
	showSettings := func() { tray.ShowWindow() }
	if trayLeftClickShowsSettings(runtime.GOOS) {
		tray.OnClick(showSettings)
	} else {
		tray.OnClick(func() { api.onTrayLeftClick() })
	}
	tray.OnRightClick(showSettings)

	if _, err := api.setAutostart(store.get().Autostart); err != nil {
		log.Printf("sync autostart: %v", err)
	}
	if err := api.sleep.set(store.get().PreventSleep); err != nil {
		log.Printf("enable sleep prevention: %v", err)
	}

	api.scheduleDragOverlay()

	if err := app.Run(); err != nil {
		log.Fatal(err)
	}
}
