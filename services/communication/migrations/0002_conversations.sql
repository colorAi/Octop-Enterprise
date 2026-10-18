-- Conversations, group settings and membership intervals (member_epochs).

CREATE TABLE conversations (
  id         text PRIMARY KEY,
  type       text NOT NULL CHECK (type IN ('private','group')),
  status     text NOT NULL DEFAULT 'active' CHECK (status IN ('active','dissolved')),
  last_seq   bigint NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
  created_by text NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  dissolved_at timestamptz NULL
);

CREATE TABLE direct_pairs (
  conversation_id text PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
  user_low        text NOT NULL CHECK (user_low < user_high),
  user_high       text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_low, user_high)
);

CREATE TABLE group_settings (
  conversation_id    text PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
  owner_id           text NOT NULL,
  name               text NOT NULL DEFAULT '',
  avatar_asset_id    text NULL,
  announcement       text NOT NULL DEFAULT '',
  members_can_invite bool NOT NULL DEFAULT false,
  mute_all           bool NOT NULL DEFAULT false,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX group_settings_owner_idx ON group_settings(owner_id);

CREATE TABLE member_epochs (
  id                  text PRIMARY KEY,
  conversation_id     text NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_id             text NOT NULL,
  epoch               int NOT NULL CHECK (epoch >= 1),
  role                text NOT NULL DEFAULT 'member' CHECK (role IN ('owner','admin','member')),
  visible_from_seq    bigint NOT NULL CHECK (visible_from_seq >= 0),
  visible_through_seq bigint NULL CHECK (visible_through_seq IS NULL OR visible_through_seq >= visible_from_seq),
  my_nickname         text NOT NULL DEFAULT '',
  mute_until          timestamptz NULL,
  joined_at           timestamptz NOT NULL DEFAULT now(),
  left_at             timestamptz NULL,
  leave_reason        text NOT NULL DEFAULT '',
  UNIQUE (conversation_id, user_id, epoch)
);
-- one active interval per user/group
CREATE UNIQUE INDEX member_epochs_one_active_idx ON member_epochs(conversation_id, user_id) WHERE left_at IS NULL;
CREATE INDEX member_epochs_active_conv_idx ON member_epochs(conversation_id) WHERE left_at IS NULL;
CREATE INDEX member_epochs_active_user_idx ON member_epochs(user_id) WHERE left_at IS NULL;
CREATE INDEX member_epochs_conv_all_idx ON member_epochs(conversation_id, user_id, epoch);
