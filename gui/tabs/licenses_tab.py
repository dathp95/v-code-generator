"""License metadata and explicit copy/issuance actions."""

from PySide6.QtWidgets import QApplication

from gui.dialogs.issue_license_dialog import IssueLicenseDialog
from gui.tabs.records_tab import RecordsTab
from gui.widgets.controls.controls import Button


class LicensesTab(RecordsTab):
    def __init__(self, repository, runner, theme, session, parent=None):
        super().__init__(repository, runner, theme, [
            ("license_id", "License ID"), ("customer", "Customer"), ("device_id", "Device ID"),
            ("edition", "Edition"), ("issued_at", "Issued · local"), ("expires_at", "Expires · local"),
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
        for column, width in enumerate((220, 155, 240, 120, 180, 180, 155, 210)):
            self.table.setColumnWidth(column, width)

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
