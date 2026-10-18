// Package realtime implements the WebSocket notification hub. It only carries
// sync and presence hints — never message content — per the sync protocol.
package realtime

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"sync"
	"time"

	"github.com/coder/websocket"
)

// Hub tracks live connections per user.
type Hub struct {
	mu         sync.Mutex
	conns      map[*conn]struct{}
	byUser     map[string]map[*conn]struct{}
	log        *slog.Logger
	maxTotal   int
	maxPerUser int
	dropped    int64
}

type conn struct {
	user  string
	c     *websocket.Conn
	send  chan []byte
	hub   *Hub
	close chan struct{}
	once  sync.Once
}

// NewHub returns an empty hub.
func NewHub(log *slog.Logger, maxTotal, maxPerUser int) *Hub {
	return &Hub{conns: map[*conn]struct{}{}, byUser: map[string]map[*conn]struct{}{},
		log: log, maxTotal: maxTotal, maxPerUser: maxPerUser}
}

// Attach upgrades and serves one connection (auth already verified upstream).
func (h *Hub) Attach(w http.ResponseWriter, r *http.Request, user string) {
	c, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		CompressionMode: websocket.CompressionDisabled,
		OriginPatterns:  nil, // allowlist enforced by caller (native has no Origin)
	})
	if err != nil {
		return
	}
	h.register(user, c)
}

// Count returns current live connections.
func (h *Hub) Count() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.conns)
}

// OnlineUsers returns users with at least one live client connection.
func (h *Hub) OnlineUsers() []string {
	h.mu.Lock()
	defer h.mu.Unlock()
	users := make([]string, 0, len(h.byUser))
	for user := range h.byUser {
		users = append(users, user)
	}
	return users
}

// presenceChangedLocked sends an identity-free hint to other connected clients.
func (h *Hub) presenceChangedLocked(except *conn) {
	for cn := range h.conns {
		if cn == except {
			continue
		}
		select {
		case cn.send <- []byte(`{"type":"presence.changed"}`):
		default:
			h.dropped++
		}
	}
}

// Wakeup nudges users that new sync data exists; coalesced, best-effort.
func (h *Hub) Wakeup(userIDs ...string) {
	now := time.Now().UTC().Format(time.RFC3339)
	h.mu.Lock()
	for _, uid := range userIDs {
		for cn := range h.byUser[uid] {
			select {
			case cn.send <- []byte(`{"type":"sync.available","ts":"` + now + `"}`):
			default:
				h.dropped++
			}
		}
	}
	h.mu.Unlock()
}

// Kick force-closes all connections of the given users (session revoked).
func (h *Hub) Kick(userIDs ...string) {
	h.mu.Lock()
	var victims []*conn
	for _, uid := range userIDs {
		for cn := range h.byUser[uid] {
			victims = append(victims, cn)
		}
	}
	h.mu.Unlock()
	for _, cn := range victims {
		cn.once.Do(func() { close(cn.close) })
		_ = cn.c.CloseNow()
	}
}

func (h *Hub) register(user string, c *websocket.Conn) {
	cn := &conn{user: user, c: c, send: make(chan []byte, 8), hub: h, close: make(chan struct{})}
	h.mu.Lock()
	if len(h.conns) >= h.maxTotal || len(h.byUser[user]) >= h.maxPerUser {
		h.mu.Unlock()
		_ = c.Close(websocket.StatusPolicyViolation, "connection_limit")
		return
	}
	h.conns[cn] = struct{}{}
	if h.byUser[user] == nil {
		h.byUser[user] = map[*conn]struct{}{}
	}
	h.byUser[user][cn] = struct{}{}
	if len(h.byUser[user]) == 1 {
		h.presenceChangedLocked(cn)
	}
	// The public proxy accepts before its upstream connection is registered.
	// Confirm registration so the new client refreshes its own presence too.
	cn.send <- []byte(`{"type":"presence.changed"}`)
	h.mu.Unlock()

	go h.writeLoop(cn)
	h.readLoop(cn)
	h.unregister(cn)
}

func (h *Hub) writeLoop(cn *conn) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	ticker := time.NewTicker(45 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case msg := <-cn.send:
			if err := cn.c.Write(ctx, websocket.MessageText, msg); err != nil {
				cn.shutdown(websocket.StatusNormalClosure, "")
				return
			}
		case <-ticker.C:
			pingCtx, pingCancel := context.WithTimeout(ctx, 10*time.Second)
			err := cn.c.Ping(pingCtx)
			pingCancel()
			if err != nil {
				cn.shutdown(websocket.StatusNormalClosure, "")
				return
			}
		case <-cn.close:
			return
		}
	}
}

func (h *Hub) readLoop(cn *conn) {
	for {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
		_, data, err := cn.c.Read(ctx)
		cancel()
		if err != nil {
			return
		}
		// Clients may send {"type":"ping"} or {"type":"resync"}; respond minimally.
		var m struct {
			Type string `json:"type"`
		}
		_ = json.Unmarshal(data, &m)
		switch m.Type {
		case "ping":
			select {
			case cn.send <- []byte(`{"type":"pong"}`):
			default:
			}
		case "resync":
			h.Wakeup(cn.user)
		}
	}
}

func (h *Hub) unregister(cn *conn) {
	h.mu.Lock()
	delete(h.conns, cn)
	if m := h.byUser[cn.user]; m != nil {
		delete(m, cn)
		if len(m) == 0 {
			delete(h.byUser, cn.user)
			h.presenceChangedLocked(nil)
		}
	}
	h.mu.Unlock()
	cn.once.Do(func() { close(cn.close) })
	_ = cn.c.CloseNow()
}

func (cn *conn) shutdown(code websocket.StatusCode, reason string) {
	cn.once.Do(func() { close(cn.close) })
	_ = cn.c.Close(code, reason)
}

// Close releases upgraded connections during process shutdown.
func (h *Hub) Close() {
	h.mu.Lock()
	var conns []*conn
	for c := range h.conns {
		conns = append(conns, c)
	}
	h.mu.Unlock()
	for _, c := range conns {
		c.once.Do(func() { close(c.close) })
		_ = c.c.CloseNow()
	}
}
