// Package testdb provisions isolated PostgreSQL databases for integration tests.
// It never touches the reference projects' databases: names are explicit and
// scoped to this project, created from OCTOP_IM_TEST_DATABASE_URL.
package testdb

import (
	"context"
	"fmt"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"octop/communication/internal/store"
)

var counter atomic.Uint64

// New creates a fresh database (schema migrated) and returns its DSN.
// Call t.Cleanup for the drop (unless OCTOP_IM_TESTDB_KEEP=1).
func New(t *testing.T) string {
	t.Helper()
	adminURL := os.Getenv("OCTOP_IM_TEST_DATABASE_URL")
	if adminURL == "" {
		t.Skip("OCTOP_IM_TEST_DATABASE_URL not set; integration tests require the isolated dev cluster (make pg-up)")
	}
	name := fmt.Sprintf("octop_im_test_%s_%d", sanitize(t.Name()), counter.Add(1))
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	admin, err := store.Open(ctx, adminURL, 2)
	if err != nil {
		t.Fatalf("testdb admin connect: %v", err)
	}
	defer admin.Close()
	owner := os.Getenv("OCTOP_IM_TEST_OWNER") // dedicated test database owner role
	if owner == "" {
		if err := admin.Pool.QueryRow(ctx, "SELECT current_user").Scan(&owner); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := admin.Pool.Exec(ctx, fmt.Sprintf(`CREATE DATABASE %s OWNER %s`, pgIdent(name), pgIdent(owner))); err != nil {
		t.Fatalf("testdb create: %v", err)
	}
	if os.Getenv("OCTOP_IM_TESTDB_KEEP") != "1" {
		t.Cleanup(func() {
			c, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			a, err := store.Open(c, adminURL, 1)
			if err == nil {
				defer a.Close()
				_, _ = a.Pool.Exec(c, "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname="+pgQuote(name))
				_, _ = a.Pool.Exec(c, "DROP DATABASE IF EXISTS "+pgIdent(name))
			}
		})
	}
	dsn := withDatabase(adminURL, name)
	c, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	st, err := store.Open(c, dsn, 4)
	if err != nil {
		t.Fatalf("testdb open: %v", err)
	}
	if _, err := st.Migrate(c); err != nil {
		st.Close()
		t.Fatalf("testdb migrate: %v", err)
	}
	st.Close()
	return dsn
}

// Open connects a store to an existing test database.
func Open(t *testing.T, dsn string) *store.Store {
	t.Helper()
	st, err := store.Open(context.Background(), dsn, 8)
	if err != nil {
		t.Fatalf("testdb open: %v", err)
	}
	t.Cleanup(st.Close)
	return st
}

func withDatabase(url, db string) string {
	// naive but sufficient for postgres:// URLs used by tests
	if i := strings.LastIndex(url, "/"); i >= 0 {
		if j := strings.Index(url[i:], "?"); j >= 0 {
			return url[:i+1] + db + url[i+j:]
		}
		return url[:i+1] + db
	}
	return url
}

func sanitize(s string) string {
	s = strings.ToLower(s)
	for _, r := range []string{"/", " ", "#", ".", "$"} {
		s = strings.ReplaceAll(s, r, "_")
	}
	if len(s) > 40 {
		s = s[:40]
	}
	return s
}

func pgIdent(s string) string {
	return `"` + strings.ReplaceAll(s, `"`, `""`) + `"`
}

func pgQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", "''") + "'"
}
