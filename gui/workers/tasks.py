"""Bounded Qt workers with queued UI results and secret-safe error messages."""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal

from core.exceptions import ActivationValidationError, PrivateKeyLoadError
from storage.database import StorageError
from services.customer_service import CustomerValidationError
from services.revenue_service import RevenueError


def safe_error(error):
    if isinstance(error, (CustomerValidationError, RevenueError)):
        return str(error)
    if isinstance(error, StorageError):
        return "Storage unavailable. Check the database path and permissions, then Refresh all. No unrecorded key is displayed."
    if isinstance(error, PrivateKeyLoadError):
        return "Unable to unlock signing. Check the encrypted RSA key file and password."
    if isinstance(error, ActivationValidationError):
        return "Check customer, Device ID, edition, and duration (1–3650 days)."
    return "The operation could not be completed. Check local configuration and retry."


class TaskSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Task(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = TaskSignals()

    def run(self):
        try:
            result = self.function()
            self.signals.result.emit(result)
        except Exception as error:
            self.signals.error.emit(safe_error(error))
        finally:
            self.function = None
            self.signals.finished.emit()


class TaskRunner(QObject):
    idle = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.tasks = set()
        self.accepting = True

    @property
    def busy(self):
        return bool(self.tasks)

    def run(self, function, success, failure):
        if not self.accepting:
            return False
        task = Task(function)
        self.tasks.add(task)
        task.signals.result.connect(success, Qt.ConnectionType.QueuedConnection)
        task.signals.error.connect(failure, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(lambda: self._finished(task), Qt.ConnectionType.QueuedConnection)
        self.pool.start(task)
        return True

    def _finished(self, task):
        self.tasks.discard(task)
        if not self.tasks:
            self.idle.emit()
