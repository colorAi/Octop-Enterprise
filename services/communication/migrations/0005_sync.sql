-- Per-user sync counters, ordered events and materialized snapshots.

CREATE TABLE user_sync_heads (
  user_id    text PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  counter    bigint NOT NULL DEFAULT 0 CHECK (counter >= 0),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_sync_events (
  user_id    text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  seq        bigint NOT NULL CHECK (seq >= 1),
  type       text NOT NULL,
  object_id  text NOT NULL DEFAULT '',
  version    bigint NOT NULL DEFAULT 0,
  payload    jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, seq)
);
CREATE INDEX user_sync_events_created_idx ON user_sync_events(user_id, created_at);

CREATE TABLE sync_snapshots (
  id          text PRIMARY KEY,
  user_id     text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  until_seq   bigint NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL,
  chunk_count int NOT NULL
);
CREATE INDEX sync_snapshots_user_idx ON sync_snapshots(user_id, created_at DESC);

CREATE TABLE snapshot_chunks (
  snapshot_id text NOT NULL REFERENCES sync_snapshots(id) ON DELETE CASCADE,
  chunk_index int NOT NULL CHECK (chunk_index >= 0),
  payload     jsonb NOT NULL,
  PRIMARY KEY (snapshot_id, chunk_index)
);
