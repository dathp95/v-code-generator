"""Persist every key before delivery; serialize request approvals in SQLite."""

from datetime import UTC, datetime

from core.services.license_service import LicenseService
from storage.database import Database
from storage.repositories.license_repository import LicenseRepository
from storage.repositories.request_repository import RequestRepository


class RequestUnavailable(ValueError):
    """The request cannot be issued or retried."""


def require_unexpired(license_record, now=None):
    if datetime.fromisoformat(license_record["expires_at"]) <= (now or datetime.now(UTC)):
        raise RequestUnavailable("Key đã hết hạn. Hãy nhờ khách gửi Device ID để tạo yêu cầu mới.")


class IssuanceService:
    def __init__(self, license_service: LicenseService, database: Database):
        self.license_service = license_service
        self.database = database
        self.requests = RequestRepository(database)
        self.licenses = LicenseRepository(database)

    def issue_manual(self, *, device_id, customer, edition, days, issuer, source, issued_at=None):
        with self.database.transaction() as connection:
            time_override = {"issued_at": issued_at or self.database.now()}
            issued = self.license_service.issue_activation(
                device_id=device_id, customer=customer, edition=edition, days=days, **time_override,
            )
            return self.licenses.add(issued, days=days, issuer=issuer, source=source, connection=connection)

    def approve_request(self, request_id, *, days, issuer, connection=None):
        # No awaits here. BEGIN IMMEDIATE protects check + issue + insert across processes.
        if connection is None:
            with self.database.transaction() as connection:
                return self.approve_request(request_id, days=days, issuer=issuer, connection=connection)
        request = self.requests.get(request_id, connection=connection)
        if request is None or request["status"] == "closed":
            raise RequestUnavailable("Yêu cầu không còn hợp lệ. Hãy nhờ khách gửi lại Device ID.")
        self.license_service.validate_inputs(
            request["device_id"], request["customer"], "Professional", days,
        )
        existing = self.licenses.for_request(request_id, connection=connection)
        if existing is not None:
            require_unexpired(existing, self.database.now())
            if days != existing["duration_days"]:
                raise RequestUnavailable(
                    f"Key đã tạo với {existing['duration_days']} ngày. Nhập đúng số ngày để thử gửi lại."
                )
            return existing
        issued = self.license_service.issue_activation(
            device_id=request["device_id"], customer=request["customer"],
            edition="Professional", days=days, issued_at=self.database.now(),
        )
        record = self.licenses.add(
            issued, days=days, issuer=issuer, source="telegram_request",
            request_id=request_id, connection=connection,
        )
        self.requests.approve(request_id, connection=connection)
        return record
