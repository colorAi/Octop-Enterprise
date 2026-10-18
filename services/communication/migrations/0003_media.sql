-- Private media: assets, variants, upload operations. Paths are server-generated only.

CREATE TABLE media_assets (
  id           text PRIMARY KEY,
  owner_id     text NOT NULL,
  purpose      text NOT NULL CHECK (purpose IN ('chat_image')),
  state        text NOT NULL DEFAULT 'initiated'
               CHECK (state IN ('initiated','uploaded','processing','ready','deleting','deleted','failed')),
  mime         text NOT NULL DEFAULT '',
  byte_size    bigint NOT NULL DEFAULT 0 CHECK (byte_size >= 0),
  width        int NULL,
  height       int NULL,
  sha256       bytea NULL,
  expected_sha256 bytea NULL,
  expected_size   bigint NULL CHECK (expected_size IS NULL OR expected_size >= 0),
  relpath      text NOT NULL DEFAULT '',   -- server-generated; never client controlled
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  ready_at     timestamptz NULL,
  -- access window for ai_result assets (2h) and cleanup guidance for others:
  access_expires_at timestamptz NULL,
  delete_after timestamptz NULL
);
CREATE INDEX media_assets_owner_idx ON media_assets(owner_id, state);
CREATE INDEX media_assets_pending_cleanup_idx ON media_assets(state, delete_after)
  WHERE state IN ('initiated','uploaded','failed','deleting');
CREATE INDEX media_assets_ready_idx ON media_assets(id) WHERE state = 'ready';

CREATE TABLE media_variants (
  asset_id  text NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
  kind      text NOT NULL CHECK (kind IN ('original','thumbnail','avatar_public')),
  relpath   text NOT NULL,
  mime      text NOT NULL,
  width     int NULL,
  height    int NULL,
  byte_size bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (asset_id, kind)
);

CREATE TABLE upload_operations (
  id            text PRIMARY KEY,          -- upload_id; 1:1 with the asset it creates
  asset_id      text NOT NULL UNIQUE REFERENCES media_assets(id) ON DELETE CASCADE,
  owner_id      text NOT NULL,
  purpose       text NOT NULL CHECK (purpose IN ('chat_image')),
  expected_sha256 bytea NOT NULL,
  expected_size   bigint NOT NULL CHECK (expected_size > 0),
  received_size   bigint NOT NULL DEFAULT 0 CHECK (received_size >= 0),
  state         text NOT NULL DEFAULT 'created'
                CHECK (state IN ('created','writing','uploaded','processing','ready','failed','aborted')),
  tmp_path      text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  completed_at  timestamptz NULL,
  last_error    text NOT NULL DEFAULT ''
);
CREATE INDEX upload_operations_open_idx ON upload_operations(created_at)
  WHERE state IN ('created','writing','uploaded');
