CREATE TABLE requests (
    request_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    customer TEXT NOT NULL,
    device_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'closed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX one_open_request ON requests(channel, user_id, device_id)
    WHERE status IN ('pending', 'approved');

CREATE TABLE licenses (
    license_id TEXT PRIMARY KEY,
    request_id TEXT UNIQUE REFERENCES requests(request_id),
    activation_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    edition TEXT NOT NULL,
    duration_days INTEGER NOT NULL CHECK (duration_days BETWEEN 1 AND 3650),
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    issuer TEXT NOT NULL,
    source TEXT NOT NULL
);

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
        'attempting', 'succeeded', 'retryable_failed', 'permanent_failed'
    )),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_category TEXT CHECK (error_category IN (
        'timeout', 'network', 'rate_limited', 'forbidden', 'bad_request', 'telegram', 'output_io'
    )),
    telegram_message_id INTEGER,
    CHECK (request_id IS NOT NULL OR license_id IS NOT NULL)
);

CREATE UNIQUE INDEX delivery_attempt ON deliveries(
    coalesce(request_id, ''), coalesce(license_id, ''), destination, purpose, attempt_number
);

PRAGMA user_version = 1;
