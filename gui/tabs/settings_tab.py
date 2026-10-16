"""Nonsecret preferences and explicit in-memory signing unlock."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from gui.formatting import timezone_label
from gui.widgets.controls.controls import Button


class SettingsTab(QWidget):
    def __init__(self, database, settings, theme, session, parent=None):
        super().__init__(parent)
        self.settings, self.theme, self.session = settings, theme, session
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        heading = QLabel("Settings")
        heading.setProperty("role", "heading")
        layout.addWidget(heading)
        info = QGroupBox("Local workspace")
        form = QFormLayout(info)
        self.db_path = QLabel(str(database.path))
        self.db_path.setWordWrap(True)
        self.db_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Active database", self.db_path)
        form.addRow("Display timezone", QLabel(timezone_label()))
        self.theme_choice = QComboBox()
        self.theme_choice.addItem("Light", "light")
        self.theme_choice.addItem("Dark", "dark")
        self.theme_choice.setCurrentIndex(1 if settings.theme == "dark" else 0)
        form.addRow("Appearance", self.theme_choice)
        layout.addWidget(info)
        signing = QGroupBox("Signing · memory only")
        signing_form = QFormLayout(signing)
        self.state = QLabel("Locked — browsing does not require a signing key.")
        self.state.setWordWrap(True)
        signing_form.addRow("Status", self.state)
        path_row = QHBoxLayout()
        self.path = QLineEdit(settings.key_path)
        self.path.setPlaceholderText("Select an encrypted RSA private-key file…")
        self.browse = Button("Browse…")
        self.browse.clicked.connect(self.choose_key)
        path_row.addWidget(self.path, 1)
        path_row.addWidget(self.browse)
        signing_form.addRow("Private-key file", path_row)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Password is never saved")
        signing_form.addRow("Password", self.password)
        actions = QHBoxLayout()
        self.unlock = Button("Unlock signing", primary=True)
        self.lock = Button("Lock signing")
        self.unlock.clicked.connect(self.unlock_signing)
        self.lock.clicked.connect(session.lock)
        actions.addWidget(self.unlock)
        actions.addWidget(self.lock)
        actions.addStretch()
        signing_form.addRow(actions)
        note = QLabel("Keys are loaded only when you choose Unlock signing. Lock blocks new issuance; a confirmed operation already running finishes safely. No .env or Telegram credentials are loaded.")
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        signing_form.addRow(note)
        layout.addWidget(signing)
        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        layout.addStretch()
        self.theme_choice.currentIndexChanged.connect(self.change_theme)
        self.path.textChanged.connect(self.update_state)
        self.password.textChanged.connect(self.update_state)
        session.changed.connect(self.update_state)
        session.error.connect(self.error.setText)
        self.update_state()

    def choose_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose encrypted RSA private key", self.path.text(), "Key files (*.pem *.key);;All files (*)")
        if path:
            self.path.setText(path)

    def change_theme(self):
        name = self.theme_choice.currentData()
        self.theme.apply(name)
        try:
            self.settings.save(theme=name)
        except OSError:
            self.error.setText("Theme applied, but preferences could not be saved.")

    def unlock_signing(self):
        if self.session.unlocking or self.session.issuing:
            return
        path, password = self.path.text().strip(), self.password.text()
        self.password.clear()
        self.error.clear()
        try:
            self.settings.save(key_path=path)
        except OSError:
            self.error.setText("Key path preference could not be saved; the password is never saved.")
        self.session.unlock(path, password)

    def update_state(self):
        session = self.session
        state = "Signing and saving…" if session.issuing else "Unlocking…" if session.unlocking else "Unlocked · ready for manual issuance" if session.unlocked else "Locked — browsing does not require a signing key."
        self.state.setText(state)
        self.unlock.setEnabled(not session.unlocking and not session.issuing and bool(self.path.text().strip()) and bool(self.password.text()))
        self.lock.setEnabled(session.unlocked or session.unlocking or session.issuing)
        self.browse.setEnabled(not session.unlocking and not session.issuing)
