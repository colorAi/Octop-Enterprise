package companyui

import (
	"archive/zip"
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
)

func bundle(t *testing.T) []byte {
	t.Helper()
	var data bytes.Buffer
	w := zip.NewWriter(&data)
	for name, content := range map[string]string{"index.html": "<html>packaged UI</html>", "desktop-login.html": "handoff", "assets/main.js": "console.log('bundled')", "assets/main.css": "body{color:red}"} {
		f, err := w.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = f.Write([]byte(content)); err != nil {
			t.Fatal(err)
		}
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return data.Bytes()
}

func connect(t *testing.T, s *Server) *http.Client {
	t.Helper()
	jar, _ := cookiejar.New(nil)
	c := &http.Client{Jar: jar, Timeout: 2 * time.Second}
	r, err := c.Get(s.Entry("test-nonce"))
	if err != nil {
		t.Fatal(err)
	}
	r.Body.Close()
	if r.StatusCode != 200 || !strings.HasPrefix(r.Request.URL.Path, "/__octop_boot/") {
		t.Fatalf("entry failed: %v", r.Status)
	}
	// A browser commits the bootstrap document, then performs the script's
	// same-origin navigation. Go's HTTP client does not execute that script.
	r, err = c.Get(s.Origin + "/desktop-login.html")
	if err != nil {
		t.Fatal(err)
	}
	r.Body.Close()
	if r.StatusCode != http.StatusOK {
		t.Fatalf("handoff failed: %v", r.Status)
	}
	return c
}

func read(t *testing.T, c *http.Client, url string) (int, string) {
	t.Helper()
	r, err := c.Get(url)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Body.Close()
	data, err := io.ReadAll(r.Body)
	if err != nil {
		t.Fatal(err)
	}
	return r.StatusCode, string(data)
}

func TestBootstrapCommitsSameOriginPageBeforeStrictCookieNavigation(t *testing.T) {
	s, err := Start("https://company.example", bundle(t), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error {
		return http.ErrUseLastResponse
	}}
	response, err := client.Get(s.Entry("resume"))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK || response.Header.Get("Location") != "" {
		t.Fatalf("bootstrap must commit a local document before navigating: %s", response.Status)
	}
	cookies := response.Cookies()
	if len(cookies) != 1 || !cookies[0].HttpOnly || cookies[0].SameSite != http.SameSiteStrictMode || cookies[0].Value != s.key {
		t.Fatal("bootstrap cookie protection changed")
	}
	body, err := io.ReadAll(response.Body)
	if err != nil || !strings.Contains(string(body), `location.replace("/desktop-login.html" + location.hash)`) {
		t.Fatal("handoff must preserve both saved-session and fresh-login fragments")
	}
}

func TestBootstrapStillRejectsForeignRequests(t *testing.T) {
	s, err := Start("https://company.example", bundle(t), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	for _, test := range []struct{ host, origin, site, method string }{
		{host: "evil.example", method: http.MethodGet},
		{origin: "https://evil.example", method: http.MethodGet},
		{site: "cross-site", method: http.MethodGet},
		{method: http.MethodPost},
	} {
		request, _ := http.NewRequest(test.method, s.Entry("resume"), nil)
		if test.host != "" {
			request.Host = test.host
		}
		request.Header.Set("Origin", test.origin)
		request.Header.Set("Sec-Fetch-Site", test.site)
		response, err := http.DefaultClient.Do(request)
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusForbidden || len(response.Cookies()) != 0 {
			t.Fatalf("untrusted bootstrap accepted: %#v %s", test, response.Status)
		}
	}
}

func TestStaticAssetsAndRoutesNeverFetchCompany(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); w.WriteHeader(503) }))
	defer upstream.Close()
	s, err := Start(upstream.URL, bundle(t), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	c := connect(t, s)
	for path, want := range map[string]string{"/chat": "packaged UI", "/contacts": "packaged UI", "/assets/main.js": "bundled", "/assets/main.css": "color:red"} {
		status, body := read(t, c, s.Origin+path)
		if status != 200 || !strings.Contains(body, want) {
			t.Fatalf("%s: %d %s", path, status, body)
		}
	}
	for _, path := range []string{"/assets/missing.css", "/sw.js", "/assets/../main.js", "/missing.js"} {
		status, _ := read(t, c, s.Origin+path)
		if status != 404 {
			t.Fatalf("missing %s returned %d", path, status)
		}
	}
	if calls.Load() != 0 {
		t.Fatal("UI used company network")
	}
}

func TestOnlySelectedAPIForwardsAndRejectsOtherOrigins(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.URL.Path != "/api/test" || r.URL.Query().Get("a") != "1" || r.Header.Get("Authorization") != "Bearer example" || r.Header.Get("Origin") == "" || r.Header.Get("Cookie") != "" || r.Header.Get("X-Forwarded-Host") != "" {
			t.Error("request identity, path, or credential boundary changed")
		}
		data, _ := io.ReadAll(r.Body)
		if string(data) != "payload" {
			t.Error("body changed")
		}
		w.Header().Set("X-Octop-Access-Token", "renewed")
		w.Write([]byte(`{"ok":true}`))
	}))
	defer upstream.Close()
	s, err := Start(upstream.URL, bundle(t), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	c := connect(t, s)
	request := func(origin, host string) int {
		r, _ := http.NewRequest("POST", s.Origin+"/api/test?a=1", strings.NewReader("payload"))
		r.Header.Set("Origin", origin)
		r.Header.Set("Authorization", "Bearer example")
		r.Header.Set("X-Forwarded-Host", "evil.test")
		if host != "" {
			r.Host = host
		}
		res, err := c.Do(r)
		if err != nil {
			t.Fatal(err)
		}
		defer res.Body.Close()
		if res.StatusCode == 200 && res.Header.Get("X-Octop-Access-Token") != "renewed" {
			t.Error("renewal lost")
		}
		return res.StatusCode
	}
	if request(s.Origin, "") != 200 || request("https://evil.test", "") != 403 || request(s.Origin, "evil.test") != 403 {
		t.Fatal("origin/host guard failed")
	}
	if calls.Load() != 1 {
		t.Fatal("forbidden request reached upstream")
	}
	plain := &http.Client{Timeout: time.Second}
	if status, _ := read(t, plain, s.Origin+"/api/test"); status != 403 {
		t.Fatal("unbootstrapped browser reached API")
	}
}

func TestServerOriginsPersistAndCompaniesRemainSeparate(t *testing.T) {
	dir := t.TempDir()
	data := bundle(t)
	a, err := Start("https://one.example", data, dir)
	if err != nil {
		t.Fatal(err)
	}
	origin := a.Origin
	c := connect(t, a)
	a.Close()
	a, err = Start("https://one.example", data, dir)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	if a.Origin != origin {
		t.Fatal("remembered browser origin changed")
	}
	if status, _ := read(t, c, a.Origin+"/chat"); status != 403 {
		t.Fatal("previous process capability survived")
	}
	b, err := Start("https://two.example", data, dir)
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	if a.Origin == b.Origin {
		t.Fatal("company browser storage is shared")
	}
}

func TestWebSocketOriginAndShutdown(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Origin") != "http://"+r.Host || r.URL.Query().Get("ticket") != "one-use" {
			t.Error("WS target identity changed")
		}
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			t.Error(err)
			return
		}
		defer conn.CloseNow()
		for {
			kind, data, err := conn.Read(r.Context())
			if err != nil {
				return
			}
			if err = conn.Write(r.Context(), kind, data); err != nil {
				return
			}
		}
	}))
	defer upstream.Close()
	s, err := Start(upstream.URL, bundle(t), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	c := connect(t, s)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	header := http.Header{"Origin": []string{s.Origin}}
	conn, _, err := websocket.Dial(ctx, "ws"+strings.TrimPrefix(s.Origin, "http")+"/api/realtime?ticket=one-use", &websocket.DialOptions{HTTPClient: c, HTTPHeader: header})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.CloseNow()
	if err = conn.Write(ctx, websocket.MessageText, []byte("hello")); err != nil {
		t.Fatal(err)
	}
	_, data, err := conn.Read(ctx)
	if err != nil || string(data) != "hello" {
		t.Fatalf("WS echo: %q %v", data, err)
	}
	s.Close()
	if _, _, err = conn.Read(ctx); err == nil {
		t.Fatal("WS survived server shutdown")
	}
}

func TestAPIRedirectCannotSendCredentialsToAnotherOrigin(t *testing.T) {
	var leaked atomic.Int32
	other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { leaked.Add(1) }))
	defer other.Close()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, other.URL+"/api/leak", 307) }))
	defer upstream.Close()
	s, err := Start(upstream.URL, bundle(t), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	status, _ := read(t, connect(t, s), s.Origin+"/api/test")
	if status != 502 || leaked.Load() != 0 {
		t.Fatal("external redirect followed")
	}
}

func TestCompanyTLSCertificateStillValidated(t *testing.T) {
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { json.NewEncoder(w).Encode(map[string]bool{"ok": true}) }))
	defer upstream.Close()
	s, err := Start(upstream.URL, bundle(t), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if status, _ := read(t, connect(t, s), s.Origin+"/api/test"); status != 502 {
		t.Fatal("untrusted certificate accepted")
	}
}

func TestMultipartUploadAndStreamRemainUnbuffered(t *testing.T) {
	finish := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/upload" {
			if err := r.ParseMultipartForm(1024); err != nil {
				t.Error(err)
				return
			}
			file, _, err := r.FormFile("file")
			if err != nil {
				t.Error(err)
				return
			}
			defer file.Close()
			data, _ := io.ReadAll(file)
			if string(data) != "file-content" {
				t.Error("upload changed")
			}
			w.WriteHeader(201)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.Write([]byte("first\n"))
		w.(http.Flusher).Flush()
		select {
		case <-finish:
		case <-r.Context().Done():
			return
		}
		w.Write([]byte("second\n"))
	}))
	defer upstream.Close()
	s, err := Start(upstream.URL, bundle(t), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	defer close(finish)
	c := connect(t, s)
	var body bytes.Buffer
	form := multipart.NewWriter(&body)
	file, _ := form.CreateFormFile("file", "example.txt")
	file.Write([]byte("file-content"))
	form.Close()
	r, _ := http.NewRequest("POST", s.Origin+"/api/upload", &body)
	r.Header.Set("Content-Type", form.FormDataContentType())
	response, err := c.Do(r)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != 201 {
		t.Fatal("upload rejected")
	}
	response, err = c.Get(s.Origin + "/api/stream")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	line, err := bufio.NewReader(response.Body).ReadString('\n')
	if err != nil || line != "first\n" {
		t.Fatalf("stream buffered: %q %v", line, err)
	}
}
