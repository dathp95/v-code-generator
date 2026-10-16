"""Local desktop shell. Importing this module never imports or starts Telegram."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from app.settings import AppSettings
from app.signing import SigningSession
from gui.tabs.deliveries_tab import DeliveriesTab
from gui.tabs.licenses_tab import LicensesTab
from gui.tabs.requests_tab import RequestsTab
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
        self.badge = QLabel("Signing locked")
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
        for tab, title in ((self.requests, "Requests"), (self.licenses, "Licenses"), (self.deliveries, "Delivery History"), (self.settings_tab, "Settings")):
            self.tabs.addTab(tab, title)
        layout.addWidget(self.tabs, 1)
        footer = QLabel("Issued ≠ sent ≠ activated  •  Request approval and retries remain in Telegram")
        footer.setProperty("role", "muted")
        layout.addWidget(footer)
        self.setCentralWidget(center)
        self.theme.apply(self.settings.theme)
        self.session.changed.connect(self._signing_changed)
        self.runner.idle.connect(self._idle)
        QTimer.singleShot(0, self.refresh_all)

    def _signing_changed(self):
        self.badge.setText("Signing busy" if self.session.issuing else "Unlocking…" if self.session.unlocking else "Signing unlocked" if self.session.unlocked else "Signing locked")

    def refresh_all(self):
        if self._initializing or self._closing:
            return
        self._initializing = True
        self.session.ready = False
        self.session.changed.emit()
        self.refresh_button.setEnabled(False)
        self.error.clear()
        for tab in (self.requests, self.licenses, self.deliveries):
            tab.ready = False
            tab.refresh_button.setEnabled(False)

        def ready(_):
            self._initializing = False
            self.refresh_button.setEnabled(True)
            self.session.ready = True
            self.session.changed.emit()
            for tab in (self.requests, self.licenses, self.deliveries):
                tab.ready = True
                tab.refresh_button.setEnabled(True)
                tab.refresh()

        def failed(message):
            self._initializing = False
            self.refresh_button.setEnabled(True)
            self.error.setText(message)
            for tab in (self.requests, self.licenses, self.deliveries):
                tab.page_label.setText("Storage unavailable · use Refresh all to retry")

        self.runner.run(self.database.initialize, ready, failed)

    def closeEvent(self, event):
        self.session.lock()
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
