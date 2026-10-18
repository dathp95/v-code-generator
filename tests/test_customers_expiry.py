"""Temporary profiles/snapshots, mocked Telegram, and exact wall-clock expiry tests."""
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from core.services.license_service import LicenseService
from services.customer_service import CustomerService, CustomerValidationError, validate_metadata
from services.approval_service import ApprovalService
from services.issuance_service import IssuanceService
from services.subscription_service import SubscriptionConfig, SubscriptionService
from storage.database import Database, REPOSITORY_ROOT, StorageError
from storage.repositories.request_repository import RequestRepository
from storage.repositories.license_repository import LicenseRepository
from storage.repositories.subscription_repository import SubscriptionRepository
from gui.expiry import expiry_display
from integrations.telegram.handlers import customer, subscription, workflow
from test_activation import DEVICE_ID, make_private_key
from test_subscriptions import PNG, OWNER, ADMIN


class ExpiryTests(unittest.TestCase):
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)

    def test_exact_boundaries_and_expiration_status(self):
        for interval, tone in ((timedelta(days=4), 'green'), (timedelta(hours=72), 'green'),
                               (timedelta(hours=72, microseconds=-1), 'red'),
                               (timedelta(0), 'red'), (timedelta(minutes=-1), 'red')):
            with self.subTest(interval=interval):
                view = expiry_display((self.now + interval).isoformat(), now=self.now)
                self.assertEqual(view.tone, tone)
                if interval <= timedelta(0):
                    self.assertEqual(view.remaining, 'Expired / Đã hết hạn')
                else:
                    self.assertIn('h ', view.remaining)
                    self.assertIn('m ·', view.remaining)
        self.assertIn('3d 0h 0m', expiry_display(self.now + timedelta(days=3), now=self.now).remaining)
        self.assertIn('2d 23h 59m', expiry_display(self.now + timedelta(days=3, microseconds=-1), now=self.now).remaining)

    def test_utc_local_equivalence_missing_and_invalid_timestamps(self):
        bangkok = timezone(timedelta(hours=7))
        utc = self.now + timedelta(hours=72)
        a = expiry_display(utc.isoformat(), now=self.now, zone=bangkok)
        b = expiry_display(utc.astimezone(bangkok).isoformat(), now=self.now.astimezone(bangkok), zone=bangkok)
        self.assertEqual(a, b)
        self.assertIn('UTC+07:00', a.expires)
        self.assertIn('19:00:00', a.expires)
        for invalid in (None, '', 'oops', '2026-99-99T00:00:00Z', '2026-10-18', '2026-10-18T12:00:00', 123, self.now.replace(tzinfo=None)):
            with self.subTest(value=invalid):
                view = expiry_display(invalid, now=self.now)
                self.assertEqual((view.tone, view.expires, view.remaining), ('neutral', 'Unknown', 'Unknown'))


class CustomerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.key = make_private_key()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.now = datetime(2026, 10, 18, 12, tzinfo=UTC)
        self.db = Database(self.root / 'test.sqlite3', clock=lambda: self.now)
        self.db.initialize()
        self.customers = CustomerService(self.db)
        path = self.root / 'test.png'
        path.write_bytes(PNG)
        self.orders = SubscriptionService(self.db, 1, config=SubscriptionConfig(99_000, 12, 30, 'Test', 'Test', '000', path))

    def company(self, text, **changes):
        return self.customers.update_owned_company(**(dict(channel='telegram', user_id=22, actor_user_id=22, chat_type='private', company=text) | changes))

    def test_validation_ownership_channel_identity_and_admin_persistence(self):
        self.customers.ensure('telegram', 22, 'Original')
        for text in ('bad\n', '\ttab', 'zero\x00', '\x7f', '\u200b', 'a' * 151):
            with self.assertRaises(CustomerValidationError): self.company(text)
        for changes in ({'actor_user_id': 23}, {'chat_type': 'group'}, {'channel': 'other'}):
            with self.assertRaises(CustomerValidationError): self.company('Wrong', **changes)
        saved = self.company('  Example Company  ')
        self.assertEqual(saved['company'], 'Example Company')
        self.assertEqual(saved['display_name'], 'Original')
        self.customers.ensure('telegram', 22, 'Updated Telegram name')
        self.assertEqual(self.customers.customers.get('telegram', 22)['display_name'], 'Original')
        self.now += timedelta(minutes=1)
        edited = self.customers.edit_admin(channel='telegram', user_id=22, display_name='  Admin Name ', company='  New Company ')
        reopened = CustomerService(Database(self.db.path))
        self.assertEqual(reopened.customers.get('telegram', 22), edited)
        self.assertNotEqual(edited['created_at'], edited['updated_at'])
        self.assertEqual(reopened.customers.list_page(search='New Company').total, 1)
        self.assertEqual(reopened.customers.list_page(search="%' OR 1=1 --").total, 0)
        self.customers.ensure('other', 22, 'Different channel')
        self.assertEqual(self.customers.customers.list_page().total, 2)
        with self.assertRaises(CustomerValidationError):
            self.customers.edit_admin(channel='telegram', user_id=22, display_name='', company='valid')
        self.assertEqual(validate_metadata(' '), '')

    def test_profile_edit_preserves_order_approval_snapshot_payload_and_license_ownership(self):
        self.customers.ensure('telegram', 22, 'Original')
        self.company('Old Company')
        order = self.orders.checkout(**OWNER, customer='Telegram Name', device_id=DEVICE_ID, months=1)
        self.assertEqual((order['customer'], order['company']), ('Original', 'Old Company'))
        _, nonce = self.orders.review_payment(order['order_id'], **ADMIN)
        paid = self.orders.confirm_payment(nonce, **ADMIN)
        issuance = IssuanceService(LicenseService(self.key), self.db)
        approvals = ApprovalService(issuance)
        selected = approvals.select(paid['request_id'], admin_id=1, chat_id=1)
        preview = approvals.prepare(selected, admin_id=1, chat_id=1, days=30)
        with self.db.connection() as connection:
            form = dict(connection.execute('SELECT * FROM approval_forms WHERE form_id = ?', (preview['form_id'],)).fetchone())
        self.customers.edit_admin(channel='telegram', user_id=22, display_name='Admin Edited', company='Current Company')
        self.assertEqual(self.orders.orders.get(order['order_id']), paid)
        with self.db.connection() as connection:
            self.assertEqual(dict(connection.execute('SELECT * FROM approval_forms WHERE form_id = ?', (preview['form_id'],)).fetchone()), form)
        request, saved = approvals.confirm(preview['form_id'], admin_id=1, chat_id=1)
        saved.pop('_delivery_version', None)
        self.assertEqual(saved['payload']['customer'], 'Original')
        self.assertNotIn('company', saved['payload'])
        self.assertEqual(LicenseRepository(self.db).get_details(saved['license_id'])['company'], 'Current Company')
        self.customers.edit_admin(channel='telegram', user_id=22, display_name='More Edits', company='Latest Company')
        self.assertEqual(LicenseRepository(self.db).get(saved['license_id']), saved)
        manual = issuance.issue_manual(device_id=DEVICE_ID, customer='Same machine', edition='Professional', days=30, issuer=22, source='telegram_manual')
        self.assertEqual(LicenseRepository(self.db).get_details(manual['license_id'])['company'], '—')
        new = self.orders.checkout(**OWNER, customer='Telegram Name', device_id=DEVICE_ID, months=2)
        self.assertEqual((new['customer'], new['company']), ('More Edits', 'Latest Company'))
        self.assertEqual(order['company'], 'Old Company')

    def test_migration_from_v3_backfills_profiles_without_rewriting_orders_or_licenses(self):
        db = Database(self.root / 'legacy.sqlite3')
        with db.connection() as connection:
            for filename in ('001_initial.sql', '002_reliable_workflow.sql', '003_subscriptions.sql'):
                connection.executescript((REPOSITORY_ROOT / 'storage/migrations' / filename).read_text(encoding='utf-8'))
        req = RequestRepository(db).create_or_get_pending(channel='telegram', recipient_id=222, user_id=22, customer='Legacy', device_id=DEVICE_ID)
        issued = LicenseService(self.key).issue_activation(DEVICE_ID, 'Legacy', 'Professional', 30)
        with db.transaction() as connection:
            saved = LicenseRepository(db).add(issued, days=30, issuer=1, source='telegram_request', request_id=req['request_id'], connection=connection)
            connection.execute('''INSERT INTO subscription_orders
                (order_id, reference, user_id, recipient_id, customer, device_id, edition, months, purchased_days,
                 unit_price_vnd, total_vnd, currency, payment_details_json, qr_image, payment_status, created_at, updated_at)
                VALUES ('legacy-order', 'LEGACY', '22', '222', 'Legacy', ?, 'Professional', 1, 30, 99000, 99000,
                    'VND', '{}', ?, 'awaiting_payment', ?, ?)''', (DEVICE_ID, PNG, self.now.isoformat(), self.now.isoformat()))
        original_order = SubscriptionRepository(db).get('legacy-order')
        with patch('storage.database.Path.read_text', return_value='CREATE TABLE half_done (id INTEGER); INVALID SQL;'):
            with self.assertRaises(StorageError): db.initialize()
        with db.connection() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 3)
        db.initialize()
        db.initialize()
        self.assertEqual(LicenseRepository(db).get(saved['license_id']), saved)
        profile = CustomerService(db).customers.get('telegram', 22)
        self.assertEqual((profile['display_name'], profile['company']), ('Legacy', ''))
        self.assertEqual(LicenseRepository(db).get_details(saved['license_id'])['company'], '—')
        preserved_order = SubscriptionRepository(db).get('legacy-order')
        self.assertEqual(preserved_order.pop('company'), '')
        self.assertEqual(preserved_order, original_order)
        self.assertEqual(SubscriptionRepository(db).list_page().records[0]['company'], '—')
        with db.connection() as connection:
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])


class CompanyTelegramTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = Database(self.root / 'test.sqlite3')
        self.db.initialize()
        self.customers = CustomerService(self.db)
        self.customers.ensure('telegram', 22, 'Name')
        self.customers.update_owned_company(channel='telegram', user_id=22, actor_user_id=22, chat_type='private', company='Saved Company')
        path = self.root / 'test.png'
        path.write_bytes(PNG)
        self.orders = SubscriptionService(self.db, 1, config=SubscriptionConfig(99_000, 12, 30, 'Test', 'Test', '000', path))
        self.context = SimpleNamespace(application=SimpleNamespace(bot_data={'customer_service': self.customers, 'subscription_service': self.orders}), user_data={'subscription_form': {'step': 'device'}})
        self.message = SimpleNamespace(text='', reply_text=AsyncMock(), reply_photo=AsyncMock())
        self.update = SimpleNamespace(effective_message=self.message, effective_user=SimpleNamespace(id=22, full_name='Name'), effective_chat=SimpleNamespace(id=222, type='private'), callback_query=None)

    def callback(self, action, nonce=None):
        nonce = nonce or self.context.user_data['subscription_form']['company_nonce']
        self.update.callback_query = SimpleNamespace(from_user=self.update.effective_user, data=f'company:{action}:{nonce}', answer=AsyncMock())

    async def test_saved_change_skip_and_stale_company_callbacks(self):
        await customer.offer_company(self.update, self.context, DEVICE_ID)
        nonce = self.context.user_data['subscription_form']['company_nonce']
        self.callback('use')
        await customer.company_action(self.update, self.context)
        self.assertEqual(self.context.user_data['subscription_form']['company'], 'Saved Company')
        self.assertEqual(self.context.user_data['subscription_form']['step'], 'months')
        self.callback('skip', nonce)
        await customer.company_action(self.update, self.context)
        self.assertEqual(self.context.user_data['subscription_form']['company'], 'Saved Company')
        await customer.offer_company(self.update, self.context, DEVICE_ID)
        self.callback('change')
        await customer.company_action(self.update, self.context)
        self.message.text = '  New Company  '
        await customer.receive_company(self.update, self.context)
        self.assertEqual(self.customers.customers.get('telegram', 22)['company'], 'New Company')
        await customer.offer_company(self.update, self.context, DEVICE_ID)
        self.callback('skip')
        await customer.company_action(self.update, self.context)
        self.assertEqual(self.customers.customers.get('telegram', 22)['company'], 'New Company')
        self.assertEqual(self.context.user_data['subscription_form']['company'], '')

    async def test_owner_private_checks_and_caption_limits(self):
        await customer.offer_company(self.update, self.context, DEVICE_ID)
        self.update.effective_user = SimpleNamespace(id=23)
        self.callback('use')
        await customer.company_action(self.update, self.context)
        self.assertEqual(self.context.user_data['subscription_form']['step'], 'company')
        self.update.effective_user = SimpleNamespace(id=22)
        self.update.effective_chat.type = 'group'
        self.callback('skip')
        await customer.company_action(self.update, self.context)
        self.assertEqual(self.context.user_data['subscription_form']['step'], 'company')
        self.update.effective_chat.type = 'private'
        order = self.orders.checkout(**OWNER, customer='Name', device_id=DEVICE_ID, months=2)
        order['company'] = '🏢' * 150
        order['payment_details']['bank'] = 'Test Bank ' * 50
        await subscription.show_customer(self.update, order)
        caption = self.message.reply_photo.await_args.kwargs['caption']
        self.assertLessEqual(len(caption.encode('utf-16-le')) // 2, 1024)
        self.assertIn('Company: ' + order['company'], self.message.reply_text.await_args.args[0])

    async def test_order_snapshot_company_in_admin_notification_and_payment_review(self):
        order = self.orders.checkout(**OWNER, customer='Name', device_id=DEVICE_ID, months=1)
        self.customers.edit_admin(channel='telegram', user_id=22, display_name='Changed', company='Current Company')
        order = self.orders.report_payment(order['order_id'], **OWNER)
        self.context.bot = SimpleNamespace(send_message=AsyncMock())
        await subscription.notify_admin(self.context, order)
        self.assertIn('Company: Saved Company', self.context.bot.send_message.await_args.kwargs['text'])
        self.update.effective_user = SimpleNamespace(id=1)
        self.update.effective_chat.id = 1
        self.update.callback_query = SimpleNamespace(from_user=self.update.effective_user,
            data='order:review:' + order['order_id'], answer=AsyncMock())
        await subscription.order_action(self.update, self.context)
        self.assertIn('Company: Saved Company', self.message.reply_text.await_args.args[0])


if __name__ == '__main__': unittest.main()
