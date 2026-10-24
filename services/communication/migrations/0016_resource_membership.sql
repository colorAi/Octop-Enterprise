ALTER TABLE users ADD COLUMN resource_revision BIGINT NOT NULL DEFAULT 0;
CREATE TABLE resource_department_operations (
    operation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    expected_revision BIGINT NOT NULL,
    department_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('complete','cancelled'))
);
