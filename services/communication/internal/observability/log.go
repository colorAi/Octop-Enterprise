// Package observability provides structured logging (JSON, slog) and metrics helpers.
package observability

import (
	"context"
	"io"
	"log/slog"
	"os"
	"sync/atomic"
)

// counters are process-level metrics for logs/diagnose output.
var counters = map[string]*atomic.Int64{}

func init() {
	for _, k := range MetricNames {
		counters[k] = &atomic.Int64{}
	}
}

// MetricNames are the counters exposed via admin diagnostics.
var MetricNames = []string{
	"messages_saved", "message_save_failed", "sync_events_emitted", "sync_backlog",
	"outbox_retries", "ai_uncertain", "db_pool_wait", "media_cleanup_deleted",
	"media_cleanup_failed", "wss_connections", "wss_notify_dropped", "push_delivered",
	"push_failed", "push_skipped", "login_attempts", "argon_runs",
}

// Inc adds to a metric counter (no-op for unknown names).
func Inc(name string, n int64) {
	if c, ok := counters[name]; ok {
		c.Add(n)
	}
}

// Snapshot returns current counter values.
func Snapshot() map[string]int64 {
	m := make(map[string]int64, len(counters))
	for k, v := range counters {
		m[k] = v.Load()
	}
	return m
}

// NewLogger builds the process logger. Secrets never reach these fields; the
// caller is responsible for not passing tokens/passwords as attributes.
func NewLogger(level string, w io.Writer) *slog.Logger {
	if w == nil {
		w = os.Stderr
	}
	lvl := slog.LevelInfo
	switch level {
	case "debug":
		lvl = slog.LevelDebug
	case "warn":
		lvl = slog.LevelWarn
	case "error":
		lvl = slog.LevelError
	}
	return slog.New(slog.NewJSONHandler(w, &slog.HandlerOptions{Level: lvl}))
}

type ctxKey struct{}

// With returns a logger enriched with contextual fields.
func With(ctx context.Context, l *slog.Logger, args ...any) *slog.Logger {
	if base, ok := ctx.Value(ctxKey{}).(*slog.Logger); ok && base != nil {
		l = base.With(args...)
	} else {
		l = l.With(args...)
	}
	return l
}

// Ctx stores a request logger for downstream calls.
func Ctx(ctx context.Context, l *slog.Logger) context.Context {
	return context.WithValue(ctx, ctxKey{}, l)
}
