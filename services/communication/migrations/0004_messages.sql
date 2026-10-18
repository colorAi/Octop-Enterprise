-- Messages with per-conversation sequence, receipts, attachments and per-user state.

CREATE TABLE messages (
  id                  text PRIMARY KEY,
  conversation_id     text NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  seq                 bigint NOT NULL CHECK (seq >= 1),
  sender_id           text NULL,               -- NULL for system messages
  client_message_id   text NULL,
  type                text NOT NULL CHECK (type IN ('text','image','system')),
  text                text NULL,               -- NULL after recall (content not recoverable)
  system_kind         text NOT NULL DEFAULT '',-- join/leave/remove/role/mute/dissolve/transfer...
  reply_to_message_id text NULL REFERENCES messages(id),
  mentions            jsonb NOT NULL DEFAULT '[]'::jsonb,  -- sorted user ids
  mention_all         bool NOT NULL DEFAULT false,
  recalled_at         timestamptz NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (conversation_id, seq)
);
-- Sender-side send idempotency (system messages excluded).
CREATE UNIQUE INDEX messages_sender_idem_idx ON messages(sender_id, client_message_id)
  WHERE client_message_id IS NOT NULL;
CREATE INDEX messages_conv_seq_idx ON messages(conversation_id, seq DESC);
CREATE INDEX messages_reply_idx ON messages(reply_to_message_id);

CREATE TABLE message_receipts (
  id                text PRIMARY KEY,
  sender_id         text NOT NULL,
  client_message_id text NOT NULL,
  content_digest    bytea NOT NULL,            -- normalized canonical digest of the request
  result            jsonb NOT NULL,            -- minimal original receipt, replayable
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (sender_id, client_message_id)
);

CREATE TABLE message_attachments (
  id         text PRIMARY KEY,
  message_id text NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  asset_id   text NOT NULL REFERENCES media_assets(id),
  position   int  NOT NULL CHECK (position >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (message_id, position),
  UNIQUE (message_id, asset_id)
);
CREATE INDEX message_attachments_asset_idx ON message_attachments(asset_id);

CREATE TABLE conversation_user_state (
  id                  text PRIMARY KEY,
  conversation_id     text NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_id             text NOT NULL,
  last_read_seq       bigint NOT NULL DEFAULT 0 CHECK (last_read_seq >= 0),
  pinned              bool NOT NULL DEFAULT false,
  muted               bool NOT NULL DEFAULT false,
  hidden_at_seq       bigint NULL,             -- hidden until a new message beyond this seq
  cleared_through_seq bigint NOT NULL DEFAULT 0 CHECK (cleared_through_seq >= 0),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (conversation_id, user_id)
);
CREATE INDEX conversation_user_state_user_idx ON conversation_user_state(user_id);
