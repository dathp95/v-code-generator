"""Immutable issued tokens and their exact canonical payloads."""

import json

from core.crypto.activation import canonical_payload_bytes
from storage.database import Database


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
