package main

import (
	"testing"

	"github.com/wailsapp/wails/v3/pkg/application"
)

func TestWindowBackgroundEventAcceptsOnlyOpaqueRGB(t *testing.T) {
	color, ok := parseWindowBackgroundEvent("desktop:background:14:14:14")
	if !ok || color != application.NewRGB(14, 14, 14) {
		t.Fatalf("dark page background was not parsed: %#v, %v", color, ok)
	}
	for _, value := range []string{"desktop:background:256:0:0", "desktop:background:-1:0:0", "desktop:background:1:2", "desktop:background:1:2:3:0", "desktop:background:red:0:0", "other:14:14:14"} {
		if _, ok := parseWindowBackgroundEvent(value); ok {
			t.Fatalf("accepted malformed background: %q", value)
		}
	}
}

func TestDesktopBackgroundMatchesPageTheme(t *testing.T) {
	if desktopBackgroundColour(true) != application.NewRGB(14, 14, 14) {
		t.Fatal("dark native window would expose a light resize background")
	}
	if desktopBackgroundColour(false) != application.NewRGB(245, 246, 248) {
		t.Fatal("light native window differs from the page background")
	}
}
