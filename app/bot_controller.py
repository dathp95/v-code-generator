"""Qt child-process lifecycle. Secrets travel only in the initial stdin frame."""
import json
import sys

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
from storage.database import REPOSITORY_ROOT

MESSAGES = {
    "startup": "Startup failed. Check configuration, encrypted key and dependencies.",
    "credentials": "Telegram rejected the bot token.",
    "conflict": "Another process is polling this bot. Stop it before connecting again.",
    "network": "Telegram network unavailable; waiting for connectivity.",
    "telegram": "Telegram polling stopped. Check configuration before reconnecting.",
}


class BotController(QObject):
    changed = Signal()
    activity = Signal(str)
    records_changed = Signal()
    stopped = Signal()
    stalled = Signal()

    def __init__(self, database, parent=None, process=None):
        super().__init__(parent)
        self.database = database
        self.process = process or QProcess(self)
        self.state = "Disconnected"
        self.identity = ""
        self._config = None
        self._buffer = b""
        self._fatal = False
        self._active = False
        self.process.started.connect(self._started)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(lambda: self.process.readAllStandardError())
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.stop_timer = QTimer(self)
        self.stop_timer.setSingleShot(True)
        self.stop_timer.setInterval(15000)
        self.stop_timer.timeout.connect(self.stalled.emit)

    @property
    def active(self):
        return self._active

    def _state(self, state):
        self.state = state
        self.changed.emit()

    def connect_bot(self, *, token, admin_id, key_path, password):
        if self.active:
            return False
        # Freeze the startup snapshot; fields cannot mutate a running child.
        self._config = dict(token=token, admin_id=admin_id, key_path=key_path,
                            password=password, db_path=str(self.database.path.resolve()))
        self._buffer = b""
        self._fatal = False
        self.identity = ""
        self._active = True
        self._state("Connecting")
        self.process.setProgram(sys.executable)
        self.process.setArguments(["-u", "-m", "integrations.telegram.process_entry"])
        self.process.setWorkingDirectory(str(REPOSITORY_ROOT))
        environment = QProcessEnvironment.systemEnvironment()
        for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_ID", "VC_KEY_PASSWORD", "VC_PRIVATE_KEY"):
            environment.remove(name)
        self.process.setProcessEnvironment(environment)
        self.process.start()
        return True

    def _started(self):
        if self._config is not None:
            message = dict(version=1, command="start", config=self._config)
            self.process.write((json.dumps(message) + "\n").encode("utf-8"))
            self._config = None
        if self.state == "Disconnecting":
            self._send_stop()

    def disconnect_bot(self):
        if not self.active or self.state == "Disconnecting":
            return
        self._state("Disconnecting")
        self._send_stop()
        self.stop_timer.start()

    def _send_stop(self):
        if self._config is None and self.process.state() == QProcess.ProcessState.Running:
            self.process.write(b'{"version":1,"command":"stop"}\n')

    def force_stop(self):
        if self.active:
            self.process.kill()

    def _read(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        if len(self._buffer) > 65536:
            self._buffer = b""
            return
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                self.accept_event(json.loads(line))
            except (ValueError, TypeError, AttributeError):
                pass

    def accept_event(self, event):
        if not self.active or event.get("version") != 1:
            return
        kind = event.get("event")
        if kind == "error":
            self._fatal = True
            self.activity.emit(MESSAGES.get(event.get("code"), MESSAGES["startup"]))
            self._state("Error")
        elif kind == "state" and not self._fatal and self.state != "Disconnecting":
            state = event.get("status")
            if state in ("Connected", "Reconnecting"):
                self._state(state)
                self.activity.emit(state if state == "Connected" else MESSAGES["network"])
                self.records_changed.emit()
        elif kind == "identity":
            # Identity is displayed as plain text, bounded; never emit the frame itself.
            self.identity = str(event.get("name", ""))[:80] + " @" + str(event.get("username", ""))[:64]
            self.changed.emit()
        elif kind == "records":
            self.records_changed.emit()
            self.activity.emit("Telegram workflow updated. Records refreshed from SQLite.")

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._fatal = True
            self.activity.emit(MESSAGES["startup"])
            self._finished()

    def _finished(self, *args):
        expected = self.state == "Disconnecting"
        self.stop_timer.stop()
        self._config = None
        self._active = False
        if not expected and not self._fatal:
            self.activity.emit("Bot process exited unexpectedly. Review delivery outcomes before retrying.")
        self._state("Disconnected" if expected else "Error")
        self.records_changed.emit()
        self.stopped.emit()
