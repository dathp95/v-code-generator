"""Local admin profile editor backed by services and queued Qt workers."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QLayout, QLineEdit, QScrollArea
from gui.tabs.records_tab import RecordsTab
from gui.widgets.controls.controls import Button
from services.customer_service import CustomerValidationError, validate_metadata


class CustomersTab(RecordsTab):
    saved = Signal()

    def __init__(self, repository, runner, theme, service, parent=None):
        super().__init__(repository, runner, theme, [
            ('display_name', 'Name'), ('company', 'Company'), ('channel', 'Channel'),
            ('user_id', 'Telegram User ID'), ('updated_at', 'Updated · local'),
        ], 'profile_id', title='Customers', note='Profile metadata only. Edits never rewrite saved orders, approval previews or signed licenses.', parent=parent)
        self.service = service
        self._editing_id = None
        self._saving = False
        self._dirty = False
        self._last_saved = None
        self.search.setPlaceholderText('Search name, Company or Telegram User ID…')
        self.editor = QGroupBox('Selected customer · local admin')
        form = QFormLayout(self.editor)
        form.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.user_id = QLineEdit()
        self.user_id.setReadOnly(True)
        self.name = QLineEdit()
        self.company = QLineEdit()
        self.company.setPlaceholderText('Optional · up to 150 characters')
        self.name.setMaxLength(151)
        self.company.setMaxLength(151)
        form.addRow('Telegram User ID', self.user_id)
        form.addRow('Name', self.name)
        form.addRow('Company', self.company)
        self.save_button = Button('Save', primary=True)
        self.save_button.clicked.connect(self.save_profile)
        form.addRow(self.save_button)
        self.feedback = QLabel('Select a customer to edit.')
        self.feedback.setWordWrap(True)
        form.addRow(self.feedback)
        # The editor replaces duplicated raw details and can scroll at compact sizes.
        self.details.hide()
        self.editor_scroll = QScrollArea()
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setWidget(self.editor)
        self.editor_scroll.setMinimumHeight(170)
        self.editor_scroll.setMaximumHeight(285)
        self.layout().insertWidget(self.layout().count() - 1, self.editor_scroll)
        self.name.textEdited.connect(self._mark_dirty)
        self.company.textEdited.connect(self._mark_dirty)
        self.editor.setEnabled(False)

    def _selection_changed(self):
        super()._selection_changed()
        if not hasattr(self, 'editor') or self._saving:
            return
        profile = self.selected()
        identity = (profile['channel'], profile['user_id']) if profile else None
        # Keep unsaved input across background refreshes of the same selection.
        if identity == self._editing_id and self._dirty:
            return
        self._editing_id = identity
        self._dirty = False
        self.editor.setEnabled(profile is not None)
        self.user_id.setText(profile['user_id'] if profile else '')
        self.name.setText(profile['display_name'] if profile else '')
        self.company.setText(profile['company'] if profile else '')
        self.feedback.setText('Saved successfully. Historical snapshots are unchanged.' if identity and identity == self._last_saved else 'Edit name/Company, then Save.' if profile else 'Select a customer to edit.')

    def _mark_dirty(self):
        self._dirty = True
        self._last_saved = None
        self.feedback.setText('Unsaved changes · choose Save to persist.')

    def save_profile(self):
        if self._saving or not self._editing_id:
            return
        try:
            name = validate_metadata(self.name.text(), name='Name', required=True)
            company = validate_metadata(self.company.text())
        except CustomerValidationError as error:
            self.feedback.setText(str(error))
            self.editor_scroll.ensureWidgetVisible(self.feedback)
            return
        channel, user_id = self._editing_id
        self._saving = True
        self.editor.setEnabled(False)
        self.feedback.setText('Saving…')

        def success(profile):
            self._saving = False
            self._dirty = False
            self._last_saved = (channel, user_id)
            self.editor.setEnabled(self.selected() is not None)
            # Do not replace fields for a different customer selected during the write.
            current = self.selected()
            if current and (current['channel'], current['user_id']) == (channel, user_id):
                self.name.setText(profile['display_name'])
                self.company.setText(profile['company'])
                self.feedback.setText('Saved successfully. Historical snapshots are unchanged.')
                self.editor_scroll.ensureWidgetVisible(self.feedback)
            else:
                self._editing_id = None
                self._selection_changed()
            self.saved.emit()

        def failure(message):
            self._saving = False
            current = self.selected()
            if not current or (current['channel'], current['user_id']) != (channel, user_id):
                self._editing_id = None
                self._dirty = False
                self._selection_changed()
            self.editor.setEnabled(self.selected() is not None)
            self.feedback.setText(message)
            self.editor_scroll.ensureWidgetVisible(self.feedback)

        if not self.runner.run(lambda: self.service.edit_admin(channel=channel, user_id=user_id, display_name=name, company=company), success, failure):
            failure('The application is closing. Save was not started.')
