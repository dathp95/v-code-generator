"""Offline subscription transactions and Telegram flows using synthetic payment details."""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from telegram.error import TimedOut
from core.services.license_service import LicenseService
from services.approval_service import ApprovalService
from services.issuance_service import IssuanceService, RequestUnavailable
from services.subscription_service import SubscriptionConfig, SubscriptionService, SubscriptionError
from storage.database import Database, REPOSITORY_ROOT, StorageError
from storage.repositories.license_repository import LicenseRepository
from storage.repositories.request_repository import RequestRepository
from storage.repositories.subscription_repository import SubscriptionRepository
from integrations.telegram.handlers import subscription as ui, workflow
from test_activation import DEVICE_ID, make_private_key

# A synthetic one-pixel PNG, not a payment QR. No banking data is used by tests.
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
OWNER = dict(user_id=22, chat_id=222, chat_type='private')
ADMIN = dict(admin_id=1, chat_id=1, chat_type='private')


class SubscriptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.key = make_private_key()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.now = datetime(2026, 10, 18, 12, tzinfo=UTC)
        self.db = Database(self.root / 'test.sqlite3', clock=lambda: self.now)
        self.db.initialize()
        qr = self.root / 'synthetic.png'
        qr.write_bytes(PNG)
        self.config = SubscriptionConfig(99_000, 12, 30, 'Test Bank', 'Test Owner', '000000', qr)
        self.orders = SubscriptionService(self.db, 1, config=self.config)
        self.signer = Mock(wraps=LicenseService(self.key))
        self.issuance = IssuanceService(self.signer, self.db)

    def checkout(self, **overrides):
        return self.orders.checkout(**(dict(**OWNER, customer='Test Customer', device_id=DEVICE_ID.lower(), months=2) | overrides))

    def pay(self, order):
        _, nonce = self.orders.review_payment(order['order_id'], **ADMIN)
        return self.orders.confirm_payment(nonce, **ADMIN)

    def test_month_validation_exact_integer_totals_and_snapshot_resume(self):
        for invalid in (0, -1, 13, True, 1.0, '2', None):
            with self.subTest(value=invalid), self.assertRaises(SubscriptionError):
                self.checkout(months=invalid)
        order = self.checkout(months=12)
        self.assertEqual((order['months'], order['purchased_days'], order['unit_price_vnd'], order['total_vnd']), (12, 360, 99_000, 1_188_000))
        self.assertEqual(order['device_id'], DEVICE_ID)
        self.orders.config = replace(self.config, monthly_price=1_000_000, bank='Other Bank')
        self.config.qr_path.unlink()
        resumed = self.checkout(months=3)
        self.assertEqual(resumed, order)
        self.assertEqual(resumed['qr_image'], PNG)
        self.assertIn('99.000 VND', ui.summary(resumed))
        self.assertIn('Test Bank', ui.summary(resumed))
        self.assertIn('1 tháng = 30 ngày', ui.summary(resumed))
        self.assertEqual(resumed['payment_details']['transfer_reference'], order['reference'])

    def test_missing_qr_no_order_and_repository_relative_resolution(self):
        self.config.qr_path.unlink()
        with self.assertRaisesRegex(SubscriptionError, 'QR'):
            self.checkout()
        self.assertEqual(self.orders.orders.list_page().total, 0)
        synthetic = SimpleNamespace(MONTHLY_PRICE_VND=99_000, MAX_SUBSCRIPTION_MONTHS=12, DAYS_PER_MONTH=30,
            BANK_NAME='Test', BANK_ACCOUNT_NAME='Test', BANK_ACCOUNT_NUMBER='000', PAYMENT_QR_PATH='assets/payment/test.png')
        # Mock module loading so owner-local configuration and real assets are never read.
        with patch('services.subscription_service.import_module', return_value=synthetic), patch('os.getcwd', return_value=str(self.root)):
            self.assertEqual(SubscriptionConfig.load().qr_path, REPOSITORY_ROOT / 'assets/payment/test.png')
        with patch('services.subscription_service.import_module', side_effect=ModuleNotFoundError):
            with self.assertRaises(SubscriptionError): SubscriptionConfig.load()

    def test_ownership_private_admin_and_payment_report_never_issues(self):
        order = self.checkout()
        for args in (OWNER | {'user_id': 23}, OWNER | {'chat_id': 223}, OWNER | {'chat_type': 'group'}):
            for method in (self.orders.owned, self.orders.report_payment, self.orders.cancel):
                with self.assertRaises(SubscriptionError): method(order['order_id'], **args)
        for args in (ADMIN | {'admin_id': 22}, ADMIN | {'chat_type': 'group'}):
            with self.assertRaises(SubscriptionError): self.orders.review_payment(order['order_id'], **args)
            with self.assertRaises(SubscriptionError): self.orders.confirm_payment('anything', **args)
        reported = self.orders.report_payment(order['order_id'], **OWNER)
        self.assertEqual(reported['payment_status'], 'payment_reported')
        self.assertIsNone(reported['request_id'])
        self.assertEqual(self.orders.report_payment(order['order_id'], **OWNER), reported)
        self.signer.issue_activation.assert_not_called()

    def test_unpaid_request_gate_for_direct_and_all_approval_stages(self):
        order = self.checkout()
        req = RequestRepository(self.db).create_or_get_pending(channel='telegram_subscription',
            recipient_id=222, user_id=22, customer='Test Customer', device_id=DEVICE_ID)
        with self.db.transaction() as connection:
            connection.execute('UPDATE subscription_orders SET request_id = ? WHERE order_id = ?', (req['request_id'], order['order_id']))
        approval = ApprovalService(self.issuance)
        with self.assertRaises(RequestUnavailable): self.issuance.approve_request(req['request_id'], days=60, issuer=1)
        with self.assertRaises(RequestUnavailable): approval.select(req['request_id'], admin_id=1, chat_id=1)
        # Also block orphan subscription-channel requests, including an existing key path.
        with self.db.transaction() as connection:
            connection.execute('UPDATE subscription_orders SET request_id = NULL WHERE order_id = ?', (order['order_id'],))
        with self.assertRaises(RequestUnavailable): self.issuance.approve_request(req['request_id'], days=60, issuer=1)
        self.signer.issue_activation.assert_not_called()
        free = RequestRepository(self.db).create_or_get_pending(channel='telegram', recipient_id=222,
            user_id=22, customer='Test Customer', device_id=DEVICE_ID)
        self.issuance.approve_request(free['request_id'], days=1, issuer=1)
        self.signer.issue_activation.assert_called_once()

    def test_gate_rechecks_payment_at_preview_confirm_and_existing_license_retry(self):
        paid = self.pay(self.checkout())
        approval = ApprovalService(self.issuance)
        selected = approval.select(paid['request_id'], admin_id=1, chat_id=1)
        preview = approval.prepare(selected, admin_id=1, chat_id=1, days=60)
        with self.db.transaction() as connection:
            connection.execute("UPDATE subscription_orders SET payment_status = 'payment_reported' WHERE order_id = ?", (paid['order_id'],))
        with self.assertRaises(RequestUnavailable): approval.prepare(selected, admin_id=1, chat_id=1, days=60)
        with self.assertRaises(RequestUnavailable): approval.confirm(preview['form_id'], admin_id=1, chat_id=1)
        self.signer.issue_activation.assert_not_called()
        with self.db.transaction() as connection:
            connection.execute("UPDATE subscription_orders SET payment_status = 'paid' WHERE order_id = ?", (paid['order_id'],))
        self.issuance.approve_request(paid['request_id'], days=60, issuer=1)
        with self.db.transaction() as connection:
            connection.execute("UPDATE subscription_orders SET payment_status = 'payment_reported' WHERE order_id = ?", (paid['order_id'],))
        with self.assertRaises(RequestUnavailable): self.issuance.approve_request(paid['request_id'], days=60, issuer=1)
        self.signer.issue_activation.assert_called_once()

    def test_concurrent_payment_notification_claims_and_expired_owner_fencing(self):
        order = self.checkout()
        self.orders.report_payment(order['order_id'], **OWNER)
        with ThreadPoolExecutor(max_workers=4) as workers:
            owners = list(workers.map(lambda _: self.orders.claim_notification(order['order_id']), range(8)))
        owners = [owner for owner in owners if owner]
        self.assertEqual(len(owners), 1)
        self.now += timedelta(seconds=121)
        self.orders.recover_notifications()
        self.orders.finish_notification(order['order_id'], owners[0], 'sent')
        self.assertEqual(self.orders.orders.get(order['order_id'])['notification_status'], 'unknown')

    def test_duplicate_checkout_confirm_restart_and_single_request_license(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            ids = list(workers.map(lambda _: self.checkout()['order_id'], range(8)))
        self.assertEqual(len(set(ids)), 1)
        order, nonce = self.orders.review_payment(ids[0], **ADMIN)
        def confirm(_):
            return self.orders.confirm_payment(nonce, **ADMIN)
        with ThreadPoolExecutor(max_workers=4) as workers:
            paid = list(workers.map(confirm, range(8)))
        self.assertEqual(len({r['request_id'] for r in paid}), 1)
        self.assertEqual(paid[0]['confirmed_by'], '1')
        self.assertIsNotNone(paid[0]['paid_at'])
        self.signer.issue_activation.assert_not_called()
        reopened = Database(self.db.path, clock=lambda: self.now)
        reopened.initialize()
        resumed = SubscriptionService(reopened, 1, config=replace(self.config, monthly_price=3))
        self.assertEqual(resumed.orders.get(ids[0]), paid[0])
        def issue(_): return self.issuance.approve_request(paid[0]['request_id'], days=60, issuer=1)
        with ThreadPoolExecutor(max_workers=4) as workers:
            keys = list(workers.map(issue, range(4)))
        self.assertEqual(len({k['license_id'] for k in keys}), 1)
        self.signer.issue_activation.assert_called_once()
        self.assertEqual(keys[0]['activation_key'], keys[-1]['activation_key'])
        with self.assertRaises(SubscriptionError): self.orders.cancel(ids[0], **OWNER)

    def test_cancel_confirmation_race_is_atomic_and_never_orphans_request(self):
        for _ in range(4):
            order = self.checkout()
            _, nonce = self.orders.review_payment(order['order_id'], **ADMIN)
            def cancel():
                try: return self.orders.cancel(order['order_id'], **OWNER)
                except SubscriptionError: return None
            def confirm():
                try: return self.orders.confirm_payment(nonce, **ADMIN)
                except SubscriptionError: return None
            with ThreadPoolExecutor(max_workers=2) as workers:
                futures = [workers.submit(cancel), workers.submit(confirm)]
                [f.result() for f in futures]
            final = self.orders.orders.get(order['order_id'])
            self.assertIn(final['payment_status'], ('paid', 'cancelled'))
            self.assertEqual(final['request_id'] is not None, final['payment_status'] == 'paid')
            if final['payment_status'] == 'cancelled':
                with self.assertRaises(SubscriptionError): self.orders.confirm_payment(nonce, **ADMIN)

    def test_stale_cancelled_unauthorized_forms_and_payment_transaction_failure(self):
        order = self.checkout()
        _, first = self.orders.review_payment(order['order_id'], **ADMIN)
        _, current = self.orders.review_payment(order['order_id'], **ADMIN)
        with self.assertRaises(SubscriptionError): self.orders.confirm_payment(first, **ADMIN)
        with self.assertRaises(SubscriptionError): self.orders.confirm_payment(current, **(ADMIN | {'admin_id': 2}))
        self.now += timedelta(minutes=11)
        with self.assertRaises(SubscriptionError): self.orders.confirm_payment(current, **ADMIN)
        _, current = self.orders.review_payment(order['order_id'], **ADMIN)
        self.orders.cancel_review(current, **ADMIN)
        with self.assertRaises(SubscriptionError): self.orders.confirm_payment(current, **ADMIN)
        _, current = self.orders.review_payment(order['order_id'], **ADMIN)
        with self.db.transaction() as connection:
            connection.execute("CREATE TRIGGER fail_order_payment BEFORE UPDATE ON subscription_orders WHEN NEW.payment_status = 'paid' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(StorageError): self.orders.confirm_payment(current, **ADMIN)
        with self.db.connection() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM requests').fetchone()[0], 0)
        self.assertEqual(self.orders.orders.get(order['order_id'])['payment_status'], 'awaiting_payment')

    def test_migration_from_commit16_preserves_data_and_rolls_back_version3(self):
        db = Database(self.root / 'legacy.sqlite3')
        with db.connection() as connection:
            for filename in ('001_initial.sql', '002_reliable_workflow.sql'):
                connection.executescript((REPOSITORY_ROOT / 'storage/migrations' / filename).read_text(encoding='utf-8'))
        req = RequestRepository(db).create_or_get_pending(channel='telegram', recipient_id=222,
            user_id=22, customer='Existing', device_id=DEVICE_ID)
        issued = LicenseService(self.key).issue_activation(DEVICE_ID, 'Existing', 'Professional', 30)
        with db.transaction() as connection:
            license = LicenseRepository(db).add(issued, days=30, issuer=1, source='telegram_request', request_id=req['request_id'], connection=connection)
            RequestRepository(db).approve(req['request_id'], connection=connection)
        with patch('storage.database.Path.read_text', return_value='CREATE TABLE half_done (id INTEGER); INVALID SQL;'):
            with self.assertRaises(StorageError): db.initialize()
        with db.connection() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'half_done'").fetchone())
        db.initialize()
        db.initialize()
        self.assertEqual(LicenseRepository(db).get(license['license_id']), license)
        with db.connection() as connection:
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_read_only_pages_filters_search_and_token_free_links(self):
        order = self.checkout()
        paid = self.pay(order)
        key = self.issuance.approve_request(paid['request_id'], days=61, issuer=1)
        self.checkout(user_id=23, chat_id=223, customer='Other')
        page = self.orders.orders.list_page(search=order['reference'], status='paid')
        self.assertEqual(page.total, 1)
        self.assertEqual(page.records[0]['license_id'], key['license_id'])
        self.assertEqual(page.records[0]['issued_days'], 61)
        self.assertNotIn('activation_key', page.records[0])
        self.assertNotIn('qr_image', page.records[0])
        self.assertEqual(self.orders.orders.list_page(user_id=22, recipient_id=222).total, 1)
        self.assertEqual(self.orders.orders.list_page(search="%' OR 1=1 --").total, 0)


class SubscriptionTelegramTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls): cls.key = make_private_key()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = Database(self.root / 'test.sqlite3')
        self.signer = Mock(wraps=LicenseService(self.key))
        self.data = {'admin_id': 1}
        workflow.configure_storage(self.data, self.signer, self.db)
        qr = self.root / 'dummy.png'
        qr.write_bytes(PNG)
        self.orders = self.data['subscription_service']
        self.orders.config = SubscriptionConfig(99_000, 12, 30, 'Test Bank', 'Test', '000', qr)
        self.context = SimpleNamespace(application=SimpleNamespace(bot_data=self.data), user_data={}, args=[],
            bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42))))
        self.message = SimpleNamespace(text=DEVICE_ID, reply_text=AsyncMock(), reply_photo=AsyncMock())
        self.update = SimpleNamespace(effective_message=self.message,
            effective_user=SimpleNamespace(id=22, full_name='Test Customer'),
            effective_chat=SimpleNamespace(id=222, type='private'), callback_query=None)

    def callback(self, data):
        self.update.callback_query = SimpleNamespace(from_user=self.update.effective_user, data=data, answer=AsyncMock())

    async def checkout(self):
        self.callback('menu:subscribe')
        await ui.begin(self.update, self.context)
        self.message.text = DEVICE_ID
        await workflow.receive_form(self.update, self.context)
        from integrations.telegram.handlers.customer import company_action
        nonce = self.context.user_data['subscription_form']['company_nonce']
        self.callback('company:skip:' + nonce)
        await company_action(self.update, self.context)
        self.message.text = '2'
        await workflow.receive_form(self.update, self.context)
        return self.orders.orders.list_page().records[0]['order_id']

    async def test_report_repeated_clicks_no_duplicate_notification_no_issue_and_restart_view(self):
        oid = await self.checkout()
        self.assertEqual(self.message.reply_photo.await_args.kwargs['photo'].getvalue(), PNG)
        for _ in range(3):
            self.callback('order:report:' + oid)
            await ui.order_action(self.update, self.context)
        self.context.bot.send_message.assert_awaited_once()
        self.signer.issue_activation.assert_not_called()
        self.assertEqual(self.orders.orders.get(oid)['payment_status'], 'payment_reported')
        self.assertIsNone(self.orders.orders.get(oid)['request_id'])
        workflow.configure_storage(self.data, self.signer, Database(self.db.path))
        self.context.user_data.clear()
        self.callback('order:view:' + oid)
        await ui.order_action(self.update, self.context)
        self.assertIn('198.000', self.message.reply_photo.await_args.kwargs['caption'])
        self.update.effective_user = SimpleNamespace(id=23)
        self.callback('order:cancel:' + oid)
        before = self.message.reply_photo.await_count
        await ui.order_action(self.update, self.context)
        self.assertEqual(self.message.reply_photo.await_count, before)
        self.assertTrue(self.update.callback_query.answer.await_args.kwargs['show_alert'])

    async def test_admin_review_final_issue_correct_recipient_restart_retry_same_key(self):
        oid = await self.checkout()
        self.callback('order:report:' + oid)
        await ui.order_action(self.update, self.context)
        self.update.effective_user = SimpleNamespace(id=1)
        self.update.effective_chat.id = 1
        self.callback('order:review:' + oid)
        await ui.order_action(self.update, self.context)
        buttons = self.message.reply_text.await_args.kwargs['reply_markup'].inline_keyboard
        self.callback(buttons[0][0].callback_data)
        await ui.order_action(self.update, self.context)
        paid = self.orders.orders.get(oid)
        self.assertEqual(paid['payment_status'], 'paid')
        self.signer.issue_activation.assert_not_called()
        self.callback('request:' + paid['request_id'])
        await workflow.select_request(self.update, self.context)
        self.assertIn('Gợi ý Days: 60', self.context.bot.send_message.await_args.kwargs['text'])
        self.message.text = '61'
        await workflow.receive_form(self.update, self.context)
        self.assertIn('Đã mua: 60 ngày · Thực cấp: 61 ngày', self.message.reply_text.await_args.args[0])
        async def timeout_customer(**kwargs):
            if kwargs['chat_id'] == 222: raise TimedOut('synthetic-sensitive-error')
            return SimpleNamespace(message_id=42)
        self.context.bot.send_message.side_effect = timeout_customer
        self.callback('decision:confirm:' + self.context.user_data['key_form']['form_id'])
        await workflow.approval_decision(self.update, self.context)
        original = self.data['license_repository'].for_request(paid['request_id'])
        self.assertEqual(original['duration_days'], 61)
        self.assertEqual(self.context.bot.send_message.await_args.kwargs['chat_id'], 222)
        workflow.configure_storage(self.data, self.signer, Database(self.db.path))
        self.context.user_data.clear()
        self.context.bot.send_message.side_effect = None
        self.callback('request:' + paid['request_id'])
        await workflow.select_request(self.update, self.context)
        await workflow.receive_form(self.update, self.context)
        self.callback('decision:confirm:' + self.context.user_data['key_form']['form_id'])
        await workflow.approval_decision(self.update, self.context)
        saved = self.data['license_repository'].for_request(paid['request_id'])
        self.assertEqual(saved, original)
        self.assertIn(saved['activation_key'], self.context.bot.send_message.await_args.kwargs['text'])
        self.assertEqual(self.context.bot.send_message.await_args.kwargs['chat_id'], 222)
        self.signer.issue_activation.assert_called_once()

    async def test_missing_qr_disables_new_checkout_and_admin_group_rejected(self):
        self.orders.config.qr_path.unlink()
        self.callback('menu:subscribe')
        await ui.begin(self.update, self.context)
        self.assertNotIn('subscription_form', self.context.user_data)
        self.assertIn('QR', self.message.reply_text.await_args.args[0])
        self.assertEqual(self.orders.orders.list_page().total, 0)
        self.update.effective_chat.type = 'group'
        self.callback('order:review:missing')
        await ui.order_action(self.update, self.context)
        self.assertTrue(self.update.callback_query.answer.await_args.kwargs['show_alert'])

    async def test_notification_crash_recovery_does_not_resend(self):
        oid = await self.checkout()
        self.orders.report_payment(oid, **OWNER)
        claim = self.orders.claim_notification(oid)
        self.assertIsNotNone(claim)
        self.assertIsNone(self.orders.claim_notification(oid))
        with self.db.transaction() as connection:
            connection.execute("UPDATE subscription_orders SET notification_at = '2020-01-01T00:00:00Z' WHERE order_id = ?", (oid,))
        self.orders.recover_notifications()
        self.assertEqual(self.orders.orders.get(oid)['notification_status'], 'unknown')
        await ui.notify_admin(self.context, self.orders.orders.get(oid))
        self.context.bot.send_message.assert_not_awaited()


if __name__ == '__main__': unittest.main()
