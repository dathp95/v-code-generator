"""Atomic delivery claims, leases, cooldowns, and fenced completion."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from storage.database import Database


LEASE_SECONDS = 120


class DeliveryBlocked(ValueError):
    def __init__(self, code, retry_at=None):
        self.code = code
        self.retry_at = retry_at
        super().__init__(code)


@dataclass(frozen=True)
class DeliveryClaim:
    delivery_id: int
    owner: str


def scope_for(request_id, license_id, purpose):
    return f"license:{license_id}:{purpose}" if license_id else f"request:{request_id}:{purpose}"


def delivery_version(row):
    return f"{row['delivery_id']}:{row['status']}:{row['retry_not_before']}" if row else ""


class DeliveryRepository:
    def __init__(self, database: Database):
        self.database = database

    def recover_stale(self, *, connection=None):
        if connection is None:
            with self.database.transaction() as connection:
                return self.recover_stale(connection=connection)
        connection.execute(
            """UPDATE deliveries SET status = 'unknown', finished_at = ?
               WHERE status = 'attempting' AND (lease_until IS NULL OR julianday(lease_until) <= julianday(?))""",
            (self.database.timestamp(), self.database.timestamp()),
        )

    def history(self, *, request_id=None, license_id=None, purpose):
        with self.database.connection() as connection:
            return [dict(row) for row in connection.execute(
                """SELECT * FROM deliveries WHERE request_id IS ? AND license_id IS ?
                   AND purpose = ? ORDER BY attempt_number, delivery_id""",
                (request_id, license_id, purpose),
            )]

    def latest(self, *, request_id=None, license_id=None, purpose, connection=None):
        if connection is None:
            self.recover_stale()
            with self.database.connection() as connection:
                return self.latest(request_id=request_id, license_id=license_id,
                                   purpose=purpose, connection=connection)
        row = connection.execute(
            "SELECT * FROM deliveries WHERE scope = ? ORDER BY delivery_id DESC LIMIT 1",
            (scope_for(request_id, license_id, purpose),),
        ).fetchone()
        return dict(row) if row else None

    def succeeded(self, *, request_id=None, license_id=None, purpose):
        with self.database.connection() as connection:
            return connection.execute(
                "SELECT 1 FROM deliveries WHERE scope = ? AND status = 'succeeded' LIMIT 1",
                (scope_for(request_id, license_id, purpose),),
            ).fetchone() is not None

    def start(self, *, destination, purpose, request_id=None, license_id=None,
              explicit_retry=False, expected_version=None):
        self.recover_stale()
        with self.database.transaction() as connection:
            scope = scope_for(request_id, license_id, purpose)
            rows = connection.execute(
                "SELECT * FROM deliveries WHERE scope = ? ORDER BY delivery_id DESC", (scope,),
            ).fetchall()
            if any(row["status"] == "succeeded" for row in rows):
                raise DeliveryBlocked("already_succeeded")
            if any(row["status"] == "attempting" for row in rows):
                raise DeliveryBlocked("in_progress")
            if expected_version is not None and delivery_version(rows[0] if rows else None) != expected_version:
                raise DeliveryBlocked("state_changed")
            now = self.database.now()
            for row in rows:
                if row["retry_not_before"] and datetime.fromisoformat(row["retry_not_before"]) > now:
                    raise DeliveryBlocked("cooldown", row["retry_not_before"])
            if rows and rows[0]["status"] in ("unknown", "permanent_failed") and not explicit_retry:
                raise DeliveryBlocked(rows[0]["status"])
            number = max((row["attempt_number"] for row in rows), default=0) + 1
            owner = uuid4().hex
            cursor = connection.execute(
                """INSERT INTO deliveries
                   (request_id, license_id, destination, purpose, attempt_number, status,
                    started_at, scope, claim_owner, lease_until)
                   VALUES (?, ?, ?, ?, ?, 'attempting', ?, ?, ?, ?)""",
                (request_id, license_id, str(destination), purpose, number, self.database.timestamp(),
                 scope, owner, (now + timedelta(seconds=LEASE_SECONDS)).isoformat()),
            )
            return DeliveryClaim(cursor.lastrowid, owner)

    def finish(self, claim, *, status, error_category=None, message_id=None, retry_after=None):
        with self.database.transaction() as connection:
            now = self.database.now()
            retry_at = (now + timedelta(seconds=retry_after)).isoformat() if retry_after else None
            cursor = connection.execute(
                """UPDATE deliveries SET status = ?, finished_at = ?, error_category = ?,
                   telegram_message_id = ?, retry_not_before = ?
                   WHERE delivery_id = ? AND claim_owner = ? AND status = 'attempting'
                     AND julianday(lease_until) > julianday(?)""",
                (status, self.database.timestamp(), error_category,
                 message_id if type(message_id) is int else None, retry_at,
                 claim.delivery_id, claim.owner, now.isoformat()),
            )
            if cursor.rowcount != 1:
                raise DeliveryBlocked("lost_claim")

    def ensure_owner(self, claim):
        with self.database.connection() as connection:
            row = connection.execute(
                """SELECT 1 FROM deliveries WHERE delivery_id = ? AND claim_owner = ?
                   AND status = 'attempting' AND julianday(lease_until) > julianday(?)""",
                (claim.delivery_id, claim.owner, self.database.timestamp()),
            ).fetchone()
            if row is None:
                raise DeliveryBlocked("lost_claim")
