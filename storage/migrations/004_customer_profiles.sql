CREATE TABLE customer_profiles (
    channel TEXT NOT NULL,
    user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '' CHECK (length(company) <= 150),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (channel, user_id)
);
WITH history AS (
    SELECT CASE WHEN channel = 'telegram_subscription' THEN 'telegram' ELSE channel END AS channel,
        user_id, customer AS display_name, created_at, updated_at FROM requests
    UNION ALL
    SELECT 'telegram', user_id, customer, created_at, updated_at FROM subscription_orders
), ranked AS (
    SELECT *, row_number() OVER (PARTITION BY channel, user_id ORDER BY updated_at DESC, display_name) AS rank,
        min(created_at) OVER (PARTITION BY channel, user_id) AS first_seen,
        max(updated_at) OVER (PARTITION BY channel, user_id) AS last_seen FROM history
)
INSERT INTO customer_profiles (channel, user_id, display_name, company, created_at, updated_at)
SELECT channel, user_id, display_name, '', first_seen, last_seen FROM ranked WHERE rank = 1;
ALTER TABLE subscription_orders ADD COLUMN company TEXT NOT NULL DEFAULT '' CHECK (length(company) <= 150);
PRAGMA user_version = 4;
