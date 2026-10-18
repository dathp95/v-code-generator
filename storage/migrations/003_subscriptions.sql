CREATE TABLE subscription_orders (
    order_id TEXT PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    customer TEXT NOT NULL,
    device_id TEXT NOT NULL,
    edition TEXT NOT NULL,
    months INTEGER NOT NULL CHECK (typeof(months) = 'integer' AND months > 0),
    purchased_days INTEGER NOT NULL CHECK (typeof(purchased_days) = 'integer' AND purchased_days BETWEEN 1 AND 3650),
    unit_price_vnd INTEGER NOT NULL CHECK (typeof(unit_price_vnd) = 'integer' AND unit_price_vnd > 0),
    total_vnd INTEGER NOT NULL CHECK (typeof(total_vnd) = 'integer' AND total_vnd = months * unit_price_vnd),
    currency TEXT NOT NULL CHECK (currency = 'VND'),
    payment_details_json TEXT NOT NULL,
    qr_image BLOB NOT NULL,
    payment_status TEXT NOT NULL CHECK (payment_status IN ('awaiting_payment', 'payment_reported', 'paid', 'cancelled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reported_at TEXT,
    paid_at TEXT,
    confirmed_by TEXT,
    cancelled_at TEXT,
    request_id TEXT UNIQUE REFERENCES requests(request_id),
    notification_status TEXT CHECK (notification_status IN ('attempting', 'sent', 'unknown', 'failed')),
    notification_claim TEXT,
    notification_at TEXT,
    notification_finished_at TEXT,
    CHECK (payment_status != 'paid' OR (paid_at IS NOT NULL AND confirmed_by IS NOT NULL AND request_id IS NOT NULL))
);
CREATE UNIQUE INDEX one_unpaid_order ON subscription_orders(user_id, recipient_id, device_id)
    WHERE payment_status IN ('awaiting_payment', 'payment_reported');
CREATE TABLE payment_confirmations (
    confirmation_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES subscription_orders(order_id),
    admin_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);
CREATE INDEX payment_forms_admin ON payment_confirmations(admin_id, chat_id, consumed_at);
DROP INDEX one_open_request;
CREATE UNIQUE INDEX one_open_request ON requests(channel, user_id, device_id)
    WHERE status IN ('pending', 'approved') AND channel != 'telegram_subscription';
PRAGMA user_version = 3;
