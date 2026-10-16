"""Each external delivery attempt has its own durable lifecycle."""

from storage.database import Database, utc_now


class DeliveryRepository:
    def __init__(self, database: Database):
        self.database = database

    def history(self, *, request_id=None, license_id=None, purpose):
        with self.database.connection() as connection:
            return [dict(row) for row in connection.execute(
                """SELECT * FROM deliveries WHERE request_id IS ? AND license_id IS ?
                   AND purpose = ? ORDER BY attempt_number, delivery_id""",
                (request_id, license_id, purpose),
            )]

    def succeeded(self, *, request_id=None, license_id=None, purpose):
        return any(row["status"] == "succeeded" for row in self.history(
            request_id=request_id, license_id=license_id, purpose=purpose,
        ))

    def start(self, *, destination, purpose, request_id=None, license_id=None):
        with self.database.transaction() as connection:
            number = connection.execute(
                """SELECT coalesce(max(attempt_number), 0) + 1 FROM deliveries
                   WHERE request_id IS ? AND license_id IS ? AND destination = ? AND purpose = ?""",
                (request_id, license_id, str(destination), purpose),
            ).fetchone()[0]
            cursor = connection.execute(
                """INSERT INTO deliveries
                   (request_id, license_id, destination, purpose, attempt_number, status, started_at)
                   VALUES (?, ?, ?, ?, ?, 'attempting', ?)""",
                (request_id, license_id, str(destination), purpose, number, utc_now()),
            )
            return cursor.lastrowid

    def finish(self, delivery_id, *, status, error_category=None, message_id=None):
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE deliveries SET status = ?, finished_at = ?, error_category = ?,
                   telegram_message_id = ? WHERE delivery_id = ? AND status = 'attempting'""",
                (status, utc_now(), error_category,
                 message_id if type(message_id) is int else None, delivery_id),
            )
