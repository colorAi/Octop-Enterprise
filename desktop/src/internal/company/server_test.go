package company

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestServerAddressDefaultsToHTTPS(t *testing.T) {
	for _, raw := range []string{"octop.internal:8443", "10.20.30.40:8443", "[fd00::10]:8443"} {
		if got, err := ServerAddress(" " + raw + "/ "); err != nil || got != "https://"+raw {
			t.Fatal(got, err)
		}
	}
	for _, raw := range []string{"", "ftp://octop.internal", "company.test/path", "user:pass@company.test", "company.test?token=x"} {
		if _, err := ServerAddress(raw); err == nil {
			t.Fatalf("accepted %q", raw)
		}
	}
}

func TestServerCheckPlainIntranetAddress(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/setup/status" || r.Header.Get("Authorization") != "" {
			t.Error("unexpected request")
		}
		_, _ = w.Write([]byte(`{"setup_required":false}`))
	}))
	defer server.Close()
	base, err := NewClient().CheckServer(context.Background(), server.URL)
	if err != nil || base != server.URL {
		t.Fatal(base, err)
	}
}

func TestServerCheckValidatesTLSResponseAndNoRedirects(t *testing.T) {
	for _, scenario := range []string{"ready", "setup", "html", "other_json", "large", "redirect", "untrusted"} {
		t.Run(scenario, func(t *testing.T) {
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/api/setup/status" || r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" {
					t.Error("unexpected endpoint, redirected request or credential")
				}
				switch scenario {
				case "ready", "untrusted":
					_, _ = w.Write([]byte(`{"setup_required":false}`))
				case "setup":
					_, _ = w.Write([]byte(`{"setup_required":true}`))
				case "html":
					_, _ = w.Write([]byte(`<html>Login proxy</html>`))
				case "other_json":
					_, _ = w.Write([]byte(`{"ok":true}`))
				case "large":
					_, _ = w.Write([]byte(`{"setup_required":false,"extra":"` + strings.Repeat("x", 65536) + `"}`))
				case "redirect":
					http.Redirect(w, r, "/login", http.StatusFound)
				}
			}))
			defer server.Close()
			client := NewClient()
			if scenario != "untrusted" {
				client.httpClient.Transport = server.Client().Transport
			}
			base, err := client.CheckServer(context.Background(), server.URL)
			valid := scenario == "ready" || scenario == "setup"
			if (err == nil) != valid || (valid && base != server.URL) {
				t.Fatal(base, err)
			}
		})
	}
}
