package main

import "strings"

func desktopProductName(locale Locale) string {
	if locale == LocaleZH {
		return "Octop 企业版"
	}
	return "Octop Enterprise"
}

func desktopSettingsTitle(locale Locale) string {
	if locale == LocaleZH {
		return desktopProductName(locale) + "设置"
	}
	return desktopProductName(locale) + " Settings"
}

func desktopPermissionsTitle(locale Locale) string {
	if locale == LocaleZH {
		return desktopProductName(locale) + " — 本机文件授权"
	}
	return desktopProductName(locale) + " — Local file permissions"
}

func parseWindowLocaleEvent(name string) (Locale, bool) {
	const prefix = "desktop:locale:"
	if !strings.HasPrefix(name, prefix) {
		return "", false
	}
	locale := Locale(strings.TrimPrefix(name, prefix))
	return locale, locale == LocaleZH || locale == LocaleEN
}

func (a *App) applyDesktopBranding(locale Locale) {
	if a.window != nil {
		a.window.SetTitle(desktopProductName(locale))
	}
	if a.settingsWindow != nil {
		a.settingsWindow.SetTitle(desktopSettingsTitle(locale))
	}
	if a.companyWindow != nil {
		a.companyWindow.SetTitle(desktopPermissionsTitle(locale))
	}
	if a.tray != nil {
		a.tray.SetTooltip(desktopProductName(locale))
	}
}
