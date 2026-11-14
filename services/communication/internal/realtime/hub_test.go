package realtime

import (
	"context"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync"
	"testing"
	"testing/synctest"
	"time"

	"github.com/coder/websocket"
)

type pipeListener struct {
	incoming chan net.Conn
	closed   chan struct{}
	once     sync.Once
}

func (l *pipeListener) Accept() (net.Conn, error) {
	select {
	case c := <-l.incoming:
		return c, nil
	case <-l.closed:
		return nil, net.ErrClosed
	}
}

func (l *pipeListener) Close() error {
	l.once.Do(func() { close(l.closed) })
	return nil
}

func (l *pipeListener) Addr() net.Addr {
	return &net.TCPAddr{}
}

func TestIdleConnectionSurvivesBeyondTenMinutes(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		hub := NewHub(slog.New(slog.NewTextHandler(io.Discard, nil)), 10, 4)
		listener := &pipeListener{incoming: make(chan net.Conn), closed: make(chan struct{})}
		server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			hub.Attach(w, r, "employee")
		})}
		go func() { _ = server.Serve(listener) }()
		defer server.Close()
		defer hub.Close()

		transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			remote, local := net.Pipe()
			select {
			case listener.incoming <- remote:
				return local, nil
			case <-ctx.Done():
				_ = remote.Close()
				_ = local.Close()
				return nil, ctx.Err()
			}
		}}
		defer transport.CloseIdleConnections()
		ctx, cancel := context.WithTimeout(t.Context(), 13*time.Minute)
		defer cancel()
		ws, _, err := websocket.Dial(ctx, "ws://realtime.test", &websocket.DialOptions{
			HTTPClient: &http.Client{Transport: transport},
		})
		if err != nil {
			t.Fatal(err)
		}
		defer ws.CloseNow()
		if _, _, err := ws.Read(ctx); err != nil {
			t.Fatalf("read registration hint: %v", err)
		}

		received := make(chan string, 1)
		go func() {
			// Reading handles WebSocket pings while the app sends no frames.
			_, data, _ := ws.Read(ctx)
			received <- string(data)
		}()
		time.Sleep(12 * time.Minute)
		hub.Wakeup("employee")
		if hint := <-received; !strings.Contains(hint, `"type":"sync.available"`) {
			t.Fatalf("connection stopped delivering notifications: %q", hint)
		}
	})
}

func TestPresenceTracksFirstAndLastConnection(t *testing.T) {
	hub := NewHub(slog.New(slog.NewTextHandler(io.Discard, nil)), 10, 4)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hub.Attach(w, r, r.URL.Query().Get("user"))
	}))
	defer server.Close()
	defer hub.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	dial := func(user string) *websocket.Conn {
		t.Helper()
		ws, _, err := websocket.Dial(ctx, strings.Replace(server.URL, "http://", "ws://", 1)+"?user="+user, nil)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = ws.CloseNow() })
		return ws
	}
	waitCount := func(want int) {
		t.Helper()
		for hub.Count() != want {
			select {
			case <-ctx.Done():
				t.Fatalf("connection count %d; want %d", hub.Count(), want)
			case <-time.After(time.Millisecond):
			}
		}
	}
	read := func(ws *websocket.Conn, want string) {
		t.Helper()
		_, data, err := ws.Read(ctx)
		if err != nil || string(data) != want {
			t.Fatalf("notification %s: %v; want %s", data, err, want)
		}
	}
	watcher := dial("watcher")
	waitCount(1)
	read(watcher, `{"type":"presence.changed"}`)
	first := dial("member")
	waitCount(2)
	read(watcher, `{"type":"presence.changed"}`)
	second := dial("member")
	waitCount(3)
	_ = first.CloseNow()
	waitCount(2)
	if !slices.Contains(hub.OnlineUsers(), "member") {
		t.Fatal("closing one client marked the member offline")
	}
	_ = second.CloseNow()
	waitCount(1)
	if slices.Contains(hub.OnlineUsers(), "member") {
		t.Fatal("member stayed online after the last client left")
	}
	read(watcher, `{"type":"presence.changed"}`)
	if err := watcher.Write(ctx, websocket.MessageText, []byte(`{"type":"ping"}`)); err != nil {
		t.Fatal(err)
	}
	// A second connection or a partial disconnect must not emit a state change.
	read(watcher, `{"type":"pong"}`)
}
