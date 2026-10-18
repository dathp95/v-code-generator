"""License metadata and explicit copy/issuance actions."""

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from datetime import UTC, datetime
from gui.expiry import expiry_display
from gui.formatting import format_local

from gui.dialogs.issue_license_dialog import IssueLicenseDialog
from gui.tabs.records_tab import RecordsTab
from gui.widgets.controls.controls import Button


class LicensesTab(RecordsTab):
    def __init__(self, repository, runner, theme, session, parent=None, *, clock=None):
        self.clock = clock or (lambda: datetime.now(UTC))
        super().__init__(repository, runner, theme, [
            ("license_id", "License ID"), ("customer", "Customer"), ("device_id", "Device ID"),
            ("edition", "Edition"), ("issued_at", "Issued · local"), ("expires_at", "Expires · local"),
            ('remaining', 'Remaining'),
            ("source", "Source"), ("request_id", "Linked request"),
        ], "license_id", title="Licenses",
            note="Issued keys are saved in SQLite. Tokens are hidden; Copy Key reads only the selected key. Issuance never sends to Telegram.", parent=parent)
        self.session = session
        self.copy_button = Button("Copy Key")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_key)
        self.issue_button = Button("Issue manually", primary=True)
        self.issue_button.clicked.connect(self.open_issue_dialog)
        self.toolbar.addWidget(self.copy_button)
        self.toolbar.addWidget(self.issue_button)
        self.table.itemSelectionChanged.connect(self._update_actions)
        session.changed.connect(self._update_actions)
        self.dialog = None
        self._copying = False
        self._update_actions()
        for column, width in enumerate((220, 155, 240, 120, 180, 310, 295, 155, 210)):
            self.table.setColumnWidth(column, width)
        self.expiry_timer = QTimer(self)
        self.expiry_timer.setInterval(30000)
        self.expiry_timer.timeout.connect(self.update_expiry)
        self.expiry_timer.start()

    def _display_value(self, record, key, value):
        if key in ('expires_at', 'remaining'):
            view = expiry_display(record.get('expires_at'), now=self.clock())
            return view.expires if key == 'expires_at' else view.remaining
        return super()._display_value(record, key, value)

    def _records_loaded(self):
        self.update_expiry()

    def _apply_status_colors(self):
        super()._apply_status_colors()
        self.update_expiry()

    def _expiry_color(self, tone):
        c = self.theme.colors
        return QColor(c.expiry_red if tone == 'red' else c.expiry_green if tone == 'green' else c.muted)

    def update_expiry(self):
        now = self.clock()
        # Existing items only: no query, sorting, row reconstruction or selection change.
        for row, record in enumerate(self.records):
            view = expiry_display(record.get('expires_at'), now=now)
            for key, text in (('expires_at', view.expires), ('remaining', view.remaining)):
                column = next(i for i, (name, _) in enumerate(self.columns) if name == key)
                item = self.table.item(row, column)
                if item:
                    item.setText(text)
                    item.setToolTip(text + ' · ' + view.status)
                    item.setForeground(self._expiry_color(view.tone))
        self._expiry_details(now)

    def _selection_changed(self):
        self._expiry_details(self.clock())

    def _expiry_details(self, now):
        record = self.selected()
        if not record:
            self.details.clear()
            return
        position = self.details.verticalScrollBar().value()
        view = expiry_display(record.get('expires_at'), now=now)
        self.details.clear()
        cursor = QTextCursor(self.details.document())
        normal = QTextCharFormat()
        normal.setForeground(QColor(self.theme.colors.text))
        colored = QTextCharFormat()
        colored.setForeground(self._expiry_color(view.tone))
        cursor.insertText(f'Expires: {view.expires}\nRemaining: {view.remaining}\nExpiry status: {view.status}\n', colored)
        cursor.insertText(f"Company (current profile): {record.get('company') or '—'}\n", normal)
        for key, value in record.items():
            if key not in ('expires_at', 'remaining', 'company', 'payload_json', 'activation_key', 'claim_owner', 'scope'):
                text = format_local(value) if key.endswith('_at') else str(value if value is not None else '—')
                label = key.replace('_', ' ').title()
                cursor.insertText(f"{label}: {text}\n", normal)
        self.details.verticalScrollBar().setValue(position)

    def _update_actions(self):
        self.copy_button.setEnabled(self.selected() is not None and not getattr(self, "_copying", False))
        self.issue_button.setEnabled(self.session.can_issue)
        self.issue_button.setToolTip("Unlock signing in Settings to issue licenses." if not self.session.can_issue else "Create a locally recorded license after review and confirmation.")

    def copy_key(self):
        record = self.selected()
        if not record or self._copying:
            return
        license_id = record["license_id"]
        self._copying = True
        self._update_actions()

        def done(saved):
            self._copying = False
            self._update_actions()
            if saved is None:
                self.error.setText("The selected license is no longer available. Refresh the table.")
                return
            QApplication.clipboard().setText(saved["activation_key"])
            self.error.clear()
            self.page_label.setText(f"Key copied for license {license_id}.")

        def failed(message):
            self._copying = False
            self._update_actions()
            self.error.setText(message)

        self.runner.run(lambda: self.repository.get(license_id), done, failed)

    def open_issue_dialog(self):
        if not self.session.can_issue:
            return
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.raise_()
            return
        self.dialog = IssueLicenseDialog(self.session, self)
        self.dialog.issued.connect(lambda _: self.refresh())
        dialog = self.dialog
        dialog.finished.connect(lambda _: setattr(self, "dialog", None))
        dialog.finished.connect(dialog.deleteLater)
        self.dialog.show()
