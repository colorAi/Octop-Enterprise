package company

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"runtime"
	"strings"
	"time"
)

// ServerAddress defaults bare addresses to HTTPS. Intranet HTTP must be explicit.
func ServerAddress(raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	if raw != "" && !strings.Contains(raw, "://") {
		raw = "https://" + raw
	}
	return BaseURL(raw)
}

// CheckServer checks the public setup endpoint without sending credentials.
// Redirects stay disabled so the user-selected origin is the only destination.
func (c *Client) CheckServer(ctx context.Context, raw string) (string, error) {
	base, err := ServerAddress(raw)
	if err != nil {
		return "", err
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, base+"/api/setup/status", nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Accept", "application/json")
	response, err := c.httpClient.Do(req)
	if err != nil {
		permissionHint := ""
		if runtime.GOOS == "darwin" {
			permissionHint = "。如 macOS 提示访问本地网络，请允许后重试；也可在系统设置 → 隐私与安全性 → 本地网络中允许 Octop Enterprise / On macOS, allow Local Network access in System Settings → Privacy & Security, then retry"
		}
		if strings.HasPrefix(base, "https://") {
			return "", errors.New("无法连接 HTTPS 服务器，请检查地址、网络及证书 / Check HTTPS address, network and certificate" + permissionHint)
		}
		return "", errors.New("无法连接服务器，请检查内网地址、端口和网络 / Check server address, port and network" + permissionHint)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", errors.New("服务器未就绪或地址不是 Octop 服务入口 / Server unavailable or not an Octop origin")
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 64*1024+1))
	var status struct {
		SetupRequired *bool `json:"setup_required"`
	}
	if err != nil || len(body) > 64*1024 || json.Unmarshal(body, &status) != nil || status.SetupRequired == nil {
		return "", errors.New("地址未返回有效的 Octop 服务信息 / Not a valid Octop server response")
	}
	return base, nil
}
