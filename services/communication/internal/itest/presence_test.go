package itest

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/coder/websocket"
)

func TestPresenceDirectoryPermissionsAndContactsOnlyConnection(t *testing.T) {
	h := newH(t)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	t.Cleanup(h.App.Hub.Close)
	if _, err := h.D.Store.Pool.Exec(ctx, `UPDATE users SET is_admin=true WHERE id=$1`, admin); err != nil {
		t.Fatal(err)
	}
	if _, err := h.D.Store.Pool.Exec(ctx, `UPDATE users SET permissions='["contacts"]' WHERE id=$1`, bob); err != nil {
		t.Fatal(err)
	}
	dial := func(user string) *websocket.Conn {
		t.Helper()
		ws, _, err := websocket.Dial(ctx, strings.Replace(h.Server.URL, "http://", "ws://", 1)+"/api/app/v1/realtime", &websocket.DialOptions{HTTPHeader: http.Header{"Authorization": []string{"Bearer " + h.token(user, "request", 1)}}})
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = ws.CloseNow() })
		return ws
	}
	_ = dial(bob)
	_ = dial(admin)
	for h.App.Hub.Count() != 2 {
		select {
		case <-ctx.Done():
			t.Fatal("connections were not registered")
		case <-time.After(time.Millisecond):
		}
	}
	users := h.ok(alice, "GET", "/presence", nil)["online_user_ids"].([]any)
	if len(users) != 1 || users[0] != bob {
		t.Fatal("presence should include the contacts-only colleague and exclude administrators", users)
	}
	if code, _ := h.request(bob, "GET", "/conversations", nil); code != 403 {
		t.Fatal("contacts-only connection granted messaging access", code)
	}
	h.identity(bob, "disabled", 2)
	if users = h.ok(alice, "GET", "/presence", nil)["online_user_ids"].([]any); len(users) != 0 {
		t.Fatal("disabled colleague remained visible online", users)
	}
	if _, err := h.D.Store.Pool.Exec(ctx, `UPDATE users SET permissions='["messaging"]' WHERE id=$1`, outsider); err != nil {
		t.Fatal(err)
	}
	if code, _ := h.request(outsider, "GET", "/presence", nil); code != 403 {
		t.Fatal("presence exposed without directory permission", code)
	}
}
