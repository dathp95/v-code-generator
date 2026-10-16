"""Managed bot configuration and safe activity. Approval remains in Telegram."""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLayout, QLineEdit, QListWidget, QMessageBox, QScrollArea, QVBoxLayout, QWidget)
from app.credentials import TokenStore
from gui.widgets.controls.controls import Button


class TelegramBotTab(QWidget):
    def __init__(self, database, settings, controller, parent=None, token_store=None):
        super().__init__(parent)
        self.controller, self.settings = controller, settings
        self.token_store = token_store or TokenStore()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout.setContentsMargins(24, 18, 24, 18)
        heading = QLabel("Telegram Bot")
        heading.setProperty("role", "heading")
        layout.addWidget(heading)
        note = QLabel("Customer requests and admin approval use the existing Telegram workflow. Bot signing is separate from desktop signing; Settings → Lock signing does not stop this bot.")
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        layout.addWidget(note)
        self.config = QGroupBox("Connection · official Telegram Bot API")
        form = QFormLayout(self.config)
        form.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Bot Token (not API ID / API Hash)")
        token_row = QHBoxLayout()
        token_row.addWidget(self.token)
        self.show_token = QCheckBox("Show")
        self.show_token.toggled.connect(lambda show: self.token.setEchoMode(QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password))
        token_row.addWidget(self.show_token)
        form.addRow("Bot Token", token_row)
        self.remember = QCheckBox("Remember Token in Windows Credential Manager")
        self.remember.setChecked(settings.remember_token and self.token_store.available)
        if self.remember.isChecked():
            self.token.setText(self.token_store.read())
        form.addRow(self.remember)
        self.secure_note = QLabel()
        self.secure_note.setWordWrap(True)
        self.secure_note.setProperty("role", "muted")
        form.addRow(self.secure_note)
        self.admin = QLineEdit(settings.bot_admin_id)
        self.admin.setPlaceholderText("Positive numeric Telegram User ID")
        form.addRow("Admin User ID", self.admin)
        key_row = QHBoxLayout()
        self.path = QLineEdit(settings.bot_key_path)
        self.browse = Button("Browse…")
        self.browse.clicked.connect(self.choose_key)
        key_row.addWidget(self.path, 1)
        key_row.addWidget(self.browse)
        form.addRow("Encrypted RSA key", key_row)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Session only · cleared after Connect")
        form.addRow("Key password", self.password)
        db = QLabel(str(database.path.resolve()))
        db.setTextFormat(Qt.TextFormat.PlainText)
        db.setWordWrap(True)
        db.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Shared database", db)
        layout.addWidget(self.config)
        actions = QHBoxLayout()
        self.connect_button = Button("Connect", primary=True)
        self.disconnect_button = Button("Disconnect")
        self.force_button = Button("Force stop…")
        self.force_button.hide()
        self.connect_button.clicked.connect(self.connect_bot)
        self.disconnect_button.clicked.connect(controller.disconnect_bot)
        self.force_button.clicked.connect(self.force_stop)
        actions.addWidget(self.connect_button)
        actions.addWidget(self.disconnect_button)
        actions.addWidget(self.force_button)
        self.status = QLabel()
        self.status.setProperty("role", "badge")
        actions.addWidget(self.status)
        actions.addStretch()
        layout.addLayout(actions)
        self.identity = QLabel()
        self.identity.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.identity)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setProperty("role", "error")
        layout.addWidget(self.error)
        layout.addWidget(QLabel("Activity · safe summaries · Telegram acknowledgement ≠ V-CODE activation"))
        self.activity = QListWidget()
        self.activity.setMinimumHeight(90)
        layout.addWidget(self.activity, 1)
        controller.changed.connect(self.update_state)
        controller.activity.connect(self.add_activity)
        controller.stalled.connect(self.shutdown_stalled)
        self.update_secure_storage()
        self.update_state()

    def update_secure_storage(self):
        available = self.token_store.available
        self.remember.setEnabled(available)
        if not available:
            self.remember.setChecked(False)
        self.secure_note.setText("Token storage is opt-in. Key passwords are never saved." if available else "Secure Windows credential storage is unavailable. Remember Token is disabled; this token stays in this session only.")

    def choose_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose encrypted RSA key", self.path.text(), "Key files (*.pem *.key);;All files (*)")
        if path:
            self.path.setText(path)

    def connect_bot(self):
        if self.controller.active:
            return
        self.error.clear()
        try:
            admin = int(self.admin.text())
            if admin <= 0 or not self.token.text().strip() or not self.password.text() or not Path(self.path.text()).is_absolute():
                raise ValueError
        except ValueError:
            self.error.setText("Enter a Bot Token, positive Admin ID, absolute key path and key password.")
            return
        if self.token_store.available:
            saved = self.token_store.save(self.token.text().strip(), self.remember.isChecked())
            if not saved:
                self.update_secure_storage()
                self.error.setText("Secure storage failed. Connection is session-only; an older vault entry may remain.")
        try:
            self.settings.save(bot_admin_id=str(admin), bot_key_path=self.path.text(), remember_token=self.remember.isChecked())
        except OSError:
            self.error.setText("Nonsecret preferences could not be saved. Review Remember Token before reconnecting.")
        password = self.password.text()
        self.password.clear()
        self.controller.connect_bot(token=self.token.text().strip(), admin_id=admin,
                                    key_path=self.path.text(), password=password)

    def update_state(self):
        active = self.controller.active
        self.config.setEnabled(not active)
        self.connect_button.setEnabled(not active)
        self.disconnect_button.setEnabled(active and self.controller.state != "Disconnecting")
        self.status.setText(self.controller.state)
        self.identity.setText(self.controller.identity)
        if not active:
            self.force_button.hide()

    def add_activity(self, text):
        self.activity.addItem(f"{datetime.now().astimezone():%H:%M:%S}  {text}")
        while self.activity.count() > 100:
            self.activity.takeItem(0)
        self.activity.scrollToBottom()

    def shutdown_stalled(self):
        self.error.setText("Shutdown is taking longer than expected. Force stop can leave delivery outcomes unknown; duplicate messages are possible on retry.")
        self.force_button.show()

    def force_stop(self):
        if QMessageBox.warning(self, "Force stop bot?", "Forced termination can leave deliveries with unknown outcomes. Review records before retrying; duplicate Telegram messages are possible.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.controller.force_stop()
