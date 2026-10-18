"""Immutable issued tokens and their exact canonical payloads."""

import json

from core.crypto.activation import canonical_payload_bytes
from storage.database import Database
from storage.repositories.pagination import query_page, search_pattern


SUMMARY_SELECT = """SELECT l.license_id, l.request_id, l.edition, l.duration_days, l.issued_at, l.expires_at, l.issuer, l.source,
    json_extract(l.payload_json, '$.customer') AS customer,
    json_extract(l.payload_json, '$.device_id') AS device_id,
    coalesce(nullif(c.company, ''), '—') AS company
    FROM licenses l LEFT JOIN requests r ON r.request_id = l.request_id
    LEFT JOIN customer_profiles c ON c.user_id = r.user_id AND c.channel =
        CASE WHEN r.channel = 'telegram_subscription' THEN 'telegram' ELSE r.channel END"""


class LicenseRepository:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _record(row):
        if row is None:
            return None
        record = dict(row)
        record["payload"] = json.loads(record["payload_json"])
        return record

    def list_page(self, *, search="", page=0, size=25, sort="issued_at", descending=True):
        where = "(json_extract(l.payload_json, '$.customer') LIKE ? ESCAPE '\\' OR json_extract(l.payload_json, '$.device_id') LIKE ? ESCAPE '\\' OR l.license_id LIKE ? ESCAPE '\\' OR coalesce(l.request_id, '') LIKE ? ESCAPE '\\')"
        pattern = search_pattern(search)
        sorts = {name: 'l.' + name for name in ("license_id", "edition", "issued_at", "expires_at", "source", "request_id")}
        sorts.update(customer='customer', device_id='device_id', remaining='l.expires_at')
        with self.database.connection() as connection:
            return query_page(connection, SUMMARY_SELECT, where, [pattern] * 4, sorts=sorts, sort=sort,
                              descending=descending, page=page, size=size)

    def get_details(self, license_id):
        with self.database.connection() as connection:
            row = connection.execute(SUMMARY_SELECT + " WHERE l.license_id = ?", (license_id,)).fetchone()
            return dict(row) if row else None

    def get(self, license_id, *, connection=None):
        if connection is None:
            with self.database.connection() as connection:
                return self.get(license_id, connection=connection)
        return self._record(connection.execute(
            "SELECT * FROM licenses WHERE license_id = ?", (license_id,),
        ).fetchone())

    def for_request(self, request_id, *, connection=None):
        if connection is None:
            with self.database.connection() as connection:
                return self.for_request(request_id, connection=connection)
        return self._record(connection.execute(
            "SELECT * FROM licenses WHERE request_id = ?", (request_id,),
        ).fetchone())

    def add(self, issued, *, days, issuer, source, request_id=None, connection):
        payload = issued.payload
        connection.execute(
            """INSERT INTO licenses
               (license_id, request_id, activation_key, payload_json, edition,
                duration_days, issued_at, expires_at, issuer, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (payload["license_id"], request_id, issued.activation_key,
             canonical_payload_bytes(payload).decode("utf-8"), payload["edition"], days,
             payload["issued_at"], payload["expires_at"], str(issuer), source),
        )
        return self.get(payload["license_id"], connection=connection)
