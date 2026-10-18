CREATE TABLE users (
 id text PRIMARY KEY, nickname text NOT NULL DEFAULT '', avatar_icon text NOT NULL DEFAULT '',
 avatar_asset_id text NULL, status text NOT NULL CHECK(status IN ('active','disabled','deleted')),
 identity_version bigint NOT NULL CHECK(identity_version>0), permissions jsonb NOT NULL DEFAULT '[]',
 notification_prefs jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE kv_settings(key text PRIMARY KEY,value jsonb NOT NULL,updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE server_heartbeats(instance_id text PRIMARY KEY,writes_in_flight int NOT NULL DEFAULT 0,accepting bool NOT NULL DEFAULT true,updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE user_blocks(id text PRIMARY KEY,blocker_id text NOT NULL,blocked_id text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(blocker_id,blocked_id));
