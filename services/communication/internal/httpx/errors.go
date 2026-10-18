// Package httpx implements the shared JSON envelope, stable error codes, and
// small HTTP utilities (bounded decode, cursors, limiters).
package httpx

import (
	"errors"
	"fmt"
	"net/http"
	"time"
)

// Error is the internal error type mapped to the public error envelope.
type Error struct {
	Status     int
	Code       string
	Message    string
	Retryable  bool
	RetryAfter int // seconds, 0 = default
	Details    map[string]any
}

func (e *Error) Error() string { return fmt.Sprintf("%d %s: %s", e.Status, e.Code, e.Message) }

// Err builds a stable error.
func Err(status int, code, msg string, retryable bool) *Error {
	return &Error{Status: status, Code: code, Message: msg, Retryable: retryable}
}

// Well-known errors from the specification (§4).
var (
	ErrInvalidRequest         = Err(http.StatusBadRequest, "invalid_request", "请求无效", false)
	ErrSessionInvalid         = Err(http.StatusUnauthorized, "session_invalid", "会话无效或已过期", false)
	ErrAccountDisabled        = Err(http.StatusForbidden, "account_disabled", "账号已被禁用", false)
	ErrSendNotAllowed         = Err(http.StatusForbidden, "send_not_allowed", "当前无权发送", false)
	ErrAINotEnabled           = Err(http.StatusForbidden, "ai_not_enabled", "未开通 AI 服务", false)
	ErrAIGatewayNotConfigured = Err(http.StatusServiceUnavailable, "ai_gateway_not_configured", "平台尚未配置可用的 AI 网关", true)
	ErrInsufficientPoints     = Err(http.StatusConflict, "insufficient_points", "积分不足", false)
	// ErrAIGatewayRejected is what a refusal by the upstream gateway becomes: the
	// platform's own key or permissions are wrong, which no end user can act on.
	// It is deliberately not ai_not_enabled, which tells a user they lack an
	// entitlement they actually have.
	ErrAIGatewayRejected    = Err(http.StatusBadGateway, "ai_gateway_rejected", "平台 AI 网关拒绝了请求（密钥或权限问题）", false)
	ErrForbidden            = Err(http.StatusForbidden, "forbidden", "无权访问", false)
	ErrCSRF                 = Err(http.StatusForbidden, "csrf_failed", "缺少或错误的 CSRF 令牌", false)
	ErrCrossOrigin          = Err(http.StatusForbidden, "cross_origin", "跨站请求被拒绝", false)
	ErrNotFound             = Err(http.StatusNotFound, "not_found", "资源不存在", false)
	ErrIdemConflict         = Err(http.StatusConflict, "idempotency_conflict", "同一请求标识对应不同内容", false)
	ErrInvalidState         = Err(http.StatusConflict, "invalid_state", "当前状态不允许该操作", false)
	ErrGroupFull            = Err(http.StatusConflict, "group_full", "群成员已满", false)
	ErrCursorExpired        = Err(http.StatusGone, "sync_cursor_expired", "同步游标已过期，请获取快照", true)
	ErrImageAccessExpired   = Err(http.StatusGone, "image_access_expired", "图片访问已过期", false)
	ErrTooLarge             = Err(http.StatusRequestEntityTooLarge, "payload_too_large", "请求体超过限制", false)
	ErrRateLimited          = Err(http.StatusTooManyRequests, "rate_limited", "请求过于频繁", true)
	ErrStorage              = Err(http.StatusServiceUnavailable, "storage_unavailable", "存储暂不可用", true)
	ErrCapacity             = Err(http.StatusServiceUnavailable, "capacity_exceeded", "容量已达上限", true)
	ErrGatewayUnavailable   = Err(http.StatusServiceUnavailable, "gateway_unavailable", "AI 网关暂不可用", true)
	ErrMaintenance          = Err(http.StatusServiceUnavailable, "maintenance", "系统维护中，暂不接受写入", true)
	ErrUnsupportedMediaType = Err(http.StatusUnsupportedMediaType, "unsupported_media_type", "不支持的媒体类型", false)
	ErrInternal             = Err(http.StatusInternalServerError, "internal_error", "服务内部错误", true)
)

// AsError maps any error to a public *Error, preserving codes we know.
func AsError(err error) *Error {
	var e *Error
	if errors.As(err, &e) {
		return e
	}
	return ErrInternal
}

// WithMessage clones with a specific message.
func (e *Error) WithCode(code string) *Error {
	c := *e
	c.Code = code
	return &c
}

func (e *Error) WithMessage(msg string) *Error {
	c := *e
	c.Message = msg
	return &c
}

// WithRetryAfter clones with a server-side retry hint.
func (e *Error) WithRetryAfter(d time.Duration) *Error {
	c := *e
	c.RetryAfter = int(d.Seconds()) + 1
	return &c
}

// WithStatus clones with a specific status.
func (e *Error) WithStatus(status int) *Error {
	c := *e
	c.Status = status
	return &c
}
