"""UTC interval calculations independent of Qt, SQLite and formatted date strings."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class ExpiryDisplay:
    expires: str
    remaining: str
    status: str
    tone: str


def expiry_display(value, *, now=None, zone=None):
    unknown = ExpiryDisplay('Unknown', 'Unknown', 'Unknown', 'neutral')
    try:
        expires = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
        current = now if now is not None else datetime.now(UTC)
        if (not isinstance(expires, datetime) or expires.tzinfo is None or expires.utcoffset() is None
                or current.tzinfo is None or current.utcoffset() is None):
            return unknown
        expires = expires.astimezone(UTC)
        remaining = expires - current.astimezone(UTC)
        local = expires.astimezone(zone)
        offset = local.strftime('%z')
        timestamp = local.strftime('%d %b %Y  %H:%M:%S ') + f"UTC{offset[:3]}:{offset[3:]}"
        if remaining <= timedelta(0):
            return ExpiryDisplay(timestamp, 'Expired / Đã hết hạn', 'Expired / Đã hết hạn', 'red')
        minutes = remaining // timedelta(minutes=1)
        days, minutes = divmod(minutes, 1440)
        hours, minutes = divmod(minutes, 60)
        soon = remaining < timedelta(days=3)
        status = 'Expiring soon (<72h)' if soon else 'Valid (≥72h)'
        return ExpiryDisplay(timestamp, f'{days}d {hours}h {minutes}m · {status}', status, 'red' if soon else 'green')
    except (ValueError, TypeError, OverflowError, AttributeError):
        return unknown
