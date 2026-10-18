-- Persistent background jobs, delivery outbox and push tokens.

CREATE TABLE background_jobs (
  id              text PRIMARY KEY,
  kind            text NOT NULL,
  work_key        text NOT NULL UNIQUE,
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
  state           text NOT NULL DEFAULT 'pending'
                  CHECK (state IN ('pending','leased','retry_wait','succeeded','failed','skipped')),
  attempts        int NOT NULL DEFAULT 0,
  max_attempts    int NOT NULL DEFAULT 12,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  lease_until     timestamptz NULL,
  last_error      text NOT NULL DEFAULT '',
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz NULL
);
CREATE INDEX background_jobs_due_idx ON background_jobs(state, next_attempt_at)
  WHERE state IN ('pending','retry_wait','leased');

CREATE TABLE delivery_outbox (
  id                text PRIMARY KEY,
  kind              text NOT NULL CHECK (kind IN ('sync_wakeup')),
  recipient_user_id text NOT NULL,
  work_key          text NOT NULL UNIQUE,
  payload           jsonb NOT NULL DEFAULT '{}'::jsonb,
  state             text NOT NULL DEFAULT 'pending'
                    CHECK (state IN ('pending','leased','retry_wait','succeeded','failed','skipped')),
  attempts          int NOT NULL DEFAULT 0,
  max_attempts      int NOT NULL DEFAULT 12,
  next_attempt_at   timestamptz NOT NULL DEFAULT now(),
  lease_until       timestamptz NULL,
  last_error        text NOT NULL DEFAULT '',
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  finished_at       timestamptz NULL
);
CREATE INDEX delivery_outbox_due_idx ON delivery_outbox(state, next_attempt_at)
  WHERE state IN ('pending','retry_wait','leased');
CREATE INDEX delivery_outbox_recipient_idx ON delivery_outbox(recipient_user_id)
  WHERE state IN ('pending','retry_wait','leased');
