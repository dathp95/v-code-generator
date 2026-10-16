"""Expiring, single-use server-side approval forms with snapshot rechecks."""

from datetime import datetime, timedelta
from uuid import uuid4

from core.services.license_service import LicenseService
from services.issuance_service import RequestUnavailable, require_unexpired
from storage.repositories.delivery_repository import DeliveryRepository, delivery_version


FORM_SECONDS = 600
STALE_FORM = "Xác nhận đã cũ/hết hạn. Chọn lại yêu cầu trong /requests."


class ApprovalService:
    def __init__(self, issuance):
        self.issuance = issuance
        self.database = issuance.database
        self.deliveries = DeliveryRepository(self.database)

    def _active(self, connection, form_id, admin_id, chat_id, action=None):
        row = connection.execute(
            """SELECT * FROM approval_forms WHERE form_id = ? AND admin_id = ?
               AND chat_id = ? AND consumed_at IS NULL""",
            (form_id, str(admin_id), str(chat_id)),
        ).fetchone()
        if (row is None or datetime.fromisoformat(row["expires_at"]) <= self.database.now()
                or (action is not None and row["action"] != action)):
            raise RequestUnavailable(STALE_FORM)
        return dict(row)

    def invalidate(self, admin_id, chat_id, *, connection=None):
        if connection is None:
            with self.database.transaction() as connection:
                return self.invalidate(admin_id, chat_id, connection=connection)
        connection.execute(
            "UPDATE approval_forms SET consumed_at = ? WHERE admin_id = ? AND chat_id = ? AND consumed_at IS NULL",
            (self.database.timestamp(), str(admin_id), str(chat_id)),
        )

    def _snapshot(self, connection, request_id):
        request = self.issuance.requests.get(request_id, connection=connection)
        if request is None or request["status"] == "closed":
            raise RequestUnavailable("Yêu cầu không còn hợp lệ. Hãy nhờ khách gửi lại Device ID.")
        record = self.issuance.licenses.for_request(request_id, connection=connection)
        if record:
            require_unexpired(record, self.database.now())
        delivery = self.deliveries.latest(
            request_id=request_id, license_id=record["license_id"] if record else None,
            purpose="customer_license", connection=connection,
        )
        version = delivery_version(delivery)
        return request, record, delivery, version

    def select(self, request_id, *, admin_id, chat_id):
        self.deliveries.recover_stale()
        with self.database.transaction() as connection:
            request, record, delivery, version = self._snapshot(connection, request_id)
            self.invalidate(admin_id, chat_id, connection=connection)
            form_id = uuid4().hex
            connection.execute(
                """INSERT INTO approval_forms (form_id, admin_id, chat_id, request_id, action, expires_at)
                   VALUES (?, ?, ?, ?, 'selected', ?)""",
                (form_id, str(admin_id), str(chat_id), request_id,
                 (self.database.now() + timedelta(seconds=FORM_SECONDS)).isoformat()),
            )
            return form_id

    def prepare(self, selection_id, *, admin_id, chat_id, days=None, reason=None):
        self.deliveries.recover_stale()
        with self.database.transaction() as connection:
            selected = self._active(connection, selection_id, admin_id, chat_id, "selected")
            request, record, delivery, version = self._snapshot(connection, selected["request_id"])
            action = "reject" if reason is not None else "approve"
            if action == "reject":
                if record or request["status"] != "pending":
                    raise RequestUnavailable("Chỉ từ chối yêu cầu chưa cấp key.")
                if not reason.strip() or len(reason) > 300:
                    raise RequestUnavailable("Nhập lý do từ 1 đến 300 ký tự; không nhập bí mật.")
            else:
                LicenseService.validate_inputs(request["device_id"], request["customer"], "Professional", days)
                if record and days != record["duration_days"]:
                    raise RequestUnavailable(f"Key đã tạo với {record['duration_days']} ngày. Nhập đúng số ngày đó.")
            self.invalidate(admin_id, chat_id, connection=connection)
            form_id = uuid4().hex
            connection.execute(
                """INSERT INTO approval_forms (form_id, admin_id, chat_id, request_id, action, days,
                   reason, request_version, license_id, delivery_version, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (form_id, str(admin_id), str(chat_id), request["request_id"], action, days, reason,
                 request["updated_at"], record["license_id"] if record else None, version,
                 (self.database.now() + timedelta(seconds=FORM_SECONDS)).isoformat()),
            )
            return dict(form_id=form_id, request=request, license=record, delivery=delivery, action=action)

    def selected_request(self, selection_id, *, admin_id, chat_id):
        with self.database.connection() as connection:
            form = self._active(connection, selection_id, admin_id, chat_id, "selected")
            request, record, delivery, version = self._snapshot(connection, form["request_id"])
            if record or request["status"] != "pending":
                raise RequestUnavailable("Chỉ từ chối yêu cầu chưa cấp key.")
            return request

    def cancel(self, form_id, *, admin_id, chat_id):
        with self.database.transaction() as connection:
            self._active(connection, form_id, admin_id, chat_id)
            self.invalidate(admin_id, chat_id, connection=connection)

    def confirm(self, form_id, *, admin_id, chat_id):
        self.deliveries.recover_stale()
        with self.database.transaction() as connection:
            form = self._active(connection, form_id, admin_id, chat_id)
            if form["action"] == "selected":
                raise RequestUnavailable(STALE_FORM)
            request, record, delivery, version = self._snapshot(connection, form["request_id"])
            if (request["updated_at"] != form["request_version"]
                    or (record["license_id"] if record else None) != form["license_id"]
                    or version != form["delivery_version"]):
                raise RequestUnavailable(STALE_FORM)
            connection.execute("UPDATE approval_forms SET consumed_at = ? WHERE form_id = ?",
                               (self.database.timestamp(), form_id))
            if form["action"] == "reject":
                self.issuance.requests.reject(request["request_id"], admin_id=admin_id,
                                              reason=form["reason"], connection=connection)
                return request, None
            record = self.issuance.approve_request(
                request["request_id"], days=form["days"], issuer=admin_id, connection=connection,
            )
            # Carry the reviewed delivery state through to the atomic network claim.
            record["_delivery_version"] = form["delivery_version"]
            return request, record
