// Package company implements the outbound-only company device channel.
// Credentials are session-only and never written to disk or returned to WebViews.
package company

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

const ProtocolVersion = 2

var errProtocol = errors.New("incompatible device protocol")

type Status struct {
	State       string `json:"state"`
	DeviceID    string `json:"device_id"`
	OwnerUserID int64  `json:"owner_user_id"`
	ExpiresAt   int64  `json:"expires_at"`
}

type Client struct {
	mu         sync.Mutex
	status     Status
	credential string
	cancel     context.CancelFunc
	conn       *websocket.Conn
	httpClient *http.Client
	files      *Files
}

func NewClient() *Client {
	dir, _ := os.UserConfigDir()
	journal := ""
	if dir != "" {
		journal = filepath.Join(dir, "Octop", "company-operations")
	}
	return &Client{files: NewFiles(journal), status: Status{State: "unpaired"}, httpClient: &http.Client{
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}}
}

// BaseURL accepts an HTTP(S) origin, without credentials, subpaths or URL tokens.
func BaseURL(raw string) (string, error) {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" || u.User != nil ||
		u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") || u.Opaque != "" {
		return "", errors.New("请填写 HTTP 或 HTTPS 服务器根地址，例如 https://ai.example.com")
	}
	return u.Scheme + "://" + u.Host, nil
}

func (c *Client) Status() Status {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.status
}

type hello struct {
	Type            string           `json:"type"`
	Credential      string           `json:"credential"`
	ProtocolVersion int              `json:"protocol_version"`
	Name            string           `json:"name"`
	Platform        string           `json:"platform"`
	Capabilities    []string         `json:"capabilities"`
	Grants          []map[string]any `json:"grants,omitempty"`
}

type ready struct {
	Type             string `json:"type"`
	DeviceID         string `json:"device_id"`
	OwnerUserID      int64  `json:"owner_user_id"`
	ProtocolVersion  int    `json:"protocol_version"`
	HeartbeatSeconds int    `json:"heartbeat_seconds"`
	ExpiresAt        int64  `json:"expires_at"`
	Credential       string `json:"credential"`
}

func (c *Client) connect(ctx context.Context, base string, h hello) (*websocket.Conn, ready, error) {
	conn, response, err := websocket.Dial(ctx, "ws"+strings.TrimPrefix(base, "http")+"/api/devices/ws", &websocket.DialOptions{HTTPClient: c.httpClient})
	if err != nil {
		if response != nil && response.StatusCode < 500 {
			return nil, ready{}, errors.New("device channel rejected")
		}
		return nil, ready{}, err
	}
	conn.SetReadLimit(1024 * 1024)
	if err := wsjson.Write(ctx, conn, h); err != nil {
		conn.CloseNow()
		return nil, ready{}, err
	}
	var r ready
	if err := wsjson.Read(ctx, conn, &r); err != nil {
		conn.CloseNow()
		return nil, ready{}, err
	}
	if r.Type != "ready" || r.ProtocolVersion != ProtocolVersion || r.DeviceID == "" ||
		r.OwnerUserID <= 0 || r.ExpiresAt <= time.Now().Unix() || r.HeartbeatSeconds != 15 ||
		(h.Type == "pair" && !strings.HasPrefix(r.Credential, "octop-device.")) {
		conn.CloseNow()
		return nil, ready{}, errProtocol
	}
	return conn, r, nil
}

func (c *Client) Pair(ctx context.Context, rawURL, code, name, platform string) error {
	base, err := BaseURL(rawURL)
	if err != nil {
		return err
	}
	code = strings.TrimSpace(code)
	if !strings.HasPrefix(code, "octop-pair.") || len(code) > 128 {
		return errors.New("请输入有效的设备配对码")
	}
	if platform != "windows" && platform != "darwin" {
		return errors.New("企业客户端仅支持 Windows 和 macOS")
	}
	c.mu.Lock()
	if c.cancel != nil {
		c.mu.Unlock()
		return errors.New("请先断开当前设备再重新配对")
	}
	runCtx, cancel := context.WithCancel(context.Background())
	c.cancel = cancel
	c.status = Status{State: "connecting"}
	c.mu.Unlock()
	c.files.Reset()
	h := hello{Type: "pair", Credential: code, ProtocolVersion: ProtocolVersion, Name: name, Platform: platform, Capabilities: []string{"device_status", "local_files"}}
	handshake, done := context.WithTimeout(ctx, 10*time.Second)
	defer done()
	stop := context.AfterFunc(runCtx, done)
	defer stop()
	conn, r, err := c.connect(handshake, base, h)
	if err != nil {
		cancel()
		c.mu.Lock()
		c.cancel = nil
		c.status.State = "unpaired"
		c.mu.Unlock()
		return errors.New("配对失败：请检查公司 TLS 证书、服务开关及配对码有效期")
	}
	c.mu.Lock()
	if runCtx.Err() != nil {
		c.cancel = nil
		c.status = Status{State: "unpaired"}
		c.mu.Unlock()
		conn.CloseNow()
		return errors.New("配对已取消")
	}
	c.conn = conn
	c.status = Status{"online", r.DeviceID, r.OwnerUserID, r.ExpiresAt}
	c.credential = r.Credential
	c.mu.Unlock()
	h.Type, h.Credential = "hello", r.Credential
	go c.run(runCtx, base, h, conn, r)
	return nil
}

func (c *Client) run(ctx context.Context, base string, h hello, conn *websocket.Conn, identity ready) {
	defer func() {
		c.mu.Lock()
		c.files.Reset()
		c.credential = ""
		c.conn = nil
		c.cancel = nil
		if c.status.State != "unpaired" {
			c.status = Status{State: "disconnected"}
		}
		c.mu.Unlock()
	}()
	delay := time.Second
	for {
		err := serveFiles(ctx, conn, identity, h.Platform, c.files)
		conn.CloseNow()
		if ctx.Err() != nil || time.Now().Unix() >= identity.ExpiresAt || errors.Is(err, errProtocol) {
			return
		}
		code := websocket.CloseStatus(err)
		if code == 4001 || code == 4002 || code == 4403 || code == 4404 {
			return
		}
		c.mu.Lock()
		c.conn = nil
		c.status.State = "reconnecting"
		c.mu.Unlock()
		for {
			timer := time.NewTimer(delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return
			case <-timer.C:
			}
			if time.Now().Unix() >= identity.ExpiresAt {
				return
			}
			attempt, cancel := context.WithTimeout(ctx, 10*time.Second)
			h.Grants = wireGrants(c.files.Grants())
			next, r, connectErr := c.connect(attempt, base, h)
			cancel()
			if connectErr == nil {
				if r.DeviceID != identity.DeviceID || r.OwnerUserID != identity.OwnerUserID || r.ExpiresAt != identity.ExpiresAt {
					next.CloseNow()
					return
				}
				conn = next
				c.mu.Lock()
				c.conn = conn
				c.status.State = "online"
				c.mu.Unlock()
				delay = time.Second
				break
			}
			code := websocket.CloseStatus(connectErr)
			if code == 4001 || code == 4002 || errors.Is(connectErr, errProtocol) || connectErr.Error() == "device channel rejected" {
				return
			}
			delay = min(delay*2, 30*time.Second)
		}
	}
}

// Close cancels the socket before clearing credentials. Pair remains blocked until run exits.
func (c *Client) Close(revoke bool) {
	c.mu.Lock()
	conn, cancel := c.conn, c.cancel
	c.status = Status{State: "unpaired"}
	c.credential = ""
	c.mu.Unlock()
	if conn != nil && revoke {
		ctx, done := context.WithTimeout(context.Background(), time.Second)
		_ = wsjson.Write(ctx, conn, map[string]string{"type": "revoke"})
		done()
	}
	if cancel != nil {
		cancel()
	}
	if conn != nil {
		conn.CloseNow()
	}
	c.files.Reset()
}

type frame struct {
	Type         string                     `json:"type"`
	CallID       string                     `json:"call_id"`
	TaskID       string                     `json:"task_id"`
	DeviceID     string                     `json:"device_id"`
	OwnerUserID  int64                      `json:"owner_user_id"`
	Tool         string                     `json:"tool"`
	ExpiresAt    int64                      `json:"expires_at"`
	Arguments    map[string]json.RawMessage `json:"arguments"`
	AgentID      string                     `json:"agent_id"`
	ThreadID     string                     `json:"thread_id"`
	GrantID      string                     `json:"grant_id"`
	GrantVersion int                        `json:"grant_version"`
}

func serve(parent context.Context, conn *websocket.Conn, identity ready, platform string) error {
	return serveFiles(parent, conn, identity, platform, nil)
}

func serveFiles(parent context.Context, conn *websocket.Conn, identity ready, platform string, files *Files) error {
	ctx, cancel := context.WithDeadline(parent, time.Unix(identity.ExpiresAt, 0))
	defer cancel()
	go func() {
		ticker := time.NewTicker(15 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				writeCtx, done := context.WithTimeout(ctx, 5*time.Second)
				err := wsjson.Write(writeCtx, conn, map[string]string{"type": "heartbeat"})
				done()
				if err != nil {
					cancel()
					return
				}
			}
		}
	}()
	// Only read-only diagnostic calls exist in v1. Replayed ids get the same cached result.
	completed := make(map[string]map[string]any)
	var fileCalls sync.Map
	slots := make(chan struct{}, 4)
	for {
		var f frame
		readCtx, done := context.WithTimeout(ctx, 45*time.Second)
		err := wsjson.Read(readCtx, conn, &f)
		done()
		if err != nil {
			return err
		}
		switch f.Type {
		case "heartbeat_ack":
			continue
		case "cancel":
			if stop, ok := fileCalls.Load(f.CallID); ok {
				stop.(context.CancelFunc)()
			}
			if result, ok := completed[f.CallID]; ok {
				if err := writeResult(ctx, conn, result); err != nil {
					return err
				}
			}
			continue
		case "call":
			if f.CallID == "" || len(f.CallID) > 64 ||
				f.DeviceID != identity.DeviceID || f.OwnerUserID != identity.OwnerUserID ||
				f.ExpiresAt <= time.Now().Unix() || f.ExpiresAt > identity.ExpiresAt {
				_ = conn.Close(4002, "invalid_call")
				return errProtocol
			}
			if files != nil && isFileTool(f.Tool) {
				if !identifier.MatchString(f.CallID) || !identifier.MatchString(f.TaskID) || !identifier.MatchString(f.GrantID) || f.AgentID == "" || f.ThreadID == "" || len(f.AgentID) > 128 || len(f.ThreadID) > 128 || f.GrantVersion < 1 {
					_ = conn.Close(4002, "invalid_file_scope")
					return errProtocol
				}
				callCtx, stop := context.WithDeadline(ctx, time.Unix(f.ExpiresAt, 0))
				if _, loaded := fileCalls.LoadOrStore(f.CallID, context.CancelFunc(stop)); loaded {
					stop()
					continue
				}
				select {
				case slots <- struct{}{}:
					go func(call frame) {
						defer func() { <-slots; fileCalls.Delete(call.CallID); stop() }()
						result := files.Execute(callCtx, call)
						_ = writeResult(ctx, conn, map[string]any{"type": "result", "call_id": call.CallID, "result": result})
					}(f)
				default:
					stop()
					fileCalls.Delete(f.CallID)
					_ = writeResult(ctx, conn, map[string]any{"type": "result", "call_id": f.CallID, "result": failure("device_busy")})
				}
				continue
			}
			if f.TaskID != "probe:"+f.CallID || f.Tool != "device_status" || len(f.Arguments) != 0 {
				_ = conn.Close(4002, "invalid_call")
				return errProtocol
			}
			result, ok := completed[f.CallID]
			if !ok {
				result = map[string]any{"type": "result", "call_id": f.CallID,
					"result": map[string]any{"platform": platform, "protocol_version": ProtocolVersion}}
				if len(completed) >= 128 {
					clear(completed)
				}
				completed[f.CallID] = result
			}
			if err := writeResult(ctx, conn, result); err != nil {
				return err
			}
		default:
			_ = conn.Close(4002, "unknown_frame")
			return errProtocol
		}
	}
}

func writeResult(ctx context.Context, conn *websocket.Conn, result map[string]any) error {
	writeCtx, done := context.WithTimeout(ctx, 5*time.Second)
	defer done()
	return wsjson.Write(writeCtx, conn, result)
}

// RuntimeIdentity is native-only. Never bind this method to a WebView.
func (c *Client) RuntimeIdentity() (Status, string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.status.State != "online" || c.credential == "" || c.status.ExpiresAt <= time.Now().Unix() {
		return Status{}, "", errors.New("本地设备未连接公司服务，请在设置中重新连接")
	}
	return c.status, c.credential, nil
}
