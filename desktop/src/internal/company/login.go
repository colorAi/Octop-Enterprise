package company

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"
)

type CaptchaConfig struct {
	Provider string `json:"provider"`
	SiteKey  string `json:"site_key,omitempty"`
}

type LoginInput struct {
	Address      string `json:"address"`
	Username     string `json:"username"`
	Password     string `json:"password"`
	CaptchaToken string `json:"captcha_token,omitempty"`
	Remember     bool   `json:"remember"`
}

type LoginResult struct {
	AccessToken string `json:"access_token"`
}

// Only these fixed authentication endpoints are used. Redirects are disabled by
// NewClient: credentials must never follow a redirect to another server.
func (c *Client) authJSON(ctx context.Context, base, path string, body any, output any) error {
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	method := http.MethodGet
	var payload []byte
	if body != nil {
		method = http.MethodPost
		payload, _ = json.Marshal(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, base+path, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	response, err := c.httpClient.Do(req)
	if err != nil {
		return errors.New("无法连接服务器，请检查地址和网络 / Unable to reach the server")
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 64*1024+1))
	if err != nil || len(data) > 64*1024 {
		return errors.New("服务器响应无效 / Invalid server response")
	}
	if response.StatusCode != http.StatusOK {
		switch response.StatusCode {
		case 401:
			return errors.New("账号或密码错误 / Incorrect username or password")
		case 429:
			return errors.New("尝试次数过多，请稍后重试 / Too many attempts; try again later")
		case 400, 403:
			return errors.New("登录未通过，请检查账号状态和验证结果 / Login rejected; check your account and verification")
		case 503:
			return errors.New("服务器尚未就绪，请联系管理员 / Server is not ready; contact your administrator")
		default:
			return errors.New("登录服务响应异常 / Unexpected login service response")
		}
	}
	if json.Unmarshal(data, output) != nil {
		return errors.New("服务器响应无效 / Invalid server response")
	}
	return nil
}

func (c *Client) LoginConfig(ctx context.Context, raw string) (CaptchaConfig, error) {
	var config CaptchaConfig
	base, err := c.CheckServer(ctx, raw)
	if err != nil {
		return config, err
	}
	err = c.authJSON(ctx, base, "/api/auth/captcha", nil, &config)
	if err == nil && config.Provider == "" {
		err = errors.New("服务器未返回登录验证配置 / Missing login verification configuration")
	}
	return config, err
}

func (c *Client) Login(ctx context.Context, input LoginInput) (string, LoginResult, error) {
	var result LoginResult
	base, err := ServerAddress(input.Address)
	if err != nil {
		return "", result, err
	}
	if strings.TrimSpace(input.Username) == "" || input.Password == "" || len(input.Username) > 320 || len(input.Password) > 4096 || len(input.CaptchaToken) > 4096 {
		return "", result, errors.New("请填写有效的账号和密码 / Enter your username and password")
	}
	err = c.authJSON(ctx, base, "/api/auth/login", map[string]string{
		"username": strings.TrimSpace(input.Username), "password": input.Password, "captcha_token": input.CaptchaToken,
	}, &result)
	if err == nil && result.AccessToken == "" {
		err = errors.New("服务器未返回登录凭证 / Missing login session")
	}
	return base, result, err
}
