-- Record which server instance holds a job lease (crash release/diagnostics).
ALTER TABLE background_jobs ADD COLUMN IF NOT EXISTS instance text NOT NULL DEFAULT '';
ALTER TABLE delivery_outbox ADD COLUMN IF NOT EXISTS instance text NOT NULL DEFAULT '';
