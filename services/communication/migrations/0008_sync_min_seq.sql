-- Oldest retained event per user (expired-cursor detection).
ALTER TABLE user_sync_heads ADD COLUMN IF NOT EXISTS min_seq bigint NOT NULL DEFAULT 0 CHECK (min_seq >= 0);
