package realtime

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/coder/websocket"
)

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
