"""Shared asynchronous, paginated record browser. No SQL in widgets."""

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QSplitter, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui.formatting import format_local, timezone_label
from gui.widgets.controls.controls import Button, RecordTable


class RecordsTab(QWidget):
    def __init__(self, repository, runner, theme, columns, id_field, *, title, note, status_filter=False, parent=None):
        super().__init__(parent)
        self.repository, self.runner, self.theme = repository, runner, theme
        self.columns, self.id_field = columns, id_field
        self.records, self.page, self.generation = [], 0, 0
        self.sort = "created_at" if id_field == "request_id" else "issued_at" if id_field == "license_id" else "delivery_id"
        self.descending = True
        self.ready = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        heading = QLabel(title)
        heading.setProperty("role", "heading")
        layout.addWidget(heading)
        help_text = QLabel(note + "  Times: " + timezone_label())
        help_text.setWordWrap(True)
        help_text.setProperty("role", "muted")
        layout.addWidget(help_text)
        self.toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search customer, Device ID, or record ID…" if id_field != "delivery_id" else "Search request ID, license ID, or destination…")
        self.search.setClearButtonEnabled(True)
        self.toolbar.addWidget(self.search, 1)
        self.status = QComboBox() if status_filter else None
        if self.status:
            self.status.addItems(["all", "pending", "approved", "rejected", "closed"])
            self.status.setMinimumWidth(130)
            self.toolbar.addWidget(self.status)
            self.status.currentTextChanged.connect(self._search_changed)
        self.refresh_button = Button("Refresh")
        self.refresh_button.setEnabled(False)
        self.refresh_button.clicked.connect(self.refresh)
        self.toolbar.addWidget(self.refresh_button)
        layout.addLayout(self.toolbar)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setProperty("role", "error")
        layout.addWidget(self.error)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = RecordTable()
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels([label for key, label in columns])
        self.table.horizontalHeader().setSortIndicator(
            next((i for i, (key, _) in enumerate(columns) if key == self.sort), 0), Qt.SortOrder.DescendingOrder,
        )
        self.table.horizontalHeader().sectionClicked.connect(self._sort_changed)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        splitter.addWidget(self.table)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Select a row to view details.")
        self.details.setMinimumHeight(95)
        splitter.addWidget(self.details)
        splitter.setSizes([360, 130])
        splitter.setStretchFactor(0, 3)
        layout.addWidget(splitter, 1)
        navigation = QHBoxLayout()
        self.page_label = QLabel("Opening database…")
        self.page_label.setProperty("role", "muted")
        navigation.addWidget(self.page_label, 1)
        self.previous = Button("← Previous")
        self.next = Button("Next →")
        self.previous.clicked.connect(lambda: self._move(-1))
        self.next.clicked.connect(lambda: self._move(1))
        navigation.addWidget(self.previous)
        navigation.addWidget(self.next)
        layout.addLayout(navigation)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(self._search_changed)
        theme.changed.connect(self._apply_status_colors)

    def _search_changed(self):
        self.page = 0
        self.generation += 1
        self.timer.start()

    def _sort_changed(self, index):
        key = self.columns[index][0]
        self.descending = not self.descending if key == self.sort else False
        self.sort = key
        self.table.horizontalHeader().setSortIndicator(index, Qt.SortOrder.DescendingOrder if self.descending else Qt.SortOrder.AscendingOrder)
        self.refresh()

    def _move(self, direction):
        self.page = max(0, self.page + direction)
        self.refresh()

    def selected(self):
        index = self.table.currentRow()
        return self.records[index] if 0 <= index < len(self.records) else None

    def refresh(self):
        if not self.ready:
            return
        self.generation += 1
        generation = self.generation
        selected = self.selected()
        selected_id = selected[self.id_field] if selected else None
        kwargs = dict(search=self.search.text(), page=self.page, sort=self.sort, descending=self.descending)
        if self.status:
            kwargs["status"] = self.status.currentText()
        self.error.clear()
        self.page_label.setText("Loading records…")

        def success(result):
            if generation != self.generation:
                return
            self.records, self.page = result.records, result.number
            self.table.setRowCount(0)
            self.table.setRowCount(len(self.records))
            for row, record in enumerate(self.records):
                for column, (key, _) in enumerate(self.columns):
                    value = record.get(key)
                    text = format_local(value) if key.endswith("_at") else str(value if value is not None else "—")
                    if value == "succeeded" and key in ("status", "delivery_status"):
                        text = "Output saved" if record.get("purpose") in ("cli_stdout", "cli_file") else "Sent · acknowledged"
                    if value == "unknown":
                        text = "UNKNOWN · review needed"
                    item = QTableWidgetItem(text)
                    item.setToolTip(text)
                    self.table.setItem(row, column, item)
            self._apply_status_colors()
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            for row, record in enumerate(self.records):
                if record[self.id_field] == selected_id:
                    self.table.selectRow(row)
                    break
            self._selection_changed()
            start = result.number * result.size + 1 if result.total else 0
            self.page_label.setText(f"{start}–{min((result.number + 1) * result.size, result.total)} of {result.total}  ·  Page {result.number + 1}")
            self.previous.setEnabled(result.number > 0)
            self.next.setEnabled((result.number + 1) * result.size < result.total)

        def failure(message):
            if generation == self.generation:
                self.error.setText(message)
                self.page_label.setText("Refresh to retry · displayed rows may be stale")

        self.runner.run(lambda: self.repository.list_page(**kwargs), success, failure)

    def _selection_changed(self):
        record = self.selected()
        self.details.setPlainText("\n".join(
            f"{key.replace('_', ' ').title()}: {format_local(value) if key.endswith('_at') or key in ('lease_until', 'retry_not_before') else value}"
            for key, value in (record or {}).items()
            if value is not None and key not in ("payload_json", "activation_key", "claim_owner", "scope")
        ))

    def _apply_status_colors(self):
        c = self.theme.colors
        for row, record in enumerate(self.records):
            for column in range(len(self.columns)):
                item = self.table.item(row, column)
                if not item:
                    continue
                unknown = record.get("status") == "unknown" or record.get("delivery_status") == "unknown"
                item.setForeground(QColor(c.warning if unknown else c.text))
                item.setBackground(QColor(c.warning_background if unknown else c.panel if row % 2 else c.window))
