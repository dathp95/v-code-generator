"""Order reads and metadata pagination; mutations live in SubscriptionService."""
import json
from storage.repositories.pagination import query_page, search_pattern

STATUSES = ('awaiting_payment', 'payment_reported', 'paid', 'cancelled')


class SubscriptionRepository:
    def __init__(self, database):
        self.database = database

    def get(self, order_id, *, connection=None):
        if connection is None:
            with self.database.connection() as connection:
                return self.get(order_id, connection=connection)
        row = connection.execute('SELECT * FROM subscription_orders WHERE order_id = ?', (order_id,)).fetchone()
        if not row:
            return None
        record = dict(row)
        record['payment_details'] = json.loads(record.pop('payment_details_json'))
        return record

    def for_request(self, request_id, *, connection=None):
        if connection is None:
            with self.database.connection() as connection:
                return self.for_request(request_id, connection=connection)
        row = connection.execute('SELECT order_id FROM subscription_orders WHERE request_id = ?', (request_id,)).fetchone()
        return self.get(row['order_id'], connection=connection) if row else None

    def unpaid(self, user_id, recipient_id, device_id, *, connection=None):
        if connection is None:
            with self.database.connection() as connection:
                return self.unpaid(user_id, recipient_id, device_id, connection=connection)
        row = connection.execute("""SELECT order_id FROM subscription_orders WHERE user_id = ? AND recipient_id = ?
            AND device_id = ? AND payment_status IN ('awaiting_payment', 'payment_reported')""",
            (str(user_id), str(recipient_id), device_id)).fetchone()
        return self.get(row['order_id'], connection=connection) if row else None

    def list_page(self, *, search='', status='all', page=0, size=25, sort='created_at', descending=True, user_id=None, recipient_id=None):
        if status not in ('all', *STATUSES):
            raise ValueError('Unsupported payment status.')
        select = """SELECT o.order_id, o.reference, o.customer, o.device_id, o.edition, o.months,
            o.purchased_days, o.unit_price_vnd, o.total_vnd, o.currency, o.payment_status,
            o.user_id, o.recipient_id, o.created_at, o.updated_at, o.reported_at, o.paid_at,
            o.confirmed_by, o.cancelled_at, o.request_id, o.notification_status,
            l.license_id, l.duration_days AS issued_days,
            (SELECT d.status FROM deliveries d WHERE d.license_id = l.license_id
             AND d.purpose = 'customer_license' ORDER BY d.delivery_id DESC LIMIT 1) AS delivery_status
            FROM subscription_orders o LEFT JOIN licenses l ON l.request_id = o.request_id"""
        pattern = search_pattern(search)
        where = "(o.reference LIKE ? ESCAPE '\\' OR o.customer LIKE ? ESCAPE '\\' OR o.device_id LIKE ? ESCAPE '\\' OR o.order_id LIKE ? ESCAPE '\\')"
        params = [pattern] * 4
        if status != 'all':
            where += ' AND o.payment_status = ?'
            params.append(status)
        if user_id is not None:
            where += ' AND o.user_id = ? AND o.recipient_id = ?'
            params.extend([str(user_id), str(recipient_id)])
        sorts = {key: 'o.' + key for key in ('created_at', 'reference', 'customer', 'device_id', 'months', 'total_vnd', 'payment_status', 'request_id', 'order_id')}
        sorts.update(license_id='l.license_id', delivery_status='delivery_status')
        with self.database.connection() as connection:
            return query_page(connection, select, where, params, sorts=sorts, sort=sort,
                              descending=descending, page=page, size=size)


def require_subscription_paid(connection, request):
    """All request issuance paths use this gate within their existing transaction."""
    from services.issuance_service import RequestUnavailable
    row = connection.execute('SELECT * FROM subscription_orders WHERE request_id = ?', (request['request_id'],)).fetchone()
    if row is None and request['channel'] != 'telegram_subscription':
        return None
    if row is None or row['payment_status'] != 'paid':
        raise RequestUnavailable('Đơn đăng ký chưa được Admin xác nhận nhận tiền. Chưa thể cấp key.')
    if (row['user_id'], row['recipient_id'], row['device_id'], row['customer']) != (
            request['user_id'], request['recipient_id'], request['device_id'], request['customer']):
        raise RequestUnavailable('Thông tin đơn và yêu cầu không khớp. Chưa thể cấp key.')
    return dict(row)
