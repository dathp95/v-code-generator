"""Request identity and approval state, independent of message delivery."""

from datetime import datetime
import json
from uuid import uuid4

from core.crypto.activation import validate_device_id, validate_non_empty
from storage.database import Database
from storage.repositories.pagination import query_page, search_pattern


class RequestRepository:
    def __init__(self, database: Database):
        self.database = database

    def get(self, request_id, *, connection=None):
        if connection is None:
            with self.database.connection() as connection:
                return self.get(request_id, connection=connection)
        row = connection.execute("SELECT * FROM requests WHERE request_id = ?", (request_id,)).fetchone()
        return dict(row) if row else None

    def list_page(self, *, search="", status="all", page=0, size=25, sort="created_at", descending=True):
        if status not in ("all", "pending", "approved", "closed", "rejected"):
            raise ValueError("Unsupported request status.")
        select = """SELECT r.*,
            CASE WHEN r.rejected_by IS NOT NULL THEN 'rejected' ELSE r.status END AS request_status,
            (SELECT d.status FROM deliveries d WHERE d.request_id = r.request_id
             AND d.purpose = 'customer_license' ORDER BY d.delivery_id DESC LIMIT 1) AS delivery_status
            FROM requests r"""
        where = "(r.customer LIKE ? ESCAPE '\\' OR r.device_id LIKE ? ESCAPE '\\' OR r.request_id LIKE ? ESCAPE '\\')"
        pattern = search_pattern(search)
        parameters = [pattern, pattern, pattern]
        if status != "all":
            where += " AND (CASE WHEN r.rejected_by IS NOT NULL THEN 'rejected' ELSE r.status END) = ?"
            parameters.append(status)
        sorts = {name: "r." + name for name in ("request_id", "customer", "channel", "device_id", "created_at")}
        sorts.update(request_status="request_status", delivery_status="delivery_status")
        with self.database.connection() as connection:
            return query_page(connection, select, where, parameters, sorts=sorts, sort=sort,
                              descending=descending, page=page, size=size)

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
            now = self.database.timestamp()
            if row is not None:
                if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= self.database.now():
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
            (self.database.timestamp(), request_id),
        )

    def reject(self, request_id, *, admin_id, reason, connection=None):
        if connection is None:
            with self.database.transaction() as connection:
                return self.reject(request_id, admin_id=admin_id, reason=reason, connection=connection)
        reason = validate_non_empty("reason", reason)
        if len(reason) > 300:
            raise ValueError("Lý do tối đa 300 ký tự.")
        cursor = connection.execute(
            """UPDATE requests SET status = 'closed', rejected_by = ?, rejected_at = ?,
               rejection_reason = ?, updated_at = ? WHERE request_id = ? AND status = 'pending'
               AND NOT EXISTS (SELECT 1 FROM licenses WHERE licenses.request_id = requests.request_id)""",
            (str(admin_id), self.database.timestamp(), reason, self.database.timestamp(), request_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Chỉ từ chối yêu cầu đang chờ, chưa cấp key.")

    def list_actionable(self, *, page=0, page_size=8, admin_id=None):
        page, page_size = max(0, min(int(page), 10000)), max(1, min(int(page_size), 10))
        with self.database.connection() as connection:
            rows = connection.execute(
                """SELECT r.request_id, r.customer, r.device_id, r.created_at, r.status, l.license_id,
                   (SELECT d.status FROM deliveries d WHERE d.request_id = r.request_id
                    ORDER BY d.delivery_id DESC LIMIT 1) AS delivery_status, NULL AS payload_json
                   FROM requests r LEFT JOIN licenses l ON l.request_id = r.request_id
                   WHERE r.channel = 'telegram' AND r.status IN ('pending', 'approved')
                   AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.request_id = r.request_id
                       AND d.purpose = 'customer_license' AND d.status = 'succeeded')
                   UNION ALL
                   SELECT NULL, NULL, NULL, l.issued_at, 'approved', l.license_id,
                   (SELECT d.status FROM deliveries d WHERE d.license_id = l.license_id
                    AND d.purpose = 'manual_license' ORDER BY d.delivery_id DESC LIMIT 1), l.payload_json
                   FROM licenses l WHERE l.request_id IS NULL AND l.source = 'telegram_manual' AND l.issuer = ?
                   AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.license_id = l.license_id
                       AND d.purpose = 'manual_license' AND d.status = 'succeeded')
                   ORDER BY 4, 1, 6 LIMIT ? OFFSET ?""",
                (str(admin_id) if admin_id is not None else "", page_size + 1, page * page_size),
            ).fetchall()
        records = [dict(row) for row in rows[:page_size]]
        for record in records:
            if record["payload_json"]:
                payload = json.loads(record.pop("payload_json"))
                record["customer"], record["device_id"] = payload["customer"], payload["device_id"]
        return records, len(rows) > page_size
