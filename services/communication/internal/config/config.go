package config

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

type Config struct {
	Listen, DatabaseURL, MediaRoot, IdentityKey, CursorKey    string
	SyncRetention, SnapshotTTL, RecallWindow, JobPollInterval time.Duration
	Limits                                                    Limits
	Resource                                                  Resource
	Workers                                                   Workers
}
type Limits struct{ MaxTextChars, MaxImageCaption, MaxImagesPerMessage, GroupMaxMembers, UserMaxGroups, UserMaxOwnedGroups, ListDefault, ListMax, SyncPageMax int }
type Resource struct {
	MediaMaxImageBytes, MediaMaxPixels, StorageMinFreeBytes int64
	WSSMaxConns, WSSMaxPerUser                              int
	PoolMaxConns                                            int32
}
type Workers struct{ JobTotal, OutboxTotal int }

func Load() (Config, error) {
	c := Config{Listen: "0.0.0.0:8090", DatabaseURL: os.Getenv("OCTOP_IM_DATABASE_URL"), MediaRoot: os.Getenv("OCTOP_IM_MEDIA_ROOT"),
		SyncRetention: 30 * 24 * time.Hour, SnapshotTTL: 15 * time.Minute, RecallWindow: 2 * time.Minute, JobPollInterval: time.Second,
		Limits: Limits{8000, 2000, 9, 500, 100, 20, 30, 100, 200}, Resource: Resource{10 << 20, 20_000_000, 512 << 20, 2000, 8, 24}, Workers: Workers{2, 2}}
	if v := os.Getenv("OCTOP_IM_LISTEN"); v != "" {
		c.Listen = v
	}
	if c.MediaRoot == "" {
		c.MediaRoot = "/data/media"
	}
	for _, pair := range []struct {
		env string
		dst *string
	}{{"OCTOP_IM_IDENTITY_KEY_FILE", &c.IdentityKey}, {"OCTOP_IM_CURSOR_KEY_FILE", &c.CursorKey}} {
		b, e := os.ReadFile(os.Getenv(pair.env))
		if e != nil || len(b) < 32 {
			return c, fmt.Errorf("%s must name a secret file with at least 32 bytes", pair.env)
		}
		*pair.dst = string(b)
	}
	if v := os.Getenv("OCTOP_IM_GROUP_MAX_MEMBERS"); v != "" {
		n, e := strconv.Atoi(v)
		if e != nil || n < 1 || n > 10000 {
			return c, fmt.Errorf("invalid group capacity")
		}
		c.Limits.GroupMaxMembers = n
	}
	if c.DatabaseURL == "" {
		return c, fmt.Errorf("OCTOP_IM_DATABASE_URL is required")
	}
	if v := os.Getenv("OCTOP_IM_POOL_MAX_CONNS"); v != "" {
		n, e := strconv.Atoi(v)
		if e != nil || n < 1 || n > 64 {
			return c, fmt.Errorf("OCTOP_IM_POOL_MAX_CONNS must be between 1 and 64")
		}
		c.Resource.PoolMaxConns = int32(n)
	}
	return c, nil
}
