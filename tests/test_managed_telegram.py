"""Managed process/IPC tests. Synthetic credentials, temporary keys, no real network."""
import asyncio
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtWidgets import QApplication
from cryptography.hazmat.primitives import serialization
from telegram.error import Conflict, InvalidToken, NetworkError, TimedOut

from app.bot_controller import BotController
from app.credentials import TokenStore
from app.settings import AppSettings
from gui.tabs.telegram_bot_tab import TelegramBotTab
from gui.windows.main_window import MainWindow
from integrations.telegram.config import BotConfig
from integrations.telegram.process_entry import emit, run
from integrations.telegram.application import register_handlers
from integrations.telegram.handlers import workflow
from core.services.license_service import LicenseService
from storage.database import Database
from test_activation import DEVICE_ID, make_private_key
from test_gui import wait_until

TOKEN = "123456:" + "synthetic_test_only_token_abcdefgh"
PASSWORD = "synthetic-secret-password"


class FakeProcess(QObject):
    started = Signal()
    readyReadStandardOutput = Signal()
    readyReadStandardError = Signal()
    finished = Signal(int, QProcess.ExitStatus)
    errorOccurred = Signal(QProcess.ProcessError)

    def __init__(self):
        super().__init__()
        self.starts = 0
        self.frames = []
        self.running = False
        self.output = b""
        self.killed = False

    def setProgram(self, value): self.program = value
    def setArguments(self, value): self.arguments = value
    def setWorkingDirectory(self, value): self.directory = value
    def setProcessEnvironment(self, value): self.environment = value
    def start(self): self.starts += 1
    def state(self): return QProcess.ProcessState.Running if self.running else QProcess.ProcessState.Starting
    def write(self, data): self.frames.append(bytes(data))
    def readAllStandardOutput(self):
        data, self.output = self.output, b""
        return data
    def readAllStandardError(self): return b"discarded raw secret"
    def kill(self): self.killed = True
    def launch(self):
        self.running = True
        self.started.emit()
    def finish(self):
        self.running = False
        self.finished.emit(0, QProcess.ExitStatus.NormalExit)


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = Database(self.root / "test.sqlite3")
        self.process = FakeProcess()
        self.controller = BotController(self.database, process=self.process)
        self.logs = []
        self.controller.activity.connect(self.logs.append)

    def tearDown(self):
        if self.controller.active:
            self.controller.disconnect_bot()
            self.process.finish()
        self.temp.cleanup()

    def connect(self):
        return self.controller.connect_bot(token=TOKEN, admin_id=1,
                    key_path=str(self.root / "test.pem"), password=PASSWORD)

    def event(self, kind, **values):
        self.controller.accept_event(dict(version=1, event=kind, **values))

    def test_startup_snapshot_duplicate_guard_same_db_and_secret_free_argv(self):
        self.assertTrue(self.connect())
        self.assertFalse(self.connect())
        self.assertEqual(self.process.starts, 1)
        self.assertEqual(self.process.program, sys.executable)
        self.assertNotIn(TOKEN, repr(self.process.arguments))
        self.assertNotIn(PASSWORD, repr(self.process.arguments))
        self.process.launch()
        message = json.loads(self.process.frames[0])
        self.assertEqual(message["config"]["db_path"], str(self.database.path.resolve()))
        self.assertEqual(message["config"]["password"], PASSWORD)
        self.assertIsNone(self.controller._config)
        self.event("identity", name="Test", username="test_bot")
        self.event("state", status="Connected")
        self.assertEqual(self.controller.state, "Connected")
        self.assertEqual(self.controller.identity, "Test @test_bot")
        self.event("state", status="Reconnecting")
        self.assertEqual(self.controller.state, "Reconnecting")
        self.event("error", code="conflict", exception=TOKEN)
        self.assertEqual(self.controller.state, "Error")
        self.event("state", status="Connected")
        self.assertEqual(self.controller.state, "Error")
        self.assertNotIn(TOKEN, str(self.logs))

    def test_graceful_stop_before_process_started_and_unexpected_exit(self):
        self.connect()
        self.controller.disconnect_bot()
        self.assertEqual(self.process.frames, [])
        self.process.launch()
        self.assertEqual([json.loads(v)["command"] for v in self.process.frames], ["start", "stop"])
        self.assertFalse(self.process.killed)
        self.process.finish()
        self.assertEqual(self.controller.state, "Disconnected")
        self.connect()
        self.process.launch()
        self.process.finish()
        self.assertEqual(self.controller.state, "Error")
        self.assertIn("unexpectedly", str(self.logs))

    def test_start_failure_partial_invalid_ipc_and_bounded_activity(self):
        self.connect()
        self.process.output = b'bad frame\n{"version":1,"event":"state",'
        self.process.readyReadStandardOutput.emit()
        self.process.output = b'"status":"Connected"}\n'
        self.process.readyReadStandardOutput.emit()
        self.assertEqual(self.controller.state, "Connected")
        self.process.errorOccurred.emit(QProcess.ProcessError.FailedToStart)
        self.assertFalse(self.controller.active)
        self.assertEqual(self.controller.state, "Error")
        store = Mock(available=False)
        tab = TelegramBotTab(self.database, AppSettings(self.root / "desktop.ini"), self.controller, token_store=store)
        for i in range(150): tab.add_activity("Safe event")
        self.assertEqual(tab.activity.count(), 100)
        tab.deleteLater()

    def test_secure_store_failure_session_only_and_running_fields_disabled(self):
        store = TokenStore()
        store.backend = Mock()
        store.backend.set_password.side_effect = RuntimeError(TOKEN)
        tab = TelegramBotTab(self.database, AppSettings(self.root / "desktop.ini"), self.controller, token_store=store)
        tab.token.setText(TOKEN)
        tab.admin.setText("1")
        tab.path.setText(str(self.root / "key.pem"))
        tab.password.setText(PASSWORD)
        tab.remember.setChecked(True)
        tab.connect_bot()
        self.assertFalse(tab.remember.isEnabled())
        self.assertEqual(tab.password.text(), "")
        self.assertFalse(tab.config.isEnabled())
        prefs = (self.root / "desktop.ini").read_text()
        self.assertNotIn(TOKEN, prefs)
        self.assertNotIn(PASSWORD, prefs)
        self.assertIn("session-only", tab.error.text())
        self.controller.disconnect_bot()
        self.process.finish()
        self.assertTrue(tab.config.isEnabled())
        tab.deleteLater()

    def test_window_close_waits_for_managed_child_without_kill(self):
        with patch("gui.windows.main_window.BotController", return_value=self.controller):
            window = MainWindow(self.database, AppSettings(self.root / "window.ini"))
        window.show()
        wait_until(lambda: window.session.ready and not window.runner.busy)
        self.connect()
        self.process.launch()
        window.close()
        self.assertTrue(window.isVisible())
        self.assertEqual(self.controller.state, "Disconnecting")
        self.assertFalse(self.process.killed)
        self.process.finish()
        wait_until(lambda: not window.isVisible())
        window.runner.pool.waitForDone()
        window.deleteLater()

    def test_stalled_shutdown_requires_explicit_force_confirmation(self):
        from PySide6.QtWidgets import QMessageBox
        tab = TelegramBotTab(self.database, AppSettings(self.root / "desktop.ini"),
                             self.controller, token_store=Mock(available=False))
        self.connect()
        self.process.launch()
        self.controller.disconnect_bot()
        self.controller.stop_timer.timeout.emit()
        self.assertIn("unknown", tab.error.text())
        self.assertFalse(tab.force_button.isHidden())
        self.assertFalse(self.process.killed)
        with patch("gui.tabs.telegram_bot_tab.QMessageBox.warning", return_value=QMessageBox.StandardButton.No):
            tab.force_stop()
        self.assertFalse(self.process.killed)
        with patch("gui.tabs.telegram_bot_tab.QMessageBox.warning", return_value=QMessageBox.StandardButton.Yes):
            tab.force_stop()
        self.assertTrue(self.process.killed)
        tab.deleteLater()

    def test_secure_store_opt_in_and_read_failure_have_no_plaintext_fallback(self):
        store = TokenStore()
        backend = store.backend = Mock()
        self.assertTrue(store.save(TOKEN, True))
        backend.set_password.assert_called_once_with(store.service, store.account, TOKEN)
        backend.get_password.return_value = TOKEN
        self.assertTrue(store.save("", False))
        backend.delete_password.assert_called_once_with(store.service, store.account)
        backend.get_password.side_effect = RuntimeError(PASSWORD)
        self.assertEqual(store.read(), "")
        self.assertFalse(store.available)

    def test_real_child_startup_failure_uses_stdin_and_no_network(self):
        # Missing temporary key stops before building Telegram or contacting its API.
        controller = BotController(self.database)
        logs = []
        controller.activity.connect(logs.append)
        try:
            controller.connect_bot(token=TOKEN, admin_id=1, key_path=str(self.root / "missing.pem"), password=PASSWORD)
            wait_until(lambda: not controller.active)
            self.assertEqual(controller.state, "Error")
            self.assertIn("Startup failed", str(logs))
            self.assertNotIn(TOKEN, str(logs))
            self.assertNotIn(PASSWORD, str(logs))
        finally:
            if controller.active:
                controller.force_stop()
                wait_until(lambda: not controller.active)


class ChildTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)
        cls.key = make_private_key()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        path = self.root / "temporary.pem"
        path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(PASSWORD.encode())))
        self.config = BotConfig(TOKEN, 1, str(path), PASSWORD, str(self.root / "test.sqlite3"))
        self.events = []
        self.stop = asyncio.Event()
        self.app = SimpleNamespace(bot_data={}, bot=SimpleNamespace(first_name="Test", username="test_bot"),
            initialize=AsyncMock(), start=AsyncMock(), stop=AsyncMock(), shutdown=AsyncMock(),
            updater=SimpleNamespace(start_polling=AsyncMock(), stop=AsyncMock()), add_handler=Mock())

    def tearDown(self): self.temp.cleanup()

    def output(self, event, **values):
        self.events.append(dict(event=event, **values))
        if event == "state" and values.get("status") == "Connected":
            self.stop.set()

    async def execute(self):
        self.builder = Mock(return_value=self.app)
        await run(self.config, self.stop, builder=self.builder, output=self.output)

    async def test_connected_only_after_start_and_graceful_shutdown_same_db(self):
        async def polling(**kwargs):
            self.assertFalse(any(e.get("status") == "Connected" for e in self.events))
            self.app.start.assert_awaited_once()
        self.app.updater.start_polling.side_effect = polling
        await self.execute()
        self.assertEqual(self.builder.call_args.args[3].path, Path(self.config.db_path))
        self.app.updater.stop.assert_awaited_once()
        self.app.stop.assert_awaited_once()
        self.app.shutdown.assert_awaited_once()
        self.assertTrue(any(e.get("status") == "Connected" for e in self.events))
        self.assertNotIn(TOKEN, str(self.events))
        self.assertNotIn(PASSWORD, repr(self.config))

    async def test_conflict_is_fatal_without_reconnect_or_connected(self):
        async def polling(**kwargs): kwargs["error_callback"](Conflict(TOKEN))
        self.app.updater.start_polling.side_effect = polling
        await self.execute()
        self.assertIn(dict(event="error", code="conflict"), self.events)
        self.assertFalse(any(e.get("status") == "Connected" for e in self.events))
        self.app.updater.start_polling.assert_awaited_once()
        self.assertNotIn(TOKEN, str(self.events))

    async def test_dns_reconnecting_and_stop_during_network_backoff(self):
        self.app.initialize.side_effect = NetworkError(TOKEN)
        def output(event, **values):
            self.events.append(dict(event=event, **values))
            self.stop.set()
        await run(self.config, self.stop, builder=Mock(return_value=self.app), output=output)
        self.assertIn(dict(event="state", status="Reconnecting", code="network"), self.events)
        self.app.start.assert_not_awaited()
        self.assertNotIn(TOKEN, str(self.events))

    async def test_invalid_credentials_and_key_never_report_connected(self):
        self.app.initialize.side_effect = InvalidToken(TOKEN)
        await self.execute()
        self.assertEqual(self.events, [dict(event="error", code="credentials")])
        bad = BotConfig(TOKEN, 1, str(self.root / "missing.pem"), PASSWORD, self.config.db_path)
        with self.assertRaises(ValueError):
            await run(bad, self.stop, builder=Mock(), output=self.output)

    async def test_polling_bootstrap_network_failure_is_reconnecting_and_cancellable(self):
        self.app.updater.start_polling.side_effect = NetworkError(TOKEN)
        def output(event, **values):
            self.events.append(dict(event=event, **values))
            if values.get("status") == "Reconnecting":
                self.stop.set()
        await run(self.config, self.stop, builder=Mock(return_value=self.app), output=output)
        self.assertEqual(self.events, [dict(event="state", status="Reconnecting", code="network")])
        self.app.stop.assert_awaited_once()
        self.app.shutdown.assert_awaited_once()

    def test_protocol_validation_sanitized_output_and_registration(self):
        frame = dict(version=1, command="start", config=dict(token=TOKEN, admin_id=1,
            key_path=self.config.key_path, password=PASSWORD, db_path=self.config.db_path))
        self.assertEqual(BotConfig.parse(frame), self.config)
        frame["config"]["admin_id"] = 0
        with self.assertRaises(ValueError): BotConfig.parse(frame)
        stream = StringIO()
        with redirect_stdout(stream): emit("records", status="succeeded", token=TOKEN, password=PASSWORD, exception=TOKEN)
        self.assertNotIn(TOKEN, stream.getvalue())
        self.assertNotIn(PASSWORD, stream.getvalue())
        app = Mock()
        stream = StringIO()
        with redirect_stdout(stream): register_handlers(app)
        self.assertEqual(stream.getvalue(), "")
        commands = {next(iter(c.args[0].commands)) for c in app.add_handler.call_args_list if hasattr(c.args[0], "commands")}
        self.assertEqual(commands, {"start", "menu", "myid", "generate", "requests", "retry", "cancel"})

    async def test_request_confirmation_delivery_retry_events_and_gui_refresh(self):
        # Existing workflow end-to-end, through its managed-event boundary.
        db = Database(self.config.db_path)
        data = {"admin_id": 1}
        workflow.configure_storage(data, LicenseService(self.key), db)
        controller_process = FakeProcess()
        controller = BotController(db, process=controller_process)
        controller.connect_bot(token=TOKEN, admin_id=1, key_path=self.config.key_path, password=PASSWORD)
        controller_process.launch()
        window = MainWindow(db, AppSettings(self.root / "gui.ini"))
        window.show()
        wait_until(lambda: window.session.ready and not window.runner.busy)
        def events(**values):
            self.events.append(values)
            window.bot.records_changed.emit()
        data["desktop_event"] = events
        context = SimpleNamespace(application=SimpleNamespace(bot_data=data), user_data={},
            bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42))))
        message = SimpleNamespace(text=DEVICE_ID, reply_text=AsyncMock())
        update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=22, full_name="Test", username=None),
            effective_chat=SimpleNamespace(id=222, type="private"), callback_query=None)
        try:
            await workflow.receive_form(update, context)
            request = data["request_repository"].list_page().records[0]
            update.effective_user = SimpleNamespace(id=1)
            update.effective_chat.id = 1
            async def review_confirm():
                before = data["license_repository"].for_request(request["request_id"])
                update.callback_query = SimpleNamespace(data="request:" + request["request_id"], from_user=update.effective_user, answer=AsyncMock())
                await workflow.select_request(update, context)
                message.text = "30"
                await workflow.receive_form(update, context)
                self.assertEqual(data["license_repository"].for_request(request["request_id"]), before)
                update.callback_query.data = "decision:confirm:" + context.user_data["key_form"]["form_id"]
                await workflow.approval_decision(update, context)
            async def send(**kwargs):
                if kwargs["chat_id"] == 222:
                    raise TimedOut(TOKEN)
                return SimpleNamespace(message_id=42)
            context.bot.send_message.side_effect = send
            await review_confirm()
            original = data["license_repository"].for_request(request["request_id"])
            self.assertEqual(context.bot.send_message.await_args.kwargs["chat_id"], 222)
            context.bot.send_message.side_effect = None
            await review_confirm()
            saved = data["license_repository"].for_request(request["request_id"])
            self.assertEqual(saved, original)
            self.assertIn(saved["activation_key"], context.bot.send_message.await_args.kwargs["text"])
            wait_until(lambda: len(window.licenses.records) == 1 and not window.runner.busy)
            self.assertEqual(window.licenses.records[0]["license_id"], saved["license_id"])
            self.assertEqual([e["status"] for e in self.events if e.get("license_id")], ["issued", "unknown", "issued", "succeeded"])
            self.assertNotIn(TOKEN, str(self.events))
            self.assertNotIn(saved["activation_key"], str(self.events))
        finally:
            controller.disconnect_bot()
            controller_process.finish()
            window.close()
            wait_until(lambda: not window.runner.busy)
            window.runner.pool.waitForDone()
            window.deleteLater()


if __name__ == "__main__":
    QApplication.instance() or QApplication([])
    unittest.main()
