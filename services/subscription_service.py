"""Manual-payment order rules. No Telegram, signing, or network calls."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import import_module
import json
from pathlib import Path
from uuid import uuid4

from core.crypto.activation import validate_device_id, validate_non_empty
from storage.database import REPOSITORY_ROOT
from storage.repositories.subscription_repository import SubscriptionRepository


class SubscriptionError(ValueError):
    """Safe, customer-readable order error."""


@dataclass(frozen=True)
class SubscriptionConfig:
    monthly_price: int
    max_months: int
    days_per_month: int
    bank: str
    account_name: str
    account_number: str
    qr_path: Path

    @classmethod
    def load(cls):
        try:
            local = import_module('config.subscription')
            path = Path(local.PAYMENT_QR_PATH)
            return cls(local.MONTHLY_PRICE_VND, local.MAX_SUBSCRIPTION_MONTHS, local.DAYS_PER_MONTH,
                       local.BANK_NAME, local.BANK_ACCOUNT_NAME, local.BANK_ACCOUNT_NUMBER,
                       path if path.is_absolute() else REPOSITORY_ROOT / path)
        except (ImportError, AttributeError, TypeError, ValueError):
            raise SubscriptionError('Chưa cấu hình thanh toán: Admin cần tạo config/subscription.py theo file mẫu.') from None

    def validate(self):
        if (any(type(value) is not int or value <= 0 for value in (self.monthly_price, self.max_months, self.days_per_month))
                or self.max_months * self.days_per_month > 3650
                or any(not isinstance(value, str) or not value.strip() or value in ('YOUR_BANK', 'ACCOUNT_HOLDER', 'ACCOUNT_NUMBER')
                       for value in (self.bank, self.account_name, self.account_number))):
            raise SubscriptionError('Cấu hình giá/thời hạn/ngân hàng chưa hợp lệ. Chưa thể tạo đơn mới.')

    def qr_bytes(self):
        self.validate()
        try:
            value = self.qr_path.read_bytes()
            if not value.startswith(b'\x89PNG\r\n\x1a\n') or len(value) > 10_000_000:
                raise ValueError
            return value
        except (OSError, ValueError):
            raise SubscriptionError('Thiếu hoặc sai ảnh QR thanh toán. Admin cần đặt ảnh QR được cung cấp tại PAYMENT_QR_PATH; checkout mới đang tắt.') from None


class SubscriptionService:
    def __init__(self, database, admin_id, *, config=None):
        self.database, self.admin_id, self.config = database, str(admin_id), config
        self.orders = SubscriptionRepository(database)

    def configuration(self):
        return self.config or SubscriptionConfig.load()

    def _private(self, chat_type):
        if chat_type != 'private':
            raise SubscriptionError('Chỉ sử dụng đơn thanh toán trong chat riêng.')

    def _admin(self, user_id, chat_type):
        self._private(chat_type)
        if str(user_id) != self.admin_id:
            raise SubscriptionError('Chỉ Admin được xác nhận đã nhận tiền.')

    def owned(self, order_id, *, user_id, chat_id, chat_type, connection=None):
        self._private(chat_type)
        order = self.orders.get(order_id, connection=connection)
        if not order or (order['user_id'], order['recipient_id']) != (str(user_id), str(chat_id)):
            raise SubscriptionError('Không tìm thấy đơn của bạn trong chat này.')
        return order

    def checkout(self, *, user_id, chat_id, customer, device_id, months, chat_type):
        self._private(chat_type)
        device_id = validate_device_id(device_id)
        customer = validate_non_empty('customer', customer)
        with self.database.transaction() as connection:
            existing = self.orders.unpaid(user_id, chat_id, device_id, connection=connection)
            if existing:
                return existing
            config = self.configuration()
            config.validate()
            if type(months) is not int or not 1 <= months <= config.max_months:
                raise SubscriptionError(f'Nhập số tháng nguyên từ 1 đến {config.max_months}.')
            qr = config.qr_bytes()
            order_id = uuid4().hex
            reference = 'VC' + order_id.upper()
            details = dict(bank=config.bank, account_name=config.account_name, account_number=config.account_number,
                           transfer_reference=reference, days_per_month=config.days_per_month)
            now = self.database.timestamp()
            connection.execute("""INSERT INTO subscription_orders
                (order_id, reference, user_id, recipient_id, customer, device_id, edition, months,
                 purchased_days, unit_price_vnd, total_vnd, currency, payment_details_json, qr_image,
                 payment_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'Professional', ?, ?, ?, ?, 'VND', ?, ?, 'awaiting_payment', ?, ?)""",
                (order_id, reference, str(user_id), str(chat_id), customer, device_id, months,
                 months * config.days_per_month, config.monthly_price, months * config.monthly_price,
                 json.dumps(details, ensure_ascii=False, sort_keys=True), qr, now, now))
            return self.orders.get(order_id, connection=connection)

    def report_payment(self, order_id, **identity):
        with self.database.transaction() as connection:
            order = self.owned(order_id, connection=connection, **identity)
            if order['payment_status'] == 'cancelled':
                raise SubscriptionError('Đơn đã hủy. Không báo chuyển khoản cho đơn này.')
            if order['payment_status'] == 'awaiting_payment':
                now = self.database.timestamp()
                connection.execute("""UPDATE subscription_orders SET payment_status = 'payment_reported',
                    reported_at = ?, updated_at = ? WHERE order_id = ?""", (now, now, order_id))
            return self.orders.get(order_id, connection=connection)

    def cancel(self, order_id, **identity):
        with self.database.transaction() as connection:
            order = self.owned(order_id, connection=connection, **identity)
            if order['payment_status'] == 'paid':
                raise SubscriptionError('Đơn đã xác nhận nhận tiền; khách không thể hủy.')
            if order['payment_status'] != 'cancelled':
                now = self.database.timestamp()
                connection.execute("UPDATE subscription_orders SET payment_status = 'cancelled', cancelled_at = ?, updated_at = ? WHERE order_id = ?", (now, now, order_id))
            return self.orders.get(order_id, connection=connection)

    def review_payment(self, order_id, *, admin_id, chat_id, chat_type):
        self._admin(admin_id, chat_type)
        with self.database.transaction() as connection:
            order = self.orders.get(order_id, connection=connection)
            if not order or order['payment_status'] == 'cancelled':
                raise SubscriptionError('Đơn không còn hợp lệ hoặc đã hủy.')
            # Selecting a different payment review invalidates older buttons.
            now = self.database.timestamp()
            connection.execute('UPDATE payment_confirmations SET consumed_at = ? WHERE admin_id = ? AND chat_id = ? AND consumed_at IS NULL', (now, str(admin_id), str(chat_id)))
            nonce = uuid4().hex
            connection.execute('INSERT INTO payment_confirmations (confirmation_id, order_id, admin_id, chat_id, expires_at) VALUES (?, ?, ?, ?, ?)',
                (nonce, order_id, str(admin_id), str(chat_id), (self.database.now() + timedelta(minutes=10)).isoformat()))
            return order, nonce

    def confirm_payment(self, nonce, *, admin_id, chat_id, chat_type):
        self._admin(admin_id, chat_type)
        with self.database.transaction() as connection:
            form = connection.execute('SELECT * FROM payment_confirmations WHERE confirmation_id = ? AND admin_id = ? AND chat_id = ?', (nonce, str(admin_id), str(chat_id))).fetchone()
            if form is None:
                raise SubscriptionError('Xác nhận thanh toán không hợp lệ. Hãy xem lại đơn.')
            order = self.orders.get(form['order_id'], connection=connection)
            if order['payment_status'] == 'paid' and form['consumed_at'] is not None:
                return order
            if form['consumed_at'] or datetime.fromisoformat(form['expires_at']) <= self.database.now():
                raise SubscriptionError('Xác nhận đã cũ/hết hạn. Hãy xem lại đơn.')
            if order['payment_status'] == 'cancelled':
                raise SubscriptionError('Đơn đã hủy. Không thể xác nhận nhận tiền.')
            now = self.database.timestamp()
            connection.execute('UPDATE payment_confirmations SET consumed_at = ? WHERE confirmation_id = ?', (now, nonce))
            if order['payment_status'] != 'paid':
                request_id = uuid4().hex
                connection.execute("""INSERT INTO requests (request_id, channel, recipient_id, user_id, customer,
                    device_id, status, created_at, updated_at) VALUES (?, 'telegram_subscription', ?, ?, ?, ?, 'pending', ?, ?)""",
                    (request_id, order['recipient_id'], order['user_id'], order['customer'], order['device_id'], now, now))
                connection.execute("""UPDATE subscription_orders SET payment_status = 'paid', paid_at = ?,
                    confirmed_by = ?, request_id = ?, updated_at = ? WHERE order_id = ?""", (now, str(admin_id), request_id, now, order['order_id']))
            return self.orders.get(order['order_id'], connection=connection)

    def cancel_review(self, nonce, *, admin_id, chat_id, chat_type):
        self._admin(admin_id, chat_type)
        with self.database.transaction() as connection:
            connection.execute('UPDATE payment_confirmations SET consumed_at = ? WHERE confirmation_id = ? AND admin_id = ? AND chat_id = ? AND consumed_at IS NULL',
                               (self.database.timestamp(), nonce, str(admin_id), str(chat_id)))

    def claim_notification(self, order_id):
        # Claim once before sending. An uncertain/crashed notification is inspected via /orders,
        # never retried automatically by repeated customer clicks.
        with self.database.transaction() as connection:
            order = self.orders.get(order_id, connection=connection)
            if not order or order['payment_status'] != 'payment_reported' or order['notification_status']:
                return None
            owner = uuid4().hex
            connection.execute("UPDATE subscription_orders SET notification_status = 'attempting', notification_claim = ?, notification_at = ? WHERE order_id = ?", (owner, self.database.timestamp(), order_id))
            return owner

    def finish_notification(self, order_id, owner, status):
        if status not in ('sent', 'unknown', 'failed'):
            raise SubscriptionError('Trạng thái thông báo không hợp lệ.')
        with self.database.transaction() as connection:
            connection.execute("""UPDATE subscription_orders SET notification_status = ?, notification_finished_at = ?
                WHERE order_id = ? AND notification_claim = ? AND notification_status = 'attempting'""",
                (status, self.database.timestamp(), order_id, owner))

    def recover_notifications(self):
        with self.database.transaction() as connection:
            connection.execute("""UPDATE subscription_orders SET notification_status = 'unknown', notification_finished_at = ?
                WHERE notification_status = 'attempting' AND notification_at <= ?""",
                (self.database.timestamp(), (self.database.now() - timedelta(seconds=120)).isoformat().replace('+00:00', 'Z')))
