// Package companyui serves the packaged dashboard on loopback and forwards only
// API traffic to the employee-selected server. It never starts a local backend.
package companyui

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type Server struct {
	Origin    string
	Base      string
	key       string
	cookie    string
	server    *http.Server
	cancel    context.CancelFunc
	transport *http.Transport
}

type savedOrigin struct {
	Base string `json:"server"`
	Port int    `json:"port"`
}

// Commit a loopback document before navigating with the Strict cookie. WebKit
// keeps the original wails:// navigation's cross-site context across an HTTP
// redirect and can otherwise omit the newly set cookie on desktop-login.html.
const bootstrapDocument = `<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="color-scheme" content="light dark"><title>Octop</title></head>
<body><p role="status">正在进入工作界面…</p>
<script>
var locale = new URLSearchParams(location.search).get("ui_locale");
if (locale === "zh" || locale === "en") {
  try { localStorage.setItem("octop:ui-locale", locale); } catch (_) {}
  document.title = locale === "zh" ? "Octop 企业版" : "Octop Enterprise";
  document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
  if (locale === "en") document.querySelector('[role="status"]').textContent = "Opening your workspace…";
}
location.replace("/desktop-login.html" + location.hash);</script></body></html>`

// Start keeps a separate browser origin per company, including across restarts.
// The small local record contains only an address and port, never credentials.
func Start(base string, bundle []byte, stateDir string) (*Server, error) {
	target, err := url.Parse(base)
	if err != nil || (target.Scheme != "http" && target.Scheme != "https") || target.Hostname() == "" || target.User != nil || target.RawQuery != "" || target.Fragment != "" || (target.Path != "" && target.Path != "/") {
		return nil, errors.New("invalid company server")
	}
	archive, err := zip.NewReader(bytes.NewReader(bundle), int64(len(bundle)))
	if err != nil {
		return nil, err
	}
	if _, err = fs.Stat(archive, "index.html"); err != nil {
		return nil, err
	}
	sum := sha256.Sum256([]byte(base))
	record := filepath.Join(stateDir, hex.EncodeToString(sum[:])+".json")
	var saved savedOrigin
	if data, readErr := os.ReadFile(record); readErr == nil {
		if json.Unmarshal(data, &saved) != nil || saved.Base != base || saved.Port < 1024 || saved.Port > 65535 {
			return nil, errors.New("invalid local UI origin record")
		}
	} else if !os.IsNotExist(readErr) {
		return nil, readErr
	}
	// The OS can reuse a closed port. Reserve records for inactive companies too,
	// otherwise switching servers could accidentally share their browser storage.
	reserved := make(map[int]bool)
	records, err := filepath.Glob(filepath.Join(stateDir, "*.json"))
	if err != nil {
		return nil, err
	}
	for _, other := range records {
		if other == record {
			continue
		}
		data, err := os.ReadFile(other)
		var value savedOrigin
		if err != nil || json.Unmarshal(data, &value) != nil {
			return nil, errors.New("invalid local UI origin record")
		}
		reserved[value.Port] = true
	}
	var listener net.Listener
	for attempt := 0; attempt < 16; attempt++ {
		listener, err = net.Listen("tcp4", net.JoinHostPort("127.0.0.1", strconv.Itoa(saved.Port)))
		if err != nil {
			return nil, errors.New("本地界面端口被占用，请退出其他 Octop 客户端后重试 / Local UI port is occupied; quit other Octop clients")
		}
		if !reserved[listener.Addr().(*net.TCPAddr).Port] {
			break
		}
		listener.Close()
		listener = nil
		if saved.Port != 0 {
			break
		}
	}
	if listener == nil {
		return nil, errors.New("company UI origins must remain separate")
	}
	if err = os.MkdirAll(stateDir, 0o700); err != nil {
		listener.Close()
		return nil, err
	}
	saved = savedOrigin{base, listener.Addr().(*net.TCPAddr).Port}
	data, _ := json.Marshal(saved)
	if err = os.WriteFile(record, data, 0o600); err != nil {
		listener.Close()
		return nil, err
	}
	var secret [32]byte
	if _, err = rand.Read(secret[:]); err != nil {
		listener.Close()
		return nil, err
	}
	ctx, cancel := context.WithCancel(context.Background())
	s := &Server{Origin: "http://" + listener.Addr().String(), Base: base, key: hex.EncodeToString(secret[:]), cookie: "octop_ui_" + strconv.Itoa(saved.Port), cancel: cancel}
	s.transport = http.DefaultTransport.(*http.Transport).Clone()
	s.transport.MaxIdleConnsPerHost = 16
	proxy := &httputil.ReverseProxy{
		Rewrite: func(p *httputil.ProxyRequest) {
			p.SetURL(target)
			p.Out.Host = target.Host
			// Validate the local Origin first, then preserve the backend's exact
			// same-origin WebSocket policy over the selected HTTPS/WSS target.
			if p.In.Header.Get("Origin") != "" {
				p.Out.Header.Set("Origin", base)
			}
			p.Out.Header.Del("Cookie")
			p.Out.Header.Del("Referer")
			for name := range p.Out.Header {
				if strings.HasPrefix(strings.ToLower(name), "x-forwarded-") || strings.EqualFold(name, "Forwarded") {
					p.Out.Header.Del(name)
				}
			}
		},
		Transport:     s.transport,
		FlushInterval: -1,
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, _ error) {
			http.Error(w, "Company server unavailable", http.StatusBadGateway)
		},
		ModifyResponse: func(r *http.Response) error {
			// Do not let remote cookies overwrite the local bootstrap capability.
			r.Header.Del("Set-Cookie")
			r.Header.Set("Cache-Control", "no-store")
			if location := r.Header.Get("Location"); location != "" {
				u, err := target.Parse(location)
				if err != nil || u.Scheme != target.Scheme || u.Host != target.Host || !strings.HasPrefix(u.Path, "/api/") {
					return errors.New("cross-origin API redirect rejected")
				}
				r.Header.Set("Location", u.RequestURI())
			}
			return nil
		},
	}
	files := http.FileServer(http.FS(archive))
	s.server = &http.Server{ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 90 * time.Second, Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("Cache-Control", "no-store")
		if r.Host != listener.Addr().String() || (r.Header.Get("Origin") != "" && r.Header.Get("Origin") != s.Origin) || r.Header.Get("Sec-Fetch-Site") == "cross-site" {
			http.Error(w, "Forbidden", http.StatusForbidden)
			return
		}
		if r.URL.Path == "/__octop_boot/"+s.key && r.Method == http.MethodGet {
			http.SetCookie(w, &http.Cookie{Name: s.cookie, Value: s.key, Path: "/", HttpOnly: true, SameSite: http.SameSiteStrictMode})
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			// The fragment is a separate native handoff nonce, not a password.
			_, _ = io.WriteString(w, bootstrapDocument)
			return
		}
		cookie, err := r.Cookie(s.cookie)
		if err != nil || cookie.Value != s.key {
			http.Error(w, "Forbidden", http.StatusForbidden)
			return
		}
		requestCtx, done := context.WithCancel(r.Context())
		stop := context.AfterFunc(ctx, done)
		defer func() { stop(); done() }()
		r = r.WithContext(requestCtx)
		if strings.HasPrefix(r.URL.Path, "/api/") {
			proxy.ServeHTTP(w, r)
			return
		}
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		name := strings.TrimPrefix(r.URL.Path, "/")
		if !fs.ValidPath(name) && name != "" {
			http.NotFound(w, r)
			return
		}
		if info, err := fs.Stat(archive, name); err == nil && !info.IsDir() {
			if strings.HasPrefix(name, "assets/") {
				w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
			}
			files.ServeHTTP(w, r)
			return
		}
		// Missing chunks must stay 404; an HTML response here causes an opaque
		// module/CSS failure and the partially rendered screen users reported.
		if path.Ext(name) != "" || strings.HasPrefix(name, "assets/") || strings.HasPrefix(name, "__octop_") {
			http.NotFound(w, r)
			return
		}
		r.URL.Path = "/"
		files.ServeHTTP(w, r)
	})}
	go func() { _ = s.server.Serve(listener) }()
	return s, nil
}

func (s *Server) Entry(fragment string) string {
	return fmt.Sprintf("%s/__octop_boot/%s#%s", s.Origin, s.key, fragment)
}

func (s *Server) Close() {
	s.cancel() // Also closes in-flight streaming / upgraded proxy requests.
	_ = s.server.Close()
	s.transport.CloseIdleConnections()
}
