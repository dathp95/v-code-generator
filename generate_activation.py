"""Generate V-CODE machine-bound Activation Keys.

The historical helper API remains available through re-exports below.
"""

from __future__ import annotations

import argparse
import getpass
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from core.crypto.activation import (
    ACTIVATION_VERSION, DEVICE_ID_RE, b64url_encode, canonical_payload_bytes,
    create_activation_payload, create_activation_token, format_utc,
    validate_days, validate_device_id, validate_non_empty,
)
from core.crypto.key_loader import load_private_key
from core.services.license_service import LicenseService
from services.issuance_service import IssuanceService
from storage.database import Database, StorageError
from storage.repositories.delivery_repository import DeliveryRepository


DEFAULT_OUTPUT_DIR = Path("output")


def generate_activation_key(
    *,
    device_id: str,
    customer: str,
    edition: str,
    days: int,
    private_key: RSAPrivateKey,
    issued_at: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    issued = LicenseService(private_key).issue_activation(
        device_id=device_id, customer=customer, edition=edition,
        days=days, issued_at=issued_at,
    )
    return issued.activation_key, issued.payload


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
        LicenseService.validate_inputs(args.device_id, args.customer, args.edition, args.days)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    passphrase = getpass.getpass("Private key passphrase: ").encode("utf-8")
    try:
        private_key = load_private_key(args.private_key, passphrase)
        database = Database()
        database.initialize()
        record = IssuanceService(LicenseService(private_key), database).issue_manual(
            device_id=args.device_id, customer=args.customer, edition=args.edition,
            days=args.days, issuer="local_admin", source="cli",
        )
        _output_activation(record, args.output_file, DeliveryRepository(database))
    except StorageError:
        raise SystemExit("License storage failed. Check database availability before retrying.") from None
    except ValueError as error:
        raise SystemExit(str(error)) from error


def _output_activation(record, output_path, deliveries):
    activation_key, payload = record["activation_key"], record["payload"]
    attempt = deliveries.start(
        license_id=record["license_id"], destination="stdout", purpose="cli_stdout",
    )
    try:
        _print_activation(activation_key, payload)
    except OSError:
        deliveries.finish(attempt, status="permanent_failed", error_category="output_io")
        raise SystemExit("Unable to write activation output.") from None
    deliveries.finish(attempt, status="succeeded")

    if output_path:
        output_file = output_path.expanduser()
        attempt = deliveries.start(
            license_id=record["license_id"], destination=str(output_file.resolve()), purpose="cli_file",
        )
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(activation_key + "\n", encoding="utf-8")
        except OSError:
            deliveries.finish(attempt, status="permanent_failed", error_category="output_io")
            raise SystemExit("Unable to save activation output file.") from None
        deliveries.finish(attempt, status="succeeded")
        print(f"Saved to   : {output_file.resolve()}")


def _print_activation(activation_key, payload):
    print("Activation Key:")
    print(activation_key)
    print()
    print(f"License ID : {payload['license_id']}")

    issued_local = datetime.fromisoformat(
        payload["issued_at"].replace("Z", "+00:00")
    ).astimezone()

    expires_local = datetime.fromisoformat(
        payload["expires_at"].replace("Z", "+00:00")
    ).astimezone()

    print(f"Issued local : {issued_local:%d/%m/%Y %H:%M:%S }")
    print(f"Expires local: {expires_local:%d/%m/%Y %H:%M:%S }")


if __name__ == "__main__":
    main()
