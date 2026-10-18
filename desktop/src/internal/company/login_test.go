package company

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestEmployeeLoginOverHTTP(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "" || r.URL.RawQuery != "" {
			t.Error("credentials in metadata")
		}
		switch r.URL.Path {
		case "/api/setup/status":
			w.Write([]byte(`{"setup_required":false}`))
		case "/api/auth/captcha":
			w.Write([]byte(`{"provider":"slider"}`))
		case "/api/auth/login":
			var body map[string]string
			if r.Method != "POST" || json.NewDecoder(r.Body).Decode(&body) != nil || body["username"] != "employee" || body["password"] != "sample-password" || body["captcha_token"] != "verification" {
				t.Error("invalid login body")
			}
			w.Write([]byte(`{"access_token":"test-session"}`))
		default:
			t.Error("unexpected endpoint")
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	client := NewClient()
	address := server.URL
	config, err := client.LoginConfig(context.Background(), address)
	if err != nil || config.Provider != "slider" {
		t.Fatal(config, err)
	}
	base, result, err := client.Login(context.Background(), LoginInput{Address: address, Username: "employee", Password: "sample-password", CaptchaToken: "verification"})
	if err != nil || base != server.URL || result.AccessToken != "test-session" {
		t.Fatal("login failed", err)
	}
}

func TestEmployeeLoginDoesNotRedirectCredentials(t *testing.T) {
	visited := false
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { visited = true }))
	defer target.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	}))
	defer server.Close()
	_, _, err := NewClient().Login(context.Background(), LoginInput{Address: server.URL, Username: "employee", Password: "sample-password"})
	if err == nil || visited {
		t.Fatal("followed login redirect")
	}
}

func TestEmployeeLoginErrorsAreBoundedAndDoNotEchoSecrets(t *testing.T) {
	for _, status := range []int{200, 401, 403, 429, 503} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(status)
				w.Write([]byte(`{"detail":"sample-password"}`))
			}))
			defer server.Close()
			_, _, err := NewClient().Login(context.Background(), LoginInput{Address: server.URL, Username: "employee", Password: "sample-password"})
			if err == nil || strings.Contains(err.Error(), "sample-password") {
				t.Fatal("invalid or unsafe error")
			}
		})
	}
}
