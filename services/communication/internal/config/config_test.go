package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDatabasePoolLimit(t *testing.T) {
	secret := filepath.Join(t.TempDir(), "secret")
	if err := os.WriteFile(secret, []byte("01234567890123456789012345678901"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("OCTOP_IM_IDENTITY_KEY_FILE", secret)
	t.Setenv("OCTOP_IM_CURSOR_KEY_FILE", secret)
	t.Setenv("OCTOP_IM_DATABASE_URL", "postgresql://test@localhost/test")
	t.Setenv("OCTOP_IM_POOL_MAX_CONNS", "8")
	cfg, err := Load()
	if err != nil || cfg.Resource.PoolMaxConns != 8 {
		t.Fatalf("pool configuration not applied: %v, %d", err, cfg.Resource.PoolMaxConns)
	}
	for _, invalid := range []string{"0", "65", "invalid"} {
		t.Setenv("OCTOP_IM_POOL_MAX_CONNS", invalid)
		if _, err := Load(); err == nil {
			t.Fatalf("accepted invalid pool limit %q", invalid)
		}
	}
}
