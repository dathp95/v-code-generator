"""UTC remains in storage; Qt applies the system timezone only for display."""

from PySide6.QtCore import QDateTime, QTimeZone, Qt


def format_local(value, zone=None):
    if not value:
        return "—"
    parsed = QDateTime.fromString(str(value), Qt.DateFormat.ISODateWithMs)
    if not parsed.isValid():
        return "Invalid timestamp"
    return parsed.toTimeZone(zone or QTimeZone.systemTimeZone()).toString("dd MMM yyyy  HH:mm:ss")


def timezone_label(zone=None):
    zone = zone or QTimeZone.systemTimeZone()
    name = bytes(zone.id()).decode("utf-8")
    seconds = zone.offsetFromUtc(QDateTime.currentDateTimeUtc())
    sign = "+" if seconds >= 0 else "−"
    hours, minutes = divmod(abs(seconds) // 60, 60)
    return f"{name} (UTC{sign}{hours:02d}:{minutes:02d})"
