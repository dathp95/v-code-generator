"""Receipt definitions, local calendar grouping and immutable report models."""
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config.reporting import REPORT_TIMEZONE
from storage.repositories.revenue_repository import RevenueRepository
from storage.repositories.pagination import Page

MAX_RANGE_DAYS = 1096
MAX_BUCKETS = 366


class RevenueError(ValueError):
    """Safe recoverable configuration/filter error, containing no raw SQL/input."""


@dataclass(frozen=True)
class RevenueFilters:
    start: date
    end: date
    grouping: str = 'day'
    company_mode: str = 'all'
    company: str = ''
    name: str = ''
    reference: str = ''


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    covered_start: date
    covered_end: date
    label: str
    partial: bool
    paid_orders: int
    unique_customers: int
    total_vnd: int


@dataclass(frozen=True)
class RevenueReport:
    filters: RevenueFilters
    timezone: str
    total_vnd: int
    paid_orders: int
    unique_customers: int
    periods: tuple[Period, ...]
    details: Page
    companies: tuple[str, ...]
    detail_period: date | None


def format_vnd(value):
    return f'{value:,}'.replace(',', '.') + ' VND'


def bucket_start(day, grouping):
    if grouping == 'day':
        return day
    if grouping == 'week':
        return day - timedelta(days=day.weekday())
    if grouping == 'month':
        return day.replace(day=1)
    raise RevenueError('Choose Day, Week or Month grouping.')


def next_bucket(day, grouping):
    if grouping == 'day':
        return day + timedelta(days=1)
    if grouping == 'week':
        return day + timedelta(days=7)
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


class RevenueService:
    def __init__(self, repository, *, timezone_name=REPORT_TIMEZONE, clock=None):
        self.repository = repository
        self.timezone_name = timezone_name
        self.clock = clock or (lambda: datetime.now(UTC))

    def zone(self):
        try:
            return ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise RevenueError('Report timezone unavailable. Check REPORT_TIMEZONE and install requirements-gui.txt (tzdata on Windows).') from None

    def defaults(self):
        today = self.clock().astimezone(self.zone()).date()
        return RevenueFilters(today.replace(day=1), next_bucket(today.replace(day=1), 'month') - timedelta(days=1))

    def boundaries(self, start, end):
        zone = self.zone()
        return (datetime.combine(start, time.min, zone).astimezone(UTC),
                datetime.combine(end + timedelta(days=1), time.min, zone).astimezone(UTC))

    def validate(self, filters):
        if (type(filters.start) is not date or type(filters.end) is not date or filters.start.year < 1900
                or filters.end.year > 9998 or filters.end < filters.start):
            raise RevenueError('Enter inclusive From/To dates in order, between 1900 and 9998.')
        if (filters.end - filters.start).days + 1 > MAX_RANGE_DAYS:
            raise RevenueError(f'Choose a date range of at most {MAX_RANGE_DAYS} days.')
        if filters.company_mode not in ('all', 'unspecified', 'exact'):
            raise RevenueError('Choose All, Unspecified or a Company snapshot.')
        if any(not isinstance(v, str) or len(v) > 200 for v in (filters.company, filters.name, filters.reference)):
            raise RevenueError('Search values must have at most 200 characters.')
        periods = []
        cursor = bucket_start(filters.start, filters.grouping)
        while cursor <= filters.end:
            end = next_bucket(cursor, filters.grouping) - timedelta(days=1)
            periods.append((cursor, end, max(cursor, filters.start), min(end, filters.end)))
            if len(periods) > MAX_BUCKETS:
                raise RevenueError(f'At most {MAX_BUCKETS} chart periods are allowed. Narrow the dates or choose Week/Month.')
            cursor = end + timedelta(days=1)
        return periods

    def report(self, filters, *, page=0, size=25, detail_period=None, sort='paid_at', descending=True):
        periods = self.validate(filters)
        zone = self.zone()
        start, end = self.boundaries(filters.start, filters.end)
        if detail_period is not None and detail_period not in {row[0] for row in periods}:
            raise RevenueError('Selected period is outside the applied report range.')
        query = dict(company_mode=filters.company_mode, company=filters.company, name=filters.name, reference=filters.reference)
        totals = {row[0]: [0, 0, set()] for row in periods}
        total_vnd, paid_orders, customers = 0, 0, set()
        with self.repository.database.connection() as connection:
            # Deferred read transaction: every report component sees the same committed snapshot.
            connection.execute('BEGIN')
            self.repository.prepare(connection)
            for receipt in self.repository.receipts(connection, start, end, **query):
                amount = receipt['total_vnd']
                if type(amount) is not int:
                    raise RevenueError('A receipt has an invalid stored VND amount. Review order data.')
                paid = datetime.fromisoformat(receipt['paid_at'].replace('Z', '+00:00'))
                local_day = paid.astimezone(zone).date()
                bucket = totals[bucket_start(local_day, filters.grouping)]
                identity = (receipt['channel'], receipt['user_id'])
                bucket[0] += amount
                bucket[1] += 1
                bucket[2].add(identity)
                total_vnd += amount
                paid_orders += 1
                customers.add(identity)
            result = []
            for first, last, covered_first, covered_last in periods:
                amount, count, identities = totals[first]
                partial = (first, last) != (covered_first, covered_last)
                label = first.isoformat() if filters.grouping == 'day' else first.strftime('%Y-%m') if filters.grouping == 'month' else f'{first.isoformat()} – {last.isoformat()}'
                if partial:
                    label += f' · partial ({covered_first:%d %b}–{covered_last:%d %b})'
                result.append(Period(first, last, covered_first, covered_last, label, partial, count, len(identities), amount))
            detail_start, detail_end = start, end
            if detail_period is not None:
                period = next(row for row in result if row.start == detail_period)
                detail_start, detail_end = self.boundaries(period.covered_start, period.covered_end)
            details = self.repository.details(connection, detail_start, detail_end, page=page, size=size, sort=sort, descending=descending, **query)
            for row in details.records:
                paid = datetime.fromisoformat(row['paid_at'].replace('Z', '+00:00')).astimezone(zone)
                row['paid_at_local'] = paid.strftime('%d/%m/%Y %H:%M:%S %z')
            companies = self.repository.companies(connection, start, end)
        return RevenueReport(filters, self.timezone_name, total_vnd, paid_orders, len(customers), tuple(result), details, companies, detail_period)
