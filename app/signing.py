"""Explicit, in-memory signing session; queued work survives a lock or shutdown."""

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from core.crypto.key_loader import load_private_key
from core.services.license_service import LicenseService
from services.issuance_service import IssuanceService


class SigningSession(QObject):
    changed = Signal()
    error = Signal(str)

    def __init__(self, database, runner, parent=None):
        super().__init__(parent)
        self.database, self.runner = database, runner
        self._service = None
        self._generation = 0
        self.unlocking = False
        self.issuing = False
        self.ready = False

    @property
    def unlocked(self):
        return self._service is not None

    @property
    def can_issue(self):
        return self.ready and self.unlocked and not self.unlocking and not self.issuing

    def unlock(self, path, password):
        if self.unlocking or self.issuing:
            return False
        self.lock()
        generation = self._generation
        self.unlocking = True
        self.changed.emit()

        def success(key):
            self.unlocking = False
            if generation == self._generation:
                self._service = IssuanceService(LicenseService(key), self.database)
            self.changed.emit()

        def failure(message):
            self.unlocking = False
            if generation == self._generation:
                self.error.emit(message)
            self.changed.emit()

        if not self.runner.run(lambda: load_private_key(Path(path), password.encode("utf-8")), success, failure):
            self.unlocking = False
            self.changed.emit()
            return False
        return True

    def lock(self):
        self._generation += 1
        self._service = None
        self.changed.emit()

    def issue(self, values, success, failure):
        if not self.can_issue:
            return False
        service = self._service
        self.issuing = True
        self.changed.emit()

        def done(record):
            self.issuing = False
            self.changed.emit()
            success(record)

        def failed(message):
            self.issuing = False
            self.changed.emit()
            failure(message)

        submitted = self.runner.run(
            lambda: service.issue_manual(**values, source="desktop_manual", issuer="local_admin"), done, failed,
        )
        if not submitted:
            self.issuing = False
            self.changed.emit()
        return submitted
