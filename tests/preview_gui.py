"""Render isolated Light/Dark validation screenshots; never opens a production DB."""

import argparse
from datetime import UTC, datetime
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.settings import AppSettings
from core.services.license_service import LicenseService
from gui.dialogs.issue_license_dialog import IssueLicenseDialog
from gui.windows.main_window import MainWindow
from services.issuance_service import IssuanceService
from services.subscription_service import SubscriptionConfig, SubscriptionService
from test_subscriptions import PNG
from storage.database import Database
from storage.repositories.delivery_repository import DeliveryRepository
from storage.repositories.request_repository import RequestRepository
from test_gui import wait_until


DEVICE_ID = "VC-1234-5678-ABCD-EF12-3456"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/gui-validation"))
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setQuitOnLastWindowClosed(False)
    application.setStyle("Fusion")
    # Windows offscreen Qt has no native font discovery; use installed fonts for QA.
    if not QFontDatabase.families():
        font_root = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "seguisym.ttf"):
            path = font_root / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    if not QFontDatabase.families():
        raise RuntimeError("No fonts available to the Qt preview renderer.")
    with tempfile.TemporaryDirectory(prefix="vcode-gui-preview-") as directory:
        root = Path(directory)
        database = Database(root / "preview.sqlite3", clock=lambda: datetime(2026, 10, 16, 8, 30, tzinfo=UTC))
        database.initialize()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        issuance = IssuanceService(LicenseService(key), database)
        requests, deliveries = RequestRepository(database), DeliveryRepository(database)
        for user, customer, outcome in ((1, "Aurora Diagnostics", "unknown"), (2, "Mekong Auto", None),
                                         (3, "Northstar Calibration", "succeeded"), (4, "Demo Test Lab", "rejected")):
            request = requests.create_or_get_pending(channel="telegram", recipient_id=10000 + user,
                user_id=user, customer=customer, device_id=DEVICE_ID)
            if outcome == "rejected":
                requests.reject(request["request_id"], admin_id=1, reason="Duplicate test request")
            elif outcome:
                record = issuance.approve_request(request["request_id"], days=30, issuer=1)
                claim = deliveries.start(request_id=request["request_id"], license_id=record["license_id"],
                    destination=request["recipient_id"], purpose="customer_license")
                deliveries.finish(claim, status=outcome, error_category="timeout" if outcome == "unknown" else None,
                                  message_id=42 if outcome == "succeeded" else None)
        issuance.issue_manual(device_id=DEVICE_ID, customer="Vehicle Integration", edition="Professional",
                              days=365, issuer="local_admin", source="desktop_manual")
        qr = root / 'synthetic-payment.png'
        qr.write_bytes(PNG)
        subscriptions = SubscriptionService(database, 1, config=SubscriptionConfig(99_000, 12, 30, 'Test Bank', 'Test Owner', '000', qr))
        for user in (11, 12, 13):
            order = subscriptions.checkout(user_id=user, chat_id=10000 + user, customer=f'Demo Order {user}', device_id=DEVICE_ID, months=user - 10, chat_type='private')
            if user == 12:
                subscriptions.report_payment(order['order_id'], user_id=user, chat_id=10000 + user, chat_type='private')
            if user == 13:
                _, nonce = subscriptions.review_payment(order['order_id'], admin_id=1, chat_id=1, chat_type='private')
                subscriptions.confirm_payment(nonce, admin_id=1, chat_id=1, chat_type='private')
        window = MainWindow(database, AppSettings(root / "desktop.ini"))
        window.show()
        wait_until(lambda: window.session.ready and not window.runner.busy)

        def capture(widget, name):
            QApplication.processEvents()
            QTest.qWait(80)
            if not widget.grab().save(str(output / (name + ".png"))):
                raise RuntimeError("Unable to save GUI validation image.")

        for name, index in (("light", 0), ("dark", 1)):
            window.settings_tab.theme_choice.setCurrentIndex(index)
            for tab, tab_name in ((0, "requests"), (1, "licenses"), (2, "deliveries"), (3, "telegram"), (4, "settings"), (5, 'orders')):
                window.tabs.setCurrentIndex(tab)
                if tab < 3:
                    (window.requests, window.licenses, window.deliveries)[tab].table.selectRow(0)
                if tab == 4:
                    settings = window.settings_tab
                    settings.path.setText(str(root / "missing-test-key.pem"))
                    settings.password.setText("temporary-only")
                    settings.unlock.click()
                    wait_until(lambda: not window.runner.busy)
                if tab == 5:
                    window.orders.table.selectRow(0)
                capture(window, f"{name}-{tab_name}")
            dialog = IssueLicenseDialog(window.session, window)
            dialog.show()
            dialog.prepare_review()
            capture(dialog, f"{name}-issue-error")
            dialog.customer.setText("Example Customer")
            dialog.device.setText(DEVICE_ID)
            # Unlock only the temporary test key through the real asynchronous loader.
            path = root / "temporary-preview-key.pem"
            path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.BestAvailableEncryption(b"preview-only-password")))
            window.session.unlock(str(path), "preview-only-password")
            wait_until(lambda: not window.runner.busy)
            dialog.prepare_review()
            capture(dialog, f"{name}-issue-review")
            dialog.reject()
            window.session.lock()
        window.tabs.setCurrentIndex(3)
        window.resize(960, 700)
        capture(window, "dark-telegram-compact")
        window.settings_tab.theme_choice.setCurrentIndex(0)
        capture(window, "light-telegram-compact")
        window.tabs.setCurrentWidget(window.orders)
        capture(window, 'light-orders-compact')
        window.settings_tab.theme_choice.setCurrentIndex(1)
        capture(window, 'dark-orders-compact')
        window.close()
        window.runner.pool.waitForDone()
    print("Saved 20 GUI validation screenshots using temporary data.")


if __name__ == "__main__":
    main()
