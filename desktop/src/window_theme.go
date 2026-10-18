package main

import (
	"strconv"
	"strings"

	"github.com/wailsapp/wails/v3/pkg/application"
)

const windowBackgroundEventPrefix = "desktop:background:"

func desktopBackgroundColour(dark bool) application.RGBA {
	if dark {
		return application.NewRGB(14, 14, 14)
	}
	return application.NewRGB(245, 246, 248)
}

func parseWindowBackgroundEvent(name string) (application.RGBA, bool) {
	if !strings.HasPrefix(name, windowBackgroundEventPrefix) {
		return application.RGBA{}, false
	}
	parts := strings.Split(strings.TrimPrefix(name, windowBackgroundEventPrefix), ":")
	if len(parts) != 3 {
		return application.RGBA{}, false
	}
	var rgb [3]uint8
	for i, part := range parts {
		value, err := strconv.ParseUint(part, 10, 8)
		if err != nil {
			return application.RGBA{}, false
		}
		rgb[i] = uint8(value)
	}
	return application.NewRGB(rgb[0], rgb[1], rgb[2]), true
}

func (a *App) installWindowTheme() {
	if a.window == nil {
		return
	}
	source, err := assets.ReadFile("assets/window-theme.js")
	if err == nil {
		a.window.ExecJS(string(source))
	}
}
