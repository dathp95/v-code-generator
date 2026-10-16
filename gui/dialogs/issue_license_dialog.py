"""Review first; only explicit, single-use confirmation can issue a key."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QSpinBox, QVBoxLayout

from core.crypto.activation import validate_device_id
from core.services.license_service import LicenseService
from gui.formatting import format_local, timezone_label
from gui.widgets.controls.controls import Button


class IssueLicenseDialog(QDialog):
    issued = Signal(object)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("Issue a manual license")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(600, 510)
        self.resize(680, 590)
        self._reviewed = None
        self._submitted = False
        self._saved = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        title = QLabel("Manual issuance")
        title.setProperty("role", "heading")
        layout.addWidget(title)
        note = QLabel("Review the recipient details before confirming. The license is saved locally; no Telegram message is sent.")
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        layout.addWidget(note)
        form = QFormLayout()
        self.customer, self.device, self.edition = QLineEdit(), QLineEdit(), QLineEdit("Professional")
        self.customer.setPlaceholderText("Customer name")
        self.device.setPlaceholderText("VC-1234-5678-ABCD-EF12-3456")
        self.days = QSpinBox()
        self.days.setRange(1, 3650)
        self.days.setValue(30)
        for label, widget in (("Customer", self.customer), ("Device ID", self.device), ("Edition", self.edition), ("Days", self.days)):
            form.addRow(label, widget)
        layout.addLayout(form)
        self.review = QPlainTextEdit()
        self.review.setReadOnly(True)
        self.review.setPlaceholderText("Enter details, then choose Review.")
        layout.addWidget(self.review, 1)
        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        self.back = Button("Edit")
        self.back.clicked.connect(self.edit)
        self.back.hide()
        self.copy = Button("Copy Key")
        self.copy.clicked.connect(self.copy_key)
        self.copy.hide()
        self.cancel = Button("Cancel")
        self.cancel.clicked.connect(self.reject)
        self.review_button = Button("Review", primary=True)
        self.review_button.clicked.connect(self.prepare_review)
        self.confirm = Button("Confirm issuance", primary=True)
        self.confirm.clicked.connect(self.confirm_issue)
        self.confirm.hide()
        for widget in (self.back, self.copy):
            buttons.addWidget(widget)
        buttons.addStretch()
        for widget in (self.cancel, self.review_button, self.confirm):
            buttons.addWidget(widget)
        layout.addLayout(buttons)
        self.session.changed.connect(self.update_state)

    def prepare_review(self):
        if self._submitted or self._saved:
            return
        try:
            values = dict(customer=self.customer.text().strip(), device_id=validate_device_id(self.device.text()),
                          edition=self.edition.text().strip(), days=self.days.value())
            LicenseService.validate_inputs(**values)
        except ValueError:
            self.error.setText("Enter a customer, valid VC Device ID, edition, and 1–3650 days.")
            return
        self._reviewed = values
        self.error.clear()
        self.review.setPlainText("\n".join(f"{key.replace('_', ' ').title()}: {value}" for key, value in values.items()) +
                                 "\n\nCreated by: Local administrator\nDelivery: Copy manually. No messages will be sent.\nConfirm to sign and save once.")
        self._fields_enabled(False)
        self.review_button.hide()
        self.back.show()
        self.confirm.show()
        self.update_state()

    def _fields_enabled(self, enabled):
        for widget in (self.customer, self.device, self.edition, self.days):
            widget.setEnabled(enabled)

    def edit(self):
        if self._submitted or self._saved:
            return
        self._reviewed = None
        self._fields_enabled(True)
        self.back.hide()
        self.confirm.hide()
        self.review_button.show()
        self.review.clear()

    def update_state(self):
        self.confirm.setEnabled(self.session.can_issue and not self._submitted and self._saved is None)
        self.back.setEnabled(not self._submitted)
        self.cancel.setEnabled(not self._submitted)
        if self._reviewed and not self.session.unlocked and not self._submitted and not self._saved:
            self.error.setText("Signing is locked. Unlock in Settings before confirmation.")

    def confirm_issue(self):
        if self._submitted or self._saved or self._reviewed is None or not self.session.can_issue:
            return
        self._submitted = True
        self.error.clear()
        self.update_state()
        self.review.setPlainText("Signing and saving… Please wait; this operation will finish before closing.")
        if not self.session.issue(dict(self._reviewed), self._issued, self._failed):
            self._failed("Signing is locked or the application is closing.")

    def _issued(self, record):
        self._submitted = False
        self._saved = record
        self.confirm.hide()
        self.back.hide()
        self.copy.show()
        self.cancel.setText("Close")
        self.review.setPlainText(
            f"Saved license: {record['license_id']}\nCustomer: {record['payload']['customer']}\n"
            f"Device ID: {record['payload']['device_id']}\n"
            f"Issued: {format_local(record['issued_at'])}\nExpires: {format_local(record['expires_at'])}\n"
            f"Time zone: {timezone_label()}\n\nThe key is recorded. Use Copy Key when ready. No message was sent."
        )
        self.update_state()
        self.issued.emit(record["license_id"])

    def _failed(self, message):
        self._submitted = False
        self.error.setText(message)
        self.review.setPlainText("Issuance did not complete. Review the fields or retry after fixing the error.")
        self.update_state()

    def copy_key(self):
        if self._saved:
            QApplication.clipboard().setText(self._saved["activation_key"])

    def reject(self):
        if not self._submitted:
            super().reject()

    def closeEvent(self, event):
        if self._submitted:
            event.ignore()
        else:
            super().closeEvent(event)
