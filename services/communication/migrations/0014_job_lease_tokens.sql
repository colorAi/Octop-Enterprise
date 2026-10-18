-- Each claim gets a fresh token, independent of retry counts and server instance.
-- Terminal writes/renewals must match it so a replaced worker cannot overwrite
-- a newer claim (including a later run of a recurring maintenance task).
ALTER TABLE background_jobs ADD COLUMN lease_token text NOT NULL DEFAULT '';
ALTER TABLE delivery_outbox ADD COLUMN lease_token text NOT NULL DEFAULT '';
