"""Receipt accounting tests with temporary SQLite, fixed clocks, and no messaging."""
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from services.revenue_service import RevenueService, RevenueFilters, RevenueError, format_vnd
from storage.database import Database
from storage.repositories.revenue_repository import RevenueRepository
from services.customer_service import CustomerService
from services.subscription_service import SubscriptionConfig, SubscriptionService
from services.issuance_service import IssuanceService
from core.services.license_service import LicenseService
from storage.repositories.delivery_repository import DeliveryRepository
from storage.repositories.license_repository import LicenseRepository
from test_activation import DEVICE_ID, make_private_key
from test_subscriptions import PNG, OWNER, ADMIN


class RevenueFixture:
    def setup_revenue(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = Database(self.root / 'test.sqlite3')
        self.db.initialize()
        self.repo = RevenueRepository(self.db)
        self.service = RevenueService(self.repo, clock=lambda: datetime(2026, 1, 31, 18, tzinfo=UTC))
        self.sequence = 0

    def seed(self, *, paid_at='2026-02-01T05:00:00Z', status='paid', amount=99_000,
             user_id=22, name='Customer', company='Acme', created='2025-01-01T00:00:00Z'):
        self.sequence += 1
        oid = f'order-{self.sequence}'
        rid = f'request-{self.sequence}' if status == 'paid' else None
        with self.db.transaction() as connection:
            if rid:
                connection.execute('''INSERT INTO requests (request_id, channel, recipient_id, user_id,
                    customer, device_id, status, created_at, updated_at) VALUES (?, 'telegram_subscription', ?, ?, ?, ?, 'pending', ?, ?)''',
                    (rid, str(user_id), str(user_id), name, DEVICE_ID, created, created))
            connection.execute('''INSERT INTO subscription_orders
                (order_id, reference, user_id, recipient_id, customer, company, device_id, edition,
                 months, purchased_days, unit_price_vnd, total_vnd, currency, payment_details_json, qr_image,
                 payment_status, created_at, updated_at, paid_at, confirmed_by, request_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'Professional', 1, 30, ?, ?, 'VND', '{}', ?, ?, ?, ?, ?, ?, ?)''',
                (oid, oid.upper(), str(user_id), str(user_id), name, company, DEVICE_ID, amount, amount,
                 PNG, status, created, created, paid_at if status == 'paid' else None, '1' if rid else None, rid))
        return oid, rid

    def filters(self, **changes):
        return replace(RevenueFilters(date(2026, 2, 1), date(2026, 2, 28)), **changes)


class RevenueTests(RevenueFixture, unittest.TestCase):
    def setUp(self): self.setup_revenue()

    def test_paid_at_only_integer_saved_total_and_unique_identity(self):
        self.seed(amount=99_000, user_id=1, name='Same name')
        self.seed(amount=198_000, user_id=2, name='Same name')
        self.seed(amount=1, user_id=1, name='Changed name')
        for user, state in enumerate(('awaiting_payment', 'payment_reported', 'cancelled'), 50):
            self.seed(status=state, amount=999_999, user_id=user)
        self.seed(paid_at='2026-01-01T00:00:00Z', created='2026-02-01T00:00:00Z', amount=7)
        report = self.service.report(self.filters())
        self.assertEqual((report.total_vnd, report.paid_orders, report.unique_customers), (297_001, 3, 2))
        self.assertIs(type(report.total_vnd), int)
        self.assertEqual(format_vnd(report.total_vnd), '297.001 VND')
        self.assertEqual(report.periods[0].total_vnd, report.total_vnd)

    def test_default_month_uses_report_timezone_not_utc_or_host(self):
        defaults = self.service.defaults()
        self.assertEqual((defaults.start, defaults.end, defaults.grouping), (date(2026, 2, 1), date(2026, 2, 28), 'day'))
        self.assertEqual(self.service.timezone_name, 'Asia/Ho_Chi_Minh')

    def test_unique_customers_include_channel_and_normalize_telegram_subscription(self):
        self.seed(user_id=22)
        _, same_customer = self.seed(user_id=22, name='Renamed')
        _, other_channel = self.seed(user_id=22, name='Same name')
        with self.db.transaction() as connection:
            connection.execute("UPDATE requests SET channel = 'telegram' WHERE request_id = ?", (same_customer,))
            connection.execute("UPDATE requests SET channel = 'other' WHERE request_id = ?", (other_channel,))
        report = self.service.report(self.filters())
        self.assertEqual((report.paid_orders, report.unique_customers), (3, 2))

    def test_inclusive_dates_and_microsecond_local_month_boundaries(self):
        self.seed(paid_at='2026-01-31T16:59:59.999999Z', amount=1)
        self.seed(paid_at='2026-01-31T17:00:00Z', amount=2)
        self.seed(paid_at='2026-02-01T16:59:59.999999+00:00', amount=4)
        self.seed(paid_at='2026-02-02T00:00:00+07:00', amount=8)
        jan = self.service.report(self.filters(start=date(2026, 1, 31), end=date(2026, 1, 31)))
        first = self.service.report(self.filters(end=date(2026, 2, 1)))
        self.assertEqual((jan.total_vnd, first.total_vnd), (1, 6))
        self.assertEqual(first.periods[0].paid_orders, 2)
        self.assertIn('+0700', first.details.records[0]['paid_at_local'])

    def test_monday_weeks_year_transition_partial_boundaries_and_drilldown(self):
        self.seed(paid_at='2025-12-30T00:00:00Z', amount=1)
        self.seed(paid_at='2026-01-04T18:00:00Z', amount=2)  # Monday 5 Jan locally.
        filters = self.filters(start=date(2025, 12, 30), end=date(2026, 1, 6), grouping='week')
        report = self.service.report(filters)
        self.assertEqual([p.start for p in report.periods], [date(2025, 12, 29), date(2026, 1, 5)])
        self.assertTrue(all(p.partial for p in report.periods))
        self.assertEqual([p.total_vnd for p in report.periods], [1, 2])
        detail = self.service.report(filters, detail_period=date(2026, 1, 5))
        self.assertEqual(detail.details.total, 1)
        self.assertEqual(detail.total_vnd, 3)  # KPIs stay on all applied filters.
        self.assertEqual(detail.filters, filters)

    def test_calendar_months_zero_filling_and_customer_count_not_summed(self):
        self.seed(paid_at='2025-12-15T00:00:00Z', user_id=1)
        self.seed(paid_at='2026-02-01T00:00:00Z', user_id=1)
        report = self.service.report(self.filters(start=date(2025, 12, 15), end=date(2026, 2, 2), grouping='month'))
        self.assertEqual([p.total_vnd for p in report.periods], [99_000, 0, 99_000])
        self.assertEqual([p.partial for p in report.periods], [True, False, True])
        self.assertEqual(report.unique_customers, 1)
        self.assertEqual(sum(p.unique_customers for p in report.periods), 2)
        self.assertIn('partial', report.periods[0].label)

    def test_company_name_reference_snapshots_blank_and_literal_search(self):
        a, _ = self.seed(name='Original Name', company='Old Company', user_id=22)
        self.seed(company='', user_id=23)
        self.seed(company='   ', user_id=24)
        self.seed(name='100% literal', company='Other', user_id=25)
        filters = self.filters(company_mode='exact', company='Old Company', name='Original', reference=a)
        before = self.service.report(filters)
        customers = CustomerService(self.db)
        customers.ensure('telegram', 22, 'Original Name')
        customers.edit_admin(channel='telegram', user_id=22, display_name='Changed Profile', company='Current Company')
        self.assertEqual(self.service.report(filters), before)
        self.assertEqual(before.details.records[0]['company'], 'Old Company')
        blank = self.service.report(self.filters(company_mode='unspecified'))
        self.assertEqual(blank.paid_orders, 2)
        self.assertTrue(all(r['company'] == '—' for r in blank.details.records))
        self.assertEqual(self.service.report(self.filters(name='100%')).paid_orders, 1)
        self.assertEqual(self.service.report(self.filters(name="%' OR 1=1 --")).paid_orders, 0)
        self.assertNotIn('Current Company', before.companies)

    def test_pagination_multiple_delivery_attempts_and_manual_free_exclusion(self):
        _, request_id = self.seed(user_id=1)
        key = make_private_key()
        issued = LicenseService(key).issue_activation(DEVICE_ID, 'Customer', 'Professional', 30)
        with self.db.transaction() as connection:
            license = LicenseRepository(self.db).add(issued, days=30, issuer=1, source='telegram_request', request_id=request_id, connection=connection)
        deliveries = DeliveryRepository(self.db)
        for state in ('retryable_failed', 'unknown', 'succeeded'):
            claim = deliveries.start(request_id=request_id, license_id=license['license_id'], destination=1, purpose='customer_license', explicit_retry=True)
            deliveries.finish(claim, status=state)
        for index in range(40): self.seed(user_id=index % 3)
        IssuanceService(LicenseService(key), self.db).issue_manual(device_id=DEVICE_ID, customer='Free',
            edition='Professional', days=30, issuer='local_admin', source='desktop_manual')
        a = self.service.report(self.filters(), page=0, size=7)
        b = self.service.report(self.filters(), page=4, size=7)
        self.assertEqual((a.total_vnd, a.paid_orders, a.unique_customers), (41 * 99_000, 41, 3))
        self.assertEqual(a.total_vnd, b.total_vnd)
        self.assertEqual(a.periods, b.periods)
        self.assertEqual(len(b.details.records), 7)
        self.assertNotEqual(a.details.records, b.details.records)
        self.assertTrue(any(r['license_id'] == license['license_id'] for r in self.service.report(self.filters(), size=100).details.records))

    def test_repeated_payment_confirmation_counts_once(self):
        qr = self.root / 'synthetic.png'
        qr.write_bytes(PNG)
        subscriptions = SubscriptionService(self.db, 1, config=SubscriptionConfig(123_456, 12, 30, 'Test', 'Test', '000', qr))
        order = subscriptions.checkout(**OWNER, customer='Customer', device_id=DEVICE_ID, months=2)
        _, nonce = subscriptions.review_payment(order['order_id'], **ADMIN)
        subscriptions.confirm_payment(nonce, **ADMIN)
        subscriptions.confirm_payment(nonce, **ADMIN)
        paid = datetime.fromisoformat(subscriptions.orders.get(order['order_id'])['paid_at']).astimezone(self.service.zone()).date()
        report = self.service.report(self.filters(start=paid, end=paid))
        self.assertEqual((report.total_vnd, report.paid_orders), (246_912, 1))

    def test_consistent_snapshot_when_another_connection_commits_between_queries(self):
        with self.db.connection() as connection: connection.execute('PRAGMA journal_mode=WAL')
        self.seed(company='First')
        original = self.repo.details
        def details(connection, *args, **kwargs):
            self.assertTrue(connection.in_transaction)
            self.seed(company='Later')
            return original(connection, *args, **kwargs)
        with patch.object(self.repo, 'details', side_effect=details):
            report = self.service.report(self.filters())
        self.assertEqual((report.paid_orders, report.details.total, report.companies), (1, 1, ('First',)))
        self.assertEqual(self.service.report(self.filters()).paid_orders, 2)

    def test_no_data_bounded_ranges_buckets_timezone_and_allowlisted_sort(self):
        report = self.service.report(self.filters())
        self.assertEqual((report.total_vnd, report.paid_orders, report.unique_customers, report.details.total), (0, 0, 0, 0))
        self.assertEqual(len(report.periods), 28)
        self.assertTrue(all(p.total_vnd == 0 for p in report.periods))
        for filters in (self.filters(end=date(2026, 1, 1)), self.filters(grouping='sql'),
                        self.filters(start=date(2020, 1, 1)), self.filters(start=date(2025, 1, 1)), self.filters(company_mode='sql')):
            with self.assertRaises(RevenueError): self.service.report(filters)
        with self.assertRaises(ValueError): self.service.report(self.filters(), sort='paid_at; DROP TABLE requests')
        with self.assertRaises(RevenueError): self.service.report(self.filters(), detail_period=date(2020, 1, 1))
        with self.assertRaises(RevenueError): RevenueService(self.repo, timezone_name='Invalid/Zone').defaults()

    def test_configurable_dst_boundaries_and_skinny_sql(self):
        service = RevenueService(self.repo, timezone_name='America/New_York')
        start, end = service.boundaries(date(2026, 3, 8), date(2026, 3, 8))
        self.assertEqual(end - start, timedelta(hours=23))
        self.seed()
        statements = []
        prepare = self.repo.prepare
        def trace(connection):
            prepare(connection)
            connection.set_trace_callback(statements.append)
        with patch.object(self.repo, 'prepare', side_effect=trace): self.service.report(self.filters())
        sql = '\n'.join(statements).lower()
        for forbidden in ('qr_image', 'activation_key', 'payload_json', 'deliveries', 'customer_profiles'):
            self.assertNotIn(forbidden, sql)


class ControlledRunner:
    def __init__(self): self.jobs = []

    def run(self, function, success, failure):
        self.jobs.append((function, success, failure))
        return True

    def finish(self):
        function, success, failure = self.jobs.pop(0)
        success(function())


class RevenueGuiTests(RevenueFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self): self.setup_revenue()

    def test_stale_results_single_flight_filters_and_recoverable_error(self):
        from gui.tabs.revenue_tab import RevenueTab
        from gui.themes.theme import ThemeManager
        self.seed(name='First')
        self.seed(name='Second', amount=123_456)
        runner = ControlledRunner()
        tab = RevenueTab(self.service, runner, ThemeManager('light'))
        tab.ready = True
        tab.refresh()
        self.assertEqual(len(runner.jobs), 1)
        tab.name.setText('Second')  # Immediately invalidates the in-flight result.
        tab.apply_filters()  # Queues one latest request; does not start a second concurrent query.
        self.assertEqual(len(runner.jobs), 1)
        runner.finish()
        self.assertIsNone(tab.report)
        self.assertEqual(len(runner.jobs), 1)
        runner.finish()
        self.assertEqual((tab.report.total_vnd, tab.report.paid_orders), (123_456, 1))
        self.assertEqual(tab.name.text(), 'Second')
        tab.refresh()
        _, _, failure = runner.jobs.pop(0)
        failure('Storage unavailable. Refresh to retry.')
        self.assertIn('Storage unavailable', tab.error.text())
        tab.refresh()
        runner.finish()
        self.assertEqual(tab.error.text(), '')
        tab.name.setText('Missing')
        tab.apply_filters()
        runner.finish()
        self.assertEqual(tab.total.text(), '0 VND')
        self.assertIn('No confirmed receipts', tab.page_label.text())
        tab.deleteLater()

    def test_real_worker_theme_period_drilldown_and_refresh_preserves_filters(self):
        from PySide6.QtCore import QThread
        from gui.tabs.revenue_tab import RevenueTab
        from gui.themes.theme import ThemeManager
        from gui.workers.tasks import TaskRunner
        from test_gui import wait_until
        self.seed(company='Saved Company', paid_at='2026-02-01T00:00:00Z')
        self.seed(company='Saved Company', paid_at='2026-02-02T00:00:00Z')
        self.seed(company='Other', paid_at='2026-02-02T00:00:00Z')
        theme = ThemeManager('light')
        runner = TaskRunner()
        tab = RevenueTab(self.service, runner, theme)
        seen = []
        receipts = self.repo.receipts
        def record_thread(*args, **kwargs):
            seen.append(QThread.currentThread())
            return receipts(*args, **kwargs)
        try:
            with patch.object(self.repo, 'receipts', side_effect=record_thread):
                tab.ready = True
                tab.refresh()
                wait_until(lambda: not runner.busy)
            self.assertNotEqual(seen[0], self.application.thread())
            index = tab.company.findText('Saved Company')
            self.assertGreaterEqual(index, 2)
            tab.company.setCurrentIndex(index)
            tab.apply_filters()
            wait_until(lambda: not runner.busy)
            tab.select_period(1, 0)
            wait_until(lambda: not runner.busy)
            self.assertEqual(tab.report.details.total, 1)
            self.assertEqual(tab.report.paid_orders, 2)
            tab.refresh()
            wait_until(lambda: not runner.busy)
            self.assertEqual(tab.report.detail_period, date(2026, 2, 2))
            self.assertEqual(tab.report.filters.company, 'Saved Company')
            for name in ('light', 'dark'):
                theme.apply(name)
                self.assertFalse(tab.chart.grab().isNull())
                self.assertEqual(tab.total.text(), '198.000 VND')
            tab.clear_detail_period()
            wait_until(lambda: not runner.busy)
            self.assertEqual(tab.report.details.total, 2)
            tab.reset_filters()
            wait_until(lambda: not runner.busy)
            self.assertEqual(tab.report.filters.company_mode, 'all')
        finally:
            runner.pool.waitForDone()
            tab.deleteLater()


if __name__ == '__main__': unittest.main()
