"""Read-only receipt queries. Callers own one explicit snapshot transaction."""
from storage.repositories.pagination import search_pattern
from datetime import UTC, datetime


def receipt_utc(value):
    """Canonical comparison retains microseconds; julianday rounds to milliseconds."""
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC).isoformat(timespec='microseconds')
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


class RevenueRepository:
    def __init__(self, database):
        self.database = database

    def prepare(self, connection):
        connection.create_function('receipt_utc', 1, receipt_utc, deterministic=True)

    @staticmethod
    def predicate(start_utc, end_utc, *, company_mode='all', company='', name='', reference=''):
        if company_mode not in ('all', 'unspecified', 'exact'):
            raise ValueError('Unsupported Company filter.')
        where = """o.payment_status = 'paid' AND receipt_utc(o.paid_at) >= ?
            AND receipt_utc(o.paid_at) < ?"""
        parameters = [start_utc.isoformat(timespec='microseconds'), end_utc.isoformat(timespec='microseconds')]
        if company_mode == 'unspecified':
            where += " AND trim(coalesce(o.company, '')) = ''"
        elif company_mode == 'exact':
            where += " AND trim(coalesce(o.company, '')) = ?"
            parameters.append(company.strip())
        if name.strip():
            where += " AND o.customer LIKE ? ESCAPE '\\'"
            parameters.append(search_pattern(name))
        if reference.strip():
            where += " AND o.reference LIKE ? ESCAPE '\\'"
            parameters.append(search_pattern(reference))
        return where, parameters

    def receipts(self, connection, start_utc, end_utc, **filters):
        where, parameters = self.predicate(start_utc, end_utc, **filters)
        # Request PK supplies channel identity only; never payment status or amount.
        # LEFT JOIN keeps the receipt as the unit of counting and cannot fan out.
        return connection.execute("""SELECT o.order_id,
            coalesce(CASE WHEN r.channel = 'telegram_subscription' THEN 'telegram' ELSE r.channel END, 'telegram') AS channel,
            o.user_id, o.total_vnd, o.paid_at FROM subscription_orders o
            LEFT JOIN requests r ON r.request_id = o.request_id WHERE """ + where, parameters)

    def companies(self, connection, start_utc, end_utc):
        where, parameters = self.predicate(start_utc, end_utc)
        rows = connection.execute("""SELECT DISTINCT trim(o.company) AS company
            FROM subscription_orders o WHERE """ + where + " AND trim(coalesce(o.company, '')) != '' ORDER BY company LIMIT 500", parameters)
        return tuple(row['company'] for row in rows)

    def details(self, connection, start_utc, end_utc, *, page=0, size=25, sort='paid_at', descending=True, **filters):
        sorts = {'paid_at': 'receipt_utc(o.paid_at)', 'reference': 'o.reference', 'customer': 'o.customer',
                 'company': 'o.company', 'months': 'o.months', 'total_vnd': 'o.total_vnd',
                 'confirmed_by': 'o.confirmed_by', 'request_id': 'o.request_id', 'license_id': 'license_id'}
        if sort not in sorts:
            raise ValueError('Unsupported receipt sort.')
        size = min(100, max(1, int(size)))
        where, parameters = self.predicate(start_utc, end_utc, **filters)
        total = connection.execute('SELECT count(*) FROM subscription_orders o WHERE ' + where, parameters).fetchone()[0]
        page = min(max(0, int(page)), max(0, (total - 1) // size))
        # UNIQUE licenses.request_id makes this scalar lookup unambiguous, with no fan-out.
        select = """SELECT o.order_id, o.reference, o.paid_at, o.customer,
            coalesce(nullif(trim(o.company), ''), '—') AS company,
            o.months, o.total_vnd, o.confirmed_by, o.request_id,
            (SELECT l.license_id FROM licenses l WHERE l.request_id = o.request_id) AS license_id
            FROM subscription_orders o WHERE """
        rows = connection.execute(select + where + ' ORDER BY ' + sorts[sort] + (' DESC' if descending else ' ASC') +
            ', o.order_id ASC LIMIT ? OFFSET ?', (*parameters, size, page * size)).fetchall()
        from storage.repositories.pagination import Page
        return Page([dict(row) for row in rows], total, page, size)
