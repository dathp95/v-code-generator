"""Local desktop shell. Importing this module never imports or starts Telegram."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from app.settings import AppSettings
from app.signing import SigningSession
from app.bot_controller import BotController
from gui.tabs.telegram_bot_tab import TelegramBotTab
from gui.tabs.deliveries_tab import DeliveriesTab
from gui.tabs.licenses_tab import LicensesTab
from gui.tabs.requests_tab import RequestsTab
from gui.tabs.orders_tab import OrdersTab
from storage.repositories.subscription_repository import SubscriptionRepository
from gui.tabs.settings_tab import SettingsTab
from gui.themes.theme import ThemeManager
from gui.widgets.controls.controls import Button
from gui.workers.tasks import TaskRunner
from storage.database import Database
from storage.repositories.delivery_repository import DeliveryRepository
from storage.repositories.license_repository import LicenseRepository
from storage.repositories.request_repository import RequestRepository


class MainWindow(QMainWindow):
    def __init__(self, database=None, settings=None):
        super().__init__()
        self.database = database or Database()
        self.settings = settings or AppSettings()
        self.runner = TaskRunner(self)
        self.session = SigningSession(self.database, self.runner, self)
        self.bot = BotController(self.database, self)
        self.theme = ThemeManager(self.settings.theme, self)
        self._closing = False
        self._initializing = False
        self.setWindowTitle("V-CODE License Manager")
        self.resize(1440, 900)
        self.setMinimumSize(960, 700)
        center = QWidget()
        layout = QVBoxLayout(center)
        layout.setContentsMargins(26, 22, 26, 18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("V-CODE License Manager")
        title.setProperty("role", "title")
        titles.addWidget(title)
        subtitle = QLabel("Local workspace  /  Requests, licenses & delivery records")
        subtitle.setProperty("role", "muted")
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        self.badge = QLabel("Desktop signing locked")
        self.badge.setProperty("role", "badge")
        header.addWidget(self.badge)
        self.refresh_button = Button("Refresh all")
        self.refresh_button.clicked.connect(self.refresh_all)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)
        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.tabs = QTabWidget()
        self.requests = RequestsTab(RequestRepository(self.database), self.runner, self.theme)
        self.licenses = LicensesTab(LicenseRepository(self.database), self.runner, self.theme, self.session)
        self.deliveries = DeliveriesTab(DeliveryRepository(self.database), self.runner, self.theme)
        self.settings_tab = SettingsTab(self.database, self.settings, self.theme, self.session)
        self.telegram_tab = TelegramBotTab(self.database, self.settings, self.bot)
        self.orders = OrdersTab(SubscriptionRepository(self.database), self.runner, self.theme)
        self.record_tabs = (self.requests, self.licenses, self.deliveries, self.orders)
        for tab, title in ((self.requests, "Requests"), (self.licenses, "Licenses"), (self.deliveries, "Delivery History"), (self.telegram_tab, "Telegram Bot"), (self.settings_tab, "Settings"), (self.orders, 'Orders')):
            self.tabs.addTab(tab, title)
        layout.addWidget(self.tabs, 1)
        footer = QLabel("Issued ≠ sent ≠ activated  •  Request approval and retries remain in Telegram")
        footer.setProperty("role", "muted")
        layout.addWidget(footer)
        self.setCentralWidget(center)
        self.theme.apply(self.settings.theme)
        self.session.changed.connect(self._signing_changed)
        self.runner.idle.connect(self._idle)
        self.bot.stopped.connect(self._idle)
        self.bot_refresh = QTimer(self)
        self.bot_refresh.setSingleShot(True)
        self.bot_refresh.setInterval(350)
        self.bot_refresh.timeout.connect(self._refresh_bot_records)
        self.bot.records_changed.connect(lambda: self.bot_refresh.start())
        self.reconcile = QTimer(self)
        self.reconcile.setInterval(15000)
        self.reconcile.timeout.connect(lambda: self.bot_refresh.start() if self.bot.active else None)
        self.reconcile.start()
        QTimer.singleShot(0, self.refresh_all)

    def _signing_changed(self):
        self.badge.setText("Desktop signing busy" if self.session.issuing else "Desktop unlocking…" if self.session.unlocking else "Desktop signing unlocked" if self.session.unlocked else "Desktop signing locked")

    def refresh_all(self):
        if self._initializing or self._closing:
            return
        self._initializing = True
        self.session.ready = False
        self.session.changed.emit()
        self.refresh_button.setEnabled(False)
        self.error.clear()
        for tab in self.record_tabs:
            tab.ready = False
            tab.refresh_button.setEnabled(False)

        def ready(_):
            self._initializing = False
            self.refresh_button.setEnabled(True)
            self.session.ready = True
            self.session.changed.emit()
            for tab in self.record_tabs:
                tab.ready = True
                tab.refresh_button.setEnabled(True)
                tab.refresh()

        def failed(message):
            self._initializing = False
            self.refresh_button.setEnabled(True)
            self.error.setText(message)
            for tab in self.record_tabs:
                tab.page_label.setText("Storage unavailable · use Refresh all to retry")

        self.runner.run(self.database.initialize, ready, failed)

    def closeEvent(self, event):
        self.session.lock()
        if self.bot.active:
            self._closing = True
            self.bot.disconnect_bot()
            self.tabs.setCurrentWidget(self.telegram_tab)
            self.error.setText("Stopping managed bot before closing…")
            event.ignore()
            return
        if self.runner.busy:
            self._closing = True
            self.runner.accepting = False
            self.setEnabled(False)
            self.error.setText("Finishing active operations before closing…")
            event.ignore()
            return
        super().closeEvent(event)

    def _idle(self):
        if self._closing:
            QTimer.singleShot(0, self.close)

    def _refresh_bot_records(self):
        if self._closing:
            return
        if self.runner.busy or self._initializing:
            self.bot_refresh.start()
            return
        for tab in self.record_tabs:
            tab.refresh()
