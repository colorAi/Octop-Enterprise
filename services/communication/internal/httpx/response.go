package httpx

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"sync"
	"time"

	"octop/communication/internal/idgen"
)

type ctxKeyType string

// RequestIDKey carries the per-request id through contexts.
const RequestIDKey ctxKeyType = "request_id"

// RequestID returns the active request id (or a fresh one).
func RequestID(ctx context.Context) string {
	if s, ok := ctx.Value(RequestIDKey).(string); ok && s != "" {
		return s
	}
	return "req_" + idgen.Random(16)
}

// WriteData emits the success envelope.
func WriteData(w http.ResponseWriter, r *http.Request, status int, data any) {
	setCommon(w, r)
	if status == 0 {
		status = http.StatusOK
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"request_id": RequestID(r.Context()), "data": data})
}

// WriteError emits the error envelope with the stable code.
func WriteError(w http.ResponseWriter, r *http.Request, e *Error) {
	setCommon(w, r)
	if e.RetryAfter > 0 {
		w.Header().Set("Retry-After", strconv.Itoa(e.RetryAfter))
	} else if e.Retryable {
		w.Header().Set("Retry-After", "2")
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(e.Status)
	er := map[string]any{"code": e.Code, "message": e.Message, "retryable": e.Retryable}
	for k, v := range e.Details {
		er[k] = v
	}
	_ = json.NewEncoder(w).Encode(map[string]any{"request_id": RequestID(r.Context()), "error": er})
}

// WriteErrorOf maps any error through AsError.
func WriteErrorOf(w http.ResponseWriter, r *http.Request, err error) {
	WriteError(w, r, AsError(err))
}

func setCommon(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if rid := RequestID(r.Context()); rid != "" {
		w.Header().Set("X-Request-Id", rid)
	}
}

// ------------------------------------------------------------------ decode

// DecodeJSON reads at most maxBytes of strict JSON (unknown fields rejected).
func DecodeJSON(w http.ResponseWriter, r *http.Request, v any, maxBytes int64) error {
	if r.Body == nil {
		return ErrInvalidRequest.WithMessage("缺少请求体")
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxBytes)
	defer r.Body.Close()
	d := json.NewDecoder(r.Body)
	d.DisallowUnknownFields()
	if err := d.Decode(v); err != nil {
		var maxErr *http.MaxBytesError
		if e, ok := err.(*json.UnmarshalTypeError); ok && e != nil {
			return ErrInvalidRequest.WithMessage("字段类型错误：" + e.Field)
		}
		if errorsAsMax(err, &maxErr) {
			return ErrTooLarge
		}
		if err == io.EOF {
			return ErrInvalidRequest.WithMessage("缺少请求体")
		}
		return ErrInvalidRequest.WithMessage("请求 JSON 无效")
	}
	var extra any
	if err := d.Decode(&extra); err != io.EOF {
		return ErrInvalidRequest.WithMessage("请求包含多余 JSON 数据")
	}
	return nil
}

// DecodeJSONLenient allows unknown fields (used for passthrough upstream bodies).
func DecodeJSONLenient(r *http.Request, v any, maxBytes int64) error {
	r.Body = http.MaxBytesReader(nil, r.Body, maxBytes)
	defer r.Body.Close()
	return json.NewDecoder(r.Body).Decode(v)
}

func errorsAsMax(err error, target **http.MaxBytesError) bool {
	for err != nil {
		if t, ok := err.(*http.MaxBytesError); ok {
			*target = t
			return true
		}
		u, ok := err.(interface{ Unwrap() error })
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}

// ---------------------------------------------------------------- pagination

// PageParams resolves limit with defaults per contract.
func PageParams(r *http.Request, def, max int) int {
	q := r.URL.Query().Get("limit")
	if q == "" {
		return def
	}
	n, err := strconv.Atoi(q)
	if err != nil || n < 1 {
		return def
	}
	return min(n, max)
}

// ------------------------------------------------------------------ limiter

type bucket struct {
	tokens   float64
	last     time.Time
	capacity float64
	rate     float64 // tokens per second
}

// Limiter is a small concurrency-safe token bucket per key.
type Limiter struct {
	mu sync.Mutex
	m  map[string]*bucket
}

// NewLimiter returns an empty limiter.
func NewLimiter() *Limiter { return &Limiter{m: map[string]*bucket{}} }

// Allow consumes one token for key with the given capacity/rate.
func (l *Limiter) Allow(key string, capacity int, perSecond float64) (ok bool, retryAfter time.Duration) {
	l.mu.Lock()
	defer l.mu.Unlock()
	b, exists := l.m[key]
	now := time.Now()
	if !exists {
		b = &bucket{tokens: float64(capacity), last: now, capacity: float64(capacity), rate: perSecond}
		l.m[key] = b
	}
	b.tokens = min(b.capacity, b.tokens+now.Sub(b.last).Seconds()*b.rate)
	b.last = now
	if b.tokens >= 1 {
		b.tokens--
		return true, 0
	}
	deficit := 1 - b.tokens
	if b.rate <= 0 {
		return false, time.Minute
	}
	return false, time.Duration(deficit / b.rate * float64(time.Second))
}

// CountWindow counts events within a window per key (for daily/quota-lite limits).
type CountWindow struct {
	mu sync.Mutex
	m  map[string]windowState
}

type windowState struct {
	n   int
	acc int64
}

// NewCountWindow returns an empty window counter.
func NewCountWindow() *CountWindow { return &CountWindow{m: map[string]windowState{}} }

// Hit adds n and returns the total in the window starting at `windowStart`.
func (c *CountWindow) Hit(key string, windowStart time.Time, n int) (total int, started time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	ws := c.m[key]
	if ws.acc != unixDay(windowStart) {
		ws = windowState{acc: unixDay(windowStart)}
	}
	ws.n += n
	c.m[key] = ws
	return ws.n, windowStart
}

func unixDay(t time.Time) int64 { return t.Unix() / 86400 }
