"""Optional desktop tests: temporary storage/keys, real Qt workers, no messaging."""

from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QThread, QTimeZone, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLineEdit
    from app.settings import AppSettings
    from gui.dialogs.issue_license_dialog import IssueLicenseDialog
    from gui.formatting import format_local, timezone_label
    from gui.windows.main_window import MainWindow
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False

from cryptography.hazmat.primitives import serialization
from core.services.license_service import LicenseService
from services.issuance_service import IssuanceService
from storage.database import Database
from storage.repositories.delivery_repository import DeliveryRepository
from storage.repositories.license_repository import LicenseRepository
from storage.repositories.request_repository import RequestRepository
from test_activation import DEVICE_ID, make_private_key


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    if not predicate():
        raise AssertionError("Qt operation did not finish in time.")


class DesktopQueryTests(unittest.TestCase):
    def test_repository_filtering_pagination_sorting_and_token_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.sqlite3")
            db.initialize()
            requests, licenses = RequestRepository(db), LicenseRepository(db)
            for user, customer in ((1, "Customer A"), (2, "Customer 100%"), (3, "Customer B")):
                requests.create_or_get_pending(channel="telegram", recipient_id=user, user_id=user,
                                               customer=customer, device_id=DEVICE_ID)
            self.assertEqual(requests.list_page(search="100%").total, 1)
            self.assertEqual(requests.list_page(search="%' OR 1=1 --").total, 0)
            page = requests.list_page(page=1, size=2, sort="customer", descending=False)
            self.assertEqual(page.total, 3)
            self.assertEqual(len(page.records), 1)
            request = requests.list_page(search="Customer A").records[0]
            self.assertEqual(requests.list_page(search=request["request_id"]).total, 1)
            requests.reject(request["request_id"], admin_id=1, reason="Test decline")
            self.assertEqual(requests.list_page(status="rejected").total, 1)
            self.assertEqual(requests.list_page(status="pending").total, 2)
            self.assertEqual(requests.list_page(status="closed").total, 0)
            record = IssuanceService(LicenseService(make_private_key()), db).issue_manual(
                device_id=DEVICE_ID, customer="Customer 100%", edition="Professional", days=30,
                source="desktop_manual", issuer="local_admin",
            )
            for search in ("100%", DEVICE_ID, record["license_id"]):
                result = licenses.list_page(search=search)
                self.assertEqual(result.total, 1)
                self.assertNotIn("activation_key", result.records[0])
                self.assertNotIn("payload_json", result.records[0])
            self.assertNotIn("activation_key", licenses.get_details(record["license_id"]))
            with self.assertRaises(ValueError):
                licenses.list_page(sort="activation_key; DROP TABLE licenses")
            with self.assertRaises(ValueError):
                requests.list_page(status="all' OR 1=1")
            deliveries = DeliveryRepository(db)
            claim = deliveries.start(license_id=record["license_id"], destination="test", purpose="manual_license")
            deliveries.finish(claim, status="unknown", error_category="timeout")
            self.assertEqual(deliveries.list_page(search=record["license_id"]).records[0]["status"], "unknown")


@unittest.skipUnless(GUI_AVAILABLE, "Install requirements-gui.txt to run real Qt desktop tests.")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        cls.application.setStyle("Fusion")
        cls.key = make_private_key()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.db = Database(self.root / "test.sqlite3", clock=lambda: datetime(2026, 10, 16, 12, tzinfo=UTC))
        self.settings = AppSettings(self.root / "desktop.ini")
        self.window = MainWindow(self.db, self.settings)
        self.window.show()
        wait_until(lambda: self.window.session.ready and not self.window.runner.busy)

    def tearDown(self):
        self.window.session.lock()
        for dialog in self.window.findChildren(IssueLicenseDialog):
            dialog.reject()
        self.window.close()
        wait_until(lambda: not self.window.runner.busy)
        self.window.runner.pool.waitForDone()
        QApplication.processEvents()
        self.window.close()
        self.window.deleteLater()
        QApplication.processEvents()
        QApplication.clipboard().clear()
        self.directory.cleanup()

    def unlock(self):
        path = self.root / "temporary-test-key.pem"
        path.write_bytes(self.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"temporary-gui-test"),
        ))
        tab = self.window.settings_tab
        tab.path.setText(str(path))
        tab.password.setText("temporary-gui-test")
        tab.unlock.click()
        self.assertEqual(tab.password.text(), "")
        wait_until(lambda: not self.window.session.unlocking and not self.window.runner.busy)
        self.assertTrue(self.window.session.unlocked)

    def dialog(self):
        self.window.tabs.setCurrentWidget(self.window.licenses)
        self.window.licenses.issue_button.click()
        dialog = self.window.licenses.dialog
        self.assertIsNotNone(dialog)
        dialog.customer.setText("Desktop Customer")
        dialog.device.setText(DEVICE_ID.lower())
        return dialog

    def license_count(self):
        return LicenseRepository(self.db).list_page().total

    def test_read_only_startup_without_secrets_or_telegram_import(self):
        self.assertEqual(self.window.tabs.count(), 5)
        self.assertFalse(self.window.session.unlocked)
        self.assertFalse(self.window.licenses.issue_button.isEnabled())
        self.assertEqual(self.window.requests.records, [])
        result = subprocess.run([sys.executable, "-c",
            "import sys, main; from gui.windows.main_window import MainWindow; assert 'telegram_bot' not in sys.modules; assert 'dotenv' not in sys.modules"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, "GUI import must be independent of bot configuration.")
        with patch("app.signing.load_private_key") as loader:
            self.window.refresh_all()
            wait_until(lambda: not self.window.runner.busy)
            loader.assert_not_called()

    def test_unlock_lock_errors_and_persistent_nonsecret_theme(self):
        self.assertEqual(self.window.settings_tab.password.echoMode(), QLineEdit.EchoMode.Password)
        self.unlock()
        self.assertTrue(self.window.licenses.issue_button.isEnabled())
        self.window.settings_tab.lock.click()
        self.assertFalse(self.window.session.can_issue)
        self.assertFalse(self.window.licenses.issue_button.isEnabled())
        self.window.settings_tab.password.setText("wrong-temporary-password")
        self.window.settings_tab.unlock.click()
        wait_until(lambda: not self.window.runner.busy)
        self.assertIn("Unable to unlock", self.window.settings_tab.error.text())
        self.assertNotIn("wrong-temporary-password", self.window.settings_tab.error.text())
        self.window.settings_tab.theme_choice.setCurrentIndex(1)
        self.assertEqual(AppSettings(self.root / "desktop.ini").theme, "dark")
        stored = (self.root / "desktop.ini").read_text(encoding="utf-8")
        self.assertNotIn("password", stored.lower())
        self.assertNotIn("temporary-gui-test", stored)

    def test_cancel_review_and_confirm_repeated_click_protection(self):
        self.unlock()
        dialog = self.dialog()
        dialog.prepare_review()
        self.assertEqual(self.license_count(), 0)
        dialog.reject()
        self.assertEqual(self.license_count(), 0)
        dialog = self.dialog()
        dialog.prepare_review()
        original = self.window.session._service.issue_manual
        with patch.object(self.window.session._service, "issue_manual", wraps=original) as issue:
            dialog.confirm_issue()
            dialog.confirm_issue()
            wait_until(lambda: not self.window.runner.busy)
            dialog.confirm_issue()
            issue.assert_called_once()
        self.assertEqual(self.license_count(), 1)
        record = dialog._saved
        self.assertEqual(record["source"], "desktop_manual")
        self.assertEqual(record["issuer"], "local_admin")
        self.assertEqual(record["payload"]["device_id"], DEVICE_ID)
        self.assertNotIn(record["activation_key"], dialog.review.toPlainText())
        dialog.copy_key()
        self.assertEqual(QApplication.clipboard().text(), record["activation_key"])
        with self.db.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM deliveries").fetchone()[0], 0)

    def test_worker_queries_selection_refresh_filter_and_explicit_copy_while_locked(self):
        request = RequestRepository(self.db).create_or_get_pending(
            channel="telegram", recipient_id=2, user_id=2, customer="Visible Customer", device_id=DEVICE_ID,
        )
        tab = self.window.requests
        seen_threads = []
        original = tab.repository.list_page

        def query(**kwargs):
            seen_threads.append(QThread.currentThread())
            return original(**kwargs)

        with patch.object(tab.repository, "list_page", side_effect=query):
            tab.refresh()
            wait_until(lambda: not self.window.runner.busy)
        self.assertNotEqual(seen_threads[0], self.application.thread())
        tab.table.selectRow(0)
        tab.refresh()
        wait_until(lambda: not self.window.runner.busy)
        self.assertEqual(tab.selected()["request_id"], request["request_id"])
        tab.search.setText("no-match")
        wait_until(lambda: not tab.timer.isActive() and not self.window.runner.busy)
        self.assertEqual(tab.table.rowCount(), 0)
        record = IssuanceService(LicenseService(self.key), self.db).issue_manual(
            device_id=DEVICE_ID, customer="Visible", edition="Professional", days=1,
            source="desktop_manual", issuer="local_admin",
        )
        self.window.licenses.refresh()
        wait_until(lambda: not self.window.runner.busy)
        self.window.licenses.table.selectRow(0)
        self.window.licenses.copy_key()
        wait_until(lambda: not self.window.runner.busy)
        self.assertFalse(self.window.session.unlocked)
        self.assertEqual(QApplication.clipboard().text(), record["activation_key"])

    def test_storage_failure_does_not_display_or_copy_unrecorded_key(self):
        self.unlock()
        dialog = self.dialog()
        dialog.prepare_review()
        with patch.object(LicenseRepository, "add", side_effect=sqlite3.OperationalError("sensitive-test-sentinel")):
            dialog.confirm_issue()
            wait_until(lambda: not self.window.runner.busy)
        self.assertIsNone(dialog._saved)
        self.assertFalse(dialog.copy.isVisible())
        self.assertEqual(self.license_count(), 0)
        self.assertIn("Storage unavailable", dialog.error.text())
        self.assertNotIn("sensitive-test-sentinel", dialog.error.text())

    def test_local_time_and_lock_during_pending_unlock(self):
        self.assertIn("19:00:00", format_local("2026-10-16T12:00:00Z", QTimeZone(b"Asia/Bangkok")))
        self.assertIn("UTC+07:00", timezone_label(QTimeZone(b"Asia/Bangkok")))
        entered, release = threading.Event(), threading.Event()

        def delayed_load(*_):
            entered.set()
            release.wait(5)
            return self.key

        with patch("app.signing.load_private_key", side_effect=delayed_load):
            self.window.session.unlock("test-only.pem", "test-only")
            try:
                wait_until(entered.is_set)
                self.window.session.lock()
            finally:
                release.set()
            wait_until(lambda: not self.window.runner.busy)
        self.assertFalse(self.window.session.unlocked)

    def test_shutdown_waits_for_confirmed_signing_without_termination(self):
        self.unlock()
        dialog = self.dialog()
        dialog.prepare_review()
        entered, release = threading.Event(), threading.Event()
        original = self.window.session._service.issue_manual

        def delayed_issue(**values):
            entered.set()
            release.wait(5)
            return original(**values)

        with patch.object(self.window.session._service, "issue_manual", side_effect=delayed_issue):
            dialog.confirm_issue()
            try:
                wait_until(entered.is_set)
                self.window.close()
                self.assertTrue(self.window.isVisible())
                self.assertTrue(self.window.runner.busy)
            finally:
                release.set()
            wait_until(lambda: not self.window.runner.busy)
            wait_until(lambda: not self.window.isVisible())
        self.assertEqual(self.license_count(), 1)


if __name__ == "__main__":
    unittest.main()
