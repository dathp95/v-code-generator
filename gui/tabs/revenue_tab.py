"""Native receipt dashboard. Reporting rules and queries live outside widgets."""
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QComboBox, QDateEdit, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLayout, QLineEdit, QScrollArea, QSplitter, QTableWidgetItem, QVBoxLayout, QWidget)
from services.revenue_service import RevenueFilters, RevenueError, format_vnd
from gui.widgets.controls.controls import Button, RecordTable
from gui.widgets.controls.receipt_chart import ReceiptChart


class RevenueTab(QWidget):
    def __init__(self, service, runner, theme, parent=None):
        super().__init__(parent)
        self.service, self.runner, self.theme = service, runner, theme
        self.ready = False
        self.report = None
        self.applied = None
        self.generation = 0
        self.dirty = False
        self.loading = self.queued = False
        self.page = 0
        self.detail_period = None
        self.sort, self.descending = 'paid_at', True
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        title = QLabel('Revenue · confirmed receipts')
        title.setProperty('role', 'heading')
        layout.addWidget(title)
        self.note = QLabel(f'Report timezone: {service.timezone_name}. Paid subscription orders only; saved Name/Company snapshots. Confirmed receipts ≠ profit. Profile edits do not change history.')
        self.note.setWordWrap(True)
        self.note.setProperty('role', 'muted')
        layout.addWidget(self.note)
        filters = QGridLayout()
        self.start = QDateEdit()
        self.end = QDateEdit()
        for edit in (self.start, self.end):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat('dd/MM/yyyy')
            edit.setDateRange(QDate(1900, 1, 1), QDate(9998, 12, 31))
        self.grouping = QComboBox()
        for label, key in (('Day', 'day'), ('Week · Monday', 'week'), ('Month', 'month')):
            self.grouping.addItem(label, key)
        self.company = QComboBox()
        self.company.setEditable(True)
        self.company.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.company.addItem('All companies', ('all', ''))
        self.company.addItem('Unspecified (blank)', ('unspecified', ''))
        self.company.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.company.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.name = QLineEdit()
        self.name.setPlaceholderText('Customer snapshot…')
        self.reference = QLineEdit()
        self.reference.setPlaceholderText('Order reference (optional)…')
        for edit in (self.name, self.reference):
            edit.setMaxLength(200)
        for row, fields in enumerate(((('From · inclusive', self.start), ('To · inclusive', self.end), ('Group', self.grouping)),
                                     (('Company', self.company), ('Name', self.name), ('Reference', self.reference)))):
            for column, (label, widget) in enumerate(fields):
                filters.addWidget(QLabel(label), row, column * 2)
                filters.addWidget(widget, row, column * 2 + 1)
                filters.setColumnStretch(column * 2 + 1, 1)
        layout.addLayout(filters)
        actions = QHBoxLayout()
        self.apply_button = Button('Apply', primary=True)
        self.reset_button = Button('Reset')
        self.refresh_button = Button('Refresh')
        for button in (self.apply_button, self.reset_button, self.refresh_button):
            actions.addWidget(button)
        actions.addStretch()
        self.clear_period = Button('All periods · clear detail selection')
        self.clear_period.clicked.connect(self.clear_detail_period)
        actions.addWidget(self.clear_period)
        layout.addLayout(actions)
        kpis = QHBoxLayout()
        self.total = self._kpi(kpis, 'Confirmed total · VND')
        self.order_count = self._kpi(kpis, 'Paid orders')
        self.customer_count = self._kpi(kpis, 'Unique customers · channel/user')
        layout.addLayout(kpis)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setProperty('role', 'error')
        layout.addWidget(self.error)
        self.status = QLabel('Waiting for report…')
        self.status.setProperty('role', 'muted')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.chart = ReceiptChart(theme)
        layout.addWidget(self.chart)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.summary = RecordTable()
        self.summary.setColumnCount(4)
        self.summary.setHorizontalHeaderLabels(['Period · click to narrow details', 'Paid orders', 'Unique customers', 'Confirmed total · VND'])
        self.summary.horizontalHeader().setSortIndicatorShown(False)
        self.summary.setColumnWidth(0, 430)
        self.summary.setMinimumHeight(110)
        splitter.addWidget(self.summary)
        details_box = QWidget()
        details_layout = QVBoxLayout(details_box)
        details_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_label = QLabel('Order details · all periods')
        self.detail_label.setWordWrap(True)
        details_layout.addWidget(self.detail_label)
        self.details = RecordTable()
        self.detail_columns = [('reference', 'Order reference'), ('paid_at', 'Paid at · report timezone'), ('customer', 'Customer snapshot'),
            ('company', 'Company snapshot'), ('months', 'Months'), ('total_vnd', 'Total · VND'), ('confirmed_by', 'Confirmed by'),
            ('request_id', 'Request ID'), ('license_id', 'License ID')]
        self.details.setColumnCount(len(self.detail_columns))
        self.details.setHorizontalHeaderLabels([label for _, label in self.detail_columns])
        self.details.setColumnWidth(0, 270)
        self.details.setColumnWidth(1, 240)
        self.details.setMinimumHeight(140)
        details_layout.addWidget(self.details)
        splitter.addWidget(details_box)
        splitter.setSizes([150, 210])
        layout.addWidget(splitter, 1)
        navigation = QHBoxLayout()
        self.page_label = QLabel('Opening report…')
        self.page_label.setWordWrap(True)
        navigation.addWidget(self.page_label, 1)
        self.previous, self.next = Button('← Previous'), Button('Next →')
        navigation.addWidget(self.previous)
        navigation.addWidget(self.next)
        layout.addLayout(navigation)
        self.apply_button.clicked.connect(self.apply_filters)
        self.reset_button.clicked.connect(self.reset_filters)
        self.refresh_button.clicked.connect(self.refresh_clicked)
        self.previous.clicked.connect(lambda: self.move_page(-1))
        self.next.clicked.connect(lambda: self.move_page(1))
        self.summary.cellClicked.connect(self.select_period)
        self.details.horizontalHeader().sectionClicked.connect(self.sort_details)
        for edit in (self.start, self.end):
            edit.dateChanged.connect(self.filters_changed)
        self.grouping.currentIndexChanged.connect(self.filters_changed)
        self.company.currentTextChanged.connect(self.filters_changed)
        self.name.textChanged.connect(self.filters_changed)
        self.reference.textChanged.connect(self.filters_changed)
        self.reset_filters()
        self._navigation()

    def _kpi(self, layout, title):
        frame = QFrame()
        frame.setProperty('role', 'kpi')
        box = QVBoxLayout(frame)
        label = QLabel(title)
        label.setWordWrap(True)
        box.addWidget(label)
        value = QLabel('—')
        value.setProperty('role', 'kpi_value')
        box.addWidget(value)
        layout.addWidget(frame, 1)
        return value

    def filters_changed(self, *args):
        self.generation += 1
        self.dirty = True
        self.page_label.setText('Filters changed · choose Apply to update. Displayed data is the previous snapshot.')
        self.status.setText('Filters changed · Apply to update. Displayed data is the previous snapshot.')
        self._navigation()

    def read_filters(self):
        index = self.company.currentIndex()
        if index >= 0 and self.company.currentText() == self.company.itemText(index):
            mode, company = self.company.currentData()
        else:
            company = self.company.currentText().strip()
            mode = 'exact' if company else 'all'
        return RevenueFilters(self.start.date().toPython(), self.end.date().toPython(), self.grouping.currentData(),
                              mode, company, self.name.text().strip(), self.reference.text().strip())

    def reset_filters(self):
        try:
            defaults = self.service.defaults()
        except RevenueError as error:
            self.error.setText(str(error))
            return
        self.start.setDate(QDate(defaults.start.year, defaults.start.month, defaults.start.day))
        self.end.setDate(QDate(defaults.end.year, defaults.end.month, defaults.end.day))
        self.grouping.setCurrentIndex(0)
        self.company.setCurrentIndex(0)
        self.name.clear()
        self.reference.clear()
        self.apply_filters()

    def apply_filters(self):
        self.generation += 1
        self.applied = self.read_filters()
        self.page, self.detail_period = 0, None
        self.dirty = False
        self.refresh()

    def refresh_clicked(self):
        if self.dirty:
            self.apply_filters()
        else:
            self.refresh()

    def refresh(self):
        if not self.ready or self.dirty or self.applied is None:
            return
        if self.loading:
            self.queued = True
            return
        self.loading = True
        self.generation += 1
        generation = self.generation
        filters, page, period, sort, descending = self.applied, self.page, self.detail_period, self.sort, self.descending
        self.error.clear()
        self.page_label.setText('Loading consistent receipt snapshot…')
        self.status.setText('Loading consistent receipt snapshot… Displayed data, if any, is the previous snapshot.')
        self._navigation()

        def finish():
            self.loading = False
            pending, self.queued = self.queued, False
            self._navigation()
            if pending:
                self.refresh()

        def success(report):
            if generation == self.generation:
                self.show_report(report)
            finish()

        def failure(message):
            if generation == self.generation:
                self.error.setText(message)
                self.status.setText('Report unavailable · check filters/configuration and Refresh. Displayed data is the previous snapshot.')
                self.page_label.setText('Report unavailable · check filters/configuration and Refresh. Displayed data is the previous snapshot.')
            finish()

        if not self.runner.run(lambda: self.service.report(filters, page=page, detail_period=period, sort=sort, descending=descending), success, failure):
            failure('Report was not started because the application is closing.')

    def show_report(self, report):
        selected = self.details.currentRow()
        selected_id = self.report.details.records[selected]['order_id'] if self.report and 0 <= selected < len(self.report.details.records) else None
        self.report, self.page = report, report.details.number
        self.total.setText(format_vnd(report.total_vnd))
        self.order_count.setText(str(report.paid_orders))
        self.customer_count.setText(str(report.unique_customers))
        self.status.setText(f'{report.paid_orders} confirmed paid orders · one consistent database snapshot.' if report.paid_orders else 'No confirmed receipts match these filters. Empty periods are shown as zero.')
        self.chart.set_periods(report.periods)
        self.summary.clearSelection()
        self.summary.setCurrentCell(-1, -1)
        self.summary.setRowCount(len(report.periods))
        for row, period in enumerate(report.periods):
            for col, value in enumerate((period.label, period.paid_orders, period.unique_customers, format_vnd(period.total_vnd))):
                self.summary.setItem(row, col, QTableWidgetItem(str(value)))
            if period.start == report.detail_period:
                self.summary.selectRow(row)
        self.details.clearSelection()
        self.details.setCurrentCell(-1, -1)
        self.details.setRowCount(len(report.details.records))
        for row, record in enumerate(report.details.records):
            for col, (key, _) in enumerate(self.detail_columns):
                value = record['paid_at_local'] if key == 'paid_at' else format_vnd(record[key]) if key == 'total_vnd' else record[key] or '—'
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.details.setItem(row, col, item)
            if record['order_id'] == selected_id:
                self.details.selectRow(row)
        mode, company = report.filters.company_mode, report.filters.company
        self.company.blockSignals(True)
        self.company.clear()
        self.company.addItem('All companies', ('all', ''))
        self.company.addItem('Unspecified (blank)', ('unspecified', ''))
        choices = list(report.companies)
        if mode == 'exact' and company not in choices:
            choices.append(company)
        for value in choices:
            label = value + ' (snapshot)' if value in ('All companies', 'Unspecified (blank)') else value
            self.company.addItem(label, ('exact', value))
        self.company.setCurrentIndex(0 if mode == 'all' else 1 if mode == 'unspecified' else choices.index(company) + 2)
        self.company.blockSignals(False)
        period = next((p for p in report.periods if p.start == report.detail_period), None)
        self.detail_label.setText('Order details · ' + (period.label if period else 'all applied periods') + f' · {report.details.total} paid orders')
        self.page_label.setText(('No confirmed receipts · zero periods shown. ' if not report.paid_orders else '') +
            f'Detail page {report.details.number + 1} · {report.details.total} matching orders · KPI totals cover all applied periods.')
        self._navigation()

    def _navigation(self):
        enabled = self.ready and not self.loading and not self.dirty and self.report is not None
        self.previous.setEnabled(enabled and self.page > 0)
        self.next.setEnabled(enabled and (self.page + 1) * self.report.details.size < self.report.details.total if self.report else False)
        self.clear_period.setEnabled(enabled and self.detail_period is not None)

    def move_page(self, delta):
        if self.loading or self.dirty:
            return
        self.page = max(0, self.page + delta)
        self.refresh()

    def select_period(self, row, column):
        if not self.report or self.dirty or not 0 <= row < len(self.report.periods):
            return
        self.detail_period = self.report.periods[row].start
        self.generation += 1
        self.page = 0
        self.refresh()

    def clear_detail_period(self):
        self.generation += 1
        self.detail_period = None
        self.page = 0
        self.refresh()

    def sort_details(self, column):
        self.generation += 1
        key = self.detail_columns[column][0]
        self.descending = not self.descending if key == self.sort else False
        self.sort = key
        self.page = 0
        self.refresh()
