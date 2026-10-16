"""V-CODE payload construction and RSA-PSS-SHA256 token signing."""

from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from core.exceptions import ActivationValidationError


ACTIVATION_VERSION = 1
DEVICE_ID_RE = re.compile(r"^VC-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}$")


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def format_utc(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_device_id(device_id: str) -> str:
    if not isinstance(device_id, str):
        raise ActivationValidationError("device_id must be text.")
    normalized = device_id.strip().upper()
    if DEVICE_ID_RE.fullmatch(normalized) is None:
        raise ActivationValidationError("device_id must match VC-XXXX-XXXX-XXXX-XXXX-XXXX using uppercase hex groups.")
    return normalized


def validate_non_empty(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ActivationValidationError(f"{name} must be non-empty text.")
    normalized = value.strip()
    if not normalized:
        raise ActivationValidationError(f"{name} must be non-empty text.")
    return normalized


def validate_days(days: int) -> int:
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise ActivationValidationError("days must be a positive integer.")
    return days


def create_activation_payload(
    *,
    device_id: str,
    customer: str,
    edition: str,
    days: int,
    issued_at: datetime | None = None,
    license_id: str | None = None,
) -> dict[str, Any]:
    issued_at = (issued_at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    expires_at = issued_at + timedelta(days=validate_days(days))
    return {
        "version": ACTIVATION_VERSION,
        "license_id": license_id or str(uuid.uuid4()),
        "device_id": validate_device_id(device_id),
        "customer": validate_non_empty("customer", customer),
        "edition": validate_non_empty("edition", edition),
        "issued_at": format_utc(issued_at),
        "expires_at": format_utc(expires_at),
    }


def create_activation_token(payload: dict[str, Any], private_key: RSAPrivateKey) -> str:
    payload_bytes = canonical_payload_bytes(payload)
    signature = private_key.sign(
        payload_bytes,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    return f"{b64url_encode(payload_bytes)}.{b64url_encode(signature)}"
