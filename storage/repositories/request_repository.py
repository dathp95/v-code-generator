"""Request identity and approval state, independent of message delivery."""

from datetime import UTC, datetime
from uuid import uuid4

from core.crypto.activation import validate_device_id, validate_non_empty
from storage.database import Database, utc_now


class RequestRepository:
    def __init__(self, database: Database):
        self.database = database

    def get(self, request_id, *, connection=None):
        if connection is None:
            with self.database.connection() as connection:
                return self.get(request_id, connection=connection)
        row = connection.execute("SELECT * FROM requests WHERE request_id = ?", (request_id,)).fetchone()
        return dict(row) if row else None

    def create_or_get_pending(self, *, channel, recipient_id, user_id, customer, device_id):
        device_id = validate_device_id(device_id)
        customer = validate_non_empty("customer", customer)
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT r.*, l.expires_at FROM requests r
                   LEFT JOIN licenses l ON l.request_id = r.request_id
                   WHERE r.channel = ? AND r.user_id = ? AND r.device_id = ?
                     AND r.status IN ('pending', 'approved')""",
                (channel, str(user_id), device_id),
            ).fetchone()
            now = utc_now()
            if row is not None:
                if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
                    connection.execute(
                        "UPDATE requests SET status = 'closed', updated_at = ? WHERE request_id = ?",
                        (now, row["request_id"]),
                    )
                else:
                    # Keep the persisted recipient mapping; caller cannot redirect an issued key.
                    return self.get(row["request_id"], connection=connection)
            request_id = uuid4().hex
            connection.execute(
                """INSERT INTO requests
                   (request_id, channel, recipient_id, user_id, customer, device_id, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (request_id, channel, str(recipient_id), str(user_id), customer, device_id, now, now),
            )
            return self.get(request_id, connection=connection)

    def approve(self, request_id, *, connection):
        connection.execute(
            "UPDATE requests SET status = 'approved', updated_at = ? WHERE request_id = ?",
            (utc_now(), request_id),
        )
