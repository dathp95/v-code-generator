"""
Generate V-CODE machine-bound Activation Keys.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


ACTIVATION_VERSION = 1
DEVICE_ID_RE = re.compile(r"^VC-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}$")
DEFAULT_OUTPUT_DIR = Path("output")


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
    normalized = device_id.strip().upper()
    if DEVICE_ID_RE.fullmatch(normalized) is None:
        raise ValueError("device_id must match VC-XXXX-XXXX-XXXX-XXXX-XXXX using uppercase hex groups.")
    return normalized


def validate_non_empty(name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty text.")
    return normalized


def validate_days(days: int) -> int:
    if days <= 0:
        raise ValueError("days must be a positive integer.")
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


def load_private_key(path: Path, passphrase: bytes) -> RSAPrivateKey:
    try:
        key = serialization.load_pem_private_key(
            path.expanduser().read_bytes(),
            password=passphrase,
        )
    except Exception as error:
        raise ValueError("Unable to load encrypted private key.") from error

    if not isinstance(key, RSAPrivateKey):
        raise ValueError("Private key must be an RSA private key.")
    return key


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


def generate_activation_key(
    *,
    device_id: str,
    customer: str,
    edition: str,
    days: int,
    private_key: RSAPrivateKey,
    issued_at: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    payload = create_activation_payload(
        device_id=device_id,
        customer=customer,
        edition=edition,
        days=days,
        issued_at=issued_at,
    )
    return create_activation_token(payload, private_key), payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a V-CODE Activation Key for one Device ID.",
    )
    parser.add_argument("--device-id", required=True, help="Machine Device ID from V-CODE License Support.")
    parser.add_argument("--customer", required=True, help="Customer name.")
    parser.add_argument("--edition", required=True, help="License edition, for example Professional.")
    parser.add_argument("--days", required=True, type=int, help="Activation duration in days.")
    parser.add_argument("--private-key", required=True, type=Path, help="Encrypted private key PEM path.")
    parser.add_argument(
        "--output-file",
        type=Path,
        help="Optional ignored output file for the Activation Key text.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        payload = create_activation_payload(
            device_id=args.device_id,
            customer=args.customer,
            edition=args.edition,
            days=args.days,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error

    passphrase = getpass.getpass("Private key passphrase: ").encode("utf-8")
    try:
        private_key = load_private_key(args.private_key, passphrase)
        activation_key = create_activation_token(payload, private_key)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    print("Activation Key:")
    print(activation_key)
    print()
    print(f"License ID : {payload['license_id']}")
    print(f"Issued UTC : {payload['issued_at']}")
    print(f"Expires UTC: {payload['expires_at']}")

    if args.output_file:
        output_file = args.output_file.expanduser()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(activation_key + "\n", encoding="utf-8")
        print(f"Saved to   : {output_file.resolve()}")


if __name__ == "__main__":
    main()
