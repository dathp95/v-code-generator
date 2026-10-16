ALTER TABLE requests ADD COLUMN rejected_by TEXT;
ALTER TABLE requests ADD COLUMN rejected_at TEXT;
ALTER TABLE requests ADD COLUMN rejection_reason TEXT;

DROP INDEX delivery_attempt;
ALTER TABLE deliveries RENAME TO deliveries_v1;
CREATE TABLE deliveries (
    delivery_id INTEGER PRIMARY KEY,
    request_id TEXT REFERENCES requests(request_id),
    license_id TEXT REFERENCES licenses(license_id),
    destination TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN (
        'admin_notification', 'customer_license', 'manual_license', 'cli_stdout', 'cli_file'
    )),
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    status TEXT NOT NULL CHECK (status IN (
        'attempting', 'unknown', 'succeeded', 'retryable_failed', 'permanent_failed'
    )),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_category TEXT CHECK (error_category IN (
        'timeout', 'network', 'rate_limited', 'forbidden', 'bad_request', 'telegram', 'output_io'
    )),
    telegram_message_id INTEGER,
    scope TEXT NOT NULL,
    claim_owner TEXT,
    lease_until TEXT,
    retry_not_before TEXT,
    CHECK (request_id IS NOT NULL OR license_id IS NOT NULL)
);
INSERT INTO deliveries (
    delivery_id, request_id, license_id, destination, purpose, attempt_number,
    status, started_at, finished_at, error_category, telegram_message_id, scope
)
SELECT delivery_id, request_id, license_id, destination, purpose, attempt_number,
    CASE WHEN status = 'attempting' OR (status = 'retryable_failed' AND error_category = 'timeout')
         THEN 'unknown' ELSE status END,
    started_at, finished_at, error_category, telegram_message_id,
    CASE WHEN license_id IS NOT NULL THEN 'license:' || license_id
         ELSE 'request:' || request_id END || ':' || purpose
FROM deliveries_v1;
DROP TABLE deliveries_v1;
CREATE UNIQUE INDEX delivery_attempt ON deliveries(
    coalesce(request_id, ''), coalesce(license_id, ''), destination, purpose, attempt_number
);
CREATE UNIQUE INDEX one_active_delivery ON deliveries(scope) WHERE status = 'attempting';

CREATE TABLE approval_forms (
    form_id TEXT PRIMARY KEY,
    admin_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    request_id TEXT NOT NULL REFERENCES requests(request_id),
    action TEXT NOT NULL CHECK (action IN ('selected', 'approve', 'reject')),
    days INTEGER,
    reason TEXT,
    request_version TEXT,
    license_id TEXT,
    delivery_version TEXT,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);
CREATE INDEX active_approval_forms ON approval_forms(admin_id, chat_id, consumed_at);
PRAGMA user_version = 2;
