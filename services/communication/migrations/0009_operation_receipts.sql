-- operation_receipts: generic idempotency receipts for non-message operations
-- (group creation, invitations) keyed by (actor, scope, idempotency key).

CREATE TABLE operation_receipts (
  id             text PRIMARY KEY,
  actor_id       text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  scope          text NOT NULL CHECK (scope IN ('group.create','group.invite','department.group')),
  idem_key       text NOT NULL CHECK (length(idem_key) BETWEEN 1 AND 128),
  content_digest bytea NOT NULL CHECK (octet_length(content_digest) = 32),
  result         jsonb NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (actor_id, scope, idem_key)
);
