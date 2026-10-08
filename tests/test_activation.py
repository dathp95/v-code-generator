from __future__ import annotations

import base64
import importlib
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from generate_activation import (
    b64url_encode,
    canonical_payload_bytes,
    create_activation_payload,
    create_activation_token,
    generate_activation_key,
    validate_device_id,
)
from generate_keys import KEY_SIZE, generate_key_pair
from verify_key_pair import key_pair_matches


DEVICE_ID = "VC-1234-5678-ABCD-EF12-3456"
PASSPHRASE = b"correct horse battery"


def make_private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * ((-len(value)) % 4)))


def validate_like_vcode(
    activation_token: str,
    *,
    expected_device_id: str,
    public_key: rsa.RSAPublicKey,
    now: datetime,
) -> dict:
    payload_part, signature_part = activation_token.strip().split(".")
    payload_bytes = b64url_decode(payload_part)
    signature = b64url_decode(signature_part)
    public_key.verify(
        signature,
        payload_bytes,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    payload = json.loads(payload_bytes.decode("utf-8"))
    required = {
        "version",
        "license_id",
        "device_id",
        "customer",
        "edition",
        "issued_at",
        "expires_at",
    }
    if set(payload) != required:
        raise ValueError("schema mismatch")
    issued_at = datetime.fromisoformat(payload["issued_at"].replace("Z", "+00:00")).astimezone(UTC)
    expires_at = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00")).astimezone(UTC)
    if issued_at >= expires_at:
        raise ValueError("invalid date range")
    if issued_at > now.astimezone(UTC):
        raise ValueError("not valid yet")
    if expires_at <= now.astimezone(UTC):
        raise ValueError("expired")
    if payload["device_id"] != expected_device_id:
        raise ValueError("wrong device")
    return payload


class ActivationGeneratorTests(unittest.TestCase):

    def test_valid_activation_key(self) -> None:
        private_key = make_private_key()
        issued_at = datetime(2026, 10, 8, tzinfo=UTC)
        token, payload = generate_activation_key(
            device_id=DEVICE_ID,
            customer="Customer A",
            edition="Professional",
            days=30,
            private_key=private_key,
            issued_at=issued_at,
        )

        validated = validate_like_vcode(
            token,
            expected_device_id=DEVICE_ID,
            public_key=private_key.public_key(),
            now=issued_at + timedelta(seconds=1),
        )
        self.assertEqual(validated, payload)

    def test_wrong_device_id_fails(self) -> None:
        private_key = make_private_key()
        issued_at = datetime(2026, 10, 8, tzinfo=UTC)
        token, _payload = generate_activation_key(
            device_id=DEVICE_ID,
            customer="Customer A",
            edition="Professional",
            days=30,
            private_key=private_key,
            issued_at=issued_at,
        )

        with self.assertRaises(ValueError):
            validate_like_vcode(
                token,
                expected_device_id="VC-FFFF-5678-ABCD-EF12-3456",
                public_key=private_key.public_key(),
                now=issued_at + timedelta(seconds=1),
            )

    def test_expired_key_fails(self) -> None:
        private_key = make_private_key()
        issued_at = datetime(2026, 10, 8, tzinfo=UTC)
        token, _payload = generate_activation_key(
            device_id=DEVICE_ID,
            customer="Customer A",
            edition="Professional",
            days=1,
            private_key=private_key,
            issued_at=issued_at,
        )

        with self.assertRaises(ValueError):
            validate_like_vcode(
                token,
                expected_device_id=DEVICE_ID,
                public_key=private_key.public_key(),
                now=issued_at + timedelta(days=1),
            )

    def test_modified_payload_fails(self) -> None:
        private_key = make_private_key()
        issued_at = datetime(2026, 10, 8, tzinfo=UTC)
        token, _payload = generate_activation_key(
            device_id=DEVICE_ID,
            customer="Customer A",
            edition="Professional",
            days=30,
            private_key=private_key,
            issued_at=issued_at,
        )
        payload_part, signature_part = token.split(".")
        payload = json.loads(b64url_decode(payload_part).decode("utf-8"))
        payload["edition"] = "Enterprise"
        tampered = f"{b64url_encode(canonical_payload_bytes(payload))}.{signature_part}"

        with self.assertRaises(InvalidSignature):
            validate_like_vcode(
                tampered,
                expected_device_id=DEVICE_ID,
                public_key=private_key.public_key(),
                now=issued_at + timedelta(seconds=1),
            )

    def test_invalid_signature_fails(self) -> None:
        private_key = make_private_key()
        other_key = make_private_key()
        issued_at = datetime(2026, 10, 8, tzinfo=UTC)
        token, _payload = generate_activation_key(
            device_id=DEVICE_ID,
            customer="Customer A",
            edition="Professional",
            days=30,
            private_key=private_key,
            issued_at=issued_at,
        )

        with self.assertRaises(InvalidSignature):
            validate_like_vcode(
                token,
                expected_device_id=DEVICE_ID,
                public_key=other_key.public_key(),
                now=issued_at + timedelta(seconds=1),
            )

    def test_wrong_private_public_key_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            private_a, public_a = generate_key_pair(temp_path / "a", PASSPHRASE)
            _private_b, public_b = generate_key_pair(temp_path / "b", PASSPHRASE)

            self.assertTrue(key_pair_matches(private_a, public_a, PASSPHRASE))
            self.assertFalse(key_pair_matches(private_a, public_b, PASSPHRASE))

    def test_generated_key_pair_uses_3072_bit_rsa_and_encryption(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            private_path, public_path = generate_key_pair(Path(temp_dir), PASSPHRASE)
            private_bytes = private_path.read_bytes()
            self.assertIn(b"-----BEGIN ENCRYPTED PRIVATE KEY-----", private_bytes)
            private_key = serialization.load_pem_private_key(private_bytes, password=PASSPHRASE)
            public_key = serialization.load_pem_public_key(public_path.read_bytes())
            self.assertEqual(private_key.key_size, KEY_SIZE)
            self.assertEqual(private_key.public_key().public_numbers(), public_key.public_numbers())

    def test_invalid_duration(self) -> None:
        with self.assertRaises(ValueError):
            create_activation_payload(
                device_id=DEVICE_ID,
                customer="Customer A",
                edition="Professional",
                days=0,
            )

    def test_malformed_device_id(self) -> None:
        with self.assertRaises(ValueError):
            validate_device_id("not-a-device")

    def test_unique_license_id(self) -> None:
        first = create_activation_payload(
            device_id=DEVICE_ID,
            customer="Customer A",
            edition="Professional",
            days=30,
        )
        second = create_activation_payload(
            device_id=DEVICE_ID,
            customer="Customer A",
            edition="Professional",
            days=30,
        )
        self.assertNotEqual(first["license_id"], second["license_id"])

    def test_utc_timestamps(self) -> None:
        payload = create_activation_payload(
            device_id=DEVICE_ID,
            customer="Customer A",
            edition="Professional",
            days=30,
            issued_at=datetime(2026, 10, 8, 7, 1, 2, 999999, tzinfo=UTC),
        )
        self.assertEqual(payload["issued_at"], "2026-10-08T07:01:02Z")
        self.assertEqual(payload["expires_at"], "2026-11-07T07:01:02Z")

    def test_token_compatibility_with_vcode_when_available(self) -> None:
        vcode_repo_path = os.environ.get("VCODE_REPO_PATH")
        if not vcode_repo_path:
            self.skipTest("Set VCODE_REPO_PATH to run against V-CODE validate_activation_token().")

        sys.path.insert(0, vcode_repo_path)
        try:
            activation = importlib.import_module("license.activation")
        finally:
            sys.path.pop(0)

        private_key = make_private_key()
        issued_at = datetime(2026, 10, 8, tzinfo=UTC)
        token, _payload = generate_activation_key(
            device_id=DEVICE_ID,
            customer="Customer A",
            edition="Professional",
            days=30,
            private_key=private_key,
            issued_at=issued_at,
        )
        license_model = activation.validate_activation_token(
            token,
            expected_device_id=DEVICE_ID,
            public_key=private_key.public_key(),
            now=issued_at + timedelta(seconds=1),
        )
        self.assertEqual(license_model.customer, "Customer A")
        self.assertEqual(license_model.edition, "Professional")

    def test_no_private_key_material_in_generated_output(self) -> None:
        private_key = make_private_key()
        stream = StringIO()
        with redirect_stdout(stream):
            token = create_activation_token(
                create_activation_payload(
                    device_id=DEVICE_ID,
                    customer="Customer A",
                    edition="Professional",
                    days=30,
                    issued_at=datetime(2026, 10, 8, tzinfo=UTC),
                ),
                private_key,
            )
            print(token)

        output = stream.getvalue()
        self.assertNotIn("PRIVATE KEY", output)
        self.assertNotIn("BEGIN", output)
        self.assertNotIn(str(private_key.private_numbers().p), output)


if __name__ == "__main__":
    unittest.main()
