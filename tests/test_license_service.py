from __future__ import annotations

import tempfile
import traceback
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import generate_activation as cli
from core.crypto import activation
from core.crypto.key_loader import load_private_key
from core.exceptions import ActivationValidationError, PrivateKeyLoadError
from core.models.issued_activation import IssuedActivation
from core.services.license_service import LicenseService
from test_activation import DEVICE_ID, PASSPHRASE, make_private_key, validate_like_vcode


class LicenseServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = make_private_key()

    def test_signature_schema_canonical_bytes_and_fixed_utc_expiration(self):
        issued_at = datetime(2026, 10, 8, 7, 1, 2, 999999,
                             tzinfo=timezone(timedelta(hours=7)))
        issued = LicenseService(self.key).issue_activation(
            DEVICE_ID.lower(), " Customer A ", " Professional ", 30, issued_at,
        )
        self.assertIsInstance(issued, IssuedActivation)
        self.assertEqual(issued.payload["issued_at"], "2026-10-08T00:01:02Z")
        self.assertEqual(issued.payload["expires_at"], "2026-11-07T00:01:02Z")
        self.assertEqual(issued.payload["customer"], "Customer A")
        self.assertEqual(issued.payload["edition"], "Professional")
        self.assertEqual(issued.payload["version"], 1)
        self.assertEqual(validate_like_vcode(
            issued.activation_key, expected_device_id=DEVICE_ID,
            public_key=self.key.public_key(), now=issued_at + timedelta(seconds=1),
        ), issued.payload)
        payload_part, signature_part = issued.activation_key.split(".")
        self.assertEqual(payload_part, activation.b64url_encode(
            activation.canonical_payload_bytes(issued.payload)))
        self.assertNotIn("=", payload_part + signature_part)
        self.assertEqual(activation.canonical_payload_bytes({"z": "é", "a": 1}),
                         b'{"a":1,"z":"\\u00e9"}')

    def test_duration_boundaries(self):
        for days in (1, 3650):
            issued = LicenseService(self.key).issue_activation(
                DEVICE_ID, "Customer", "Professional", days,
                datetime(2026, 1, 1, tzinfo=UTC),
            )
            self.assertEqual(
                datetime.fromisoformat(issued.payload["expires_at"]) -
                datetime.fromisoformat(issued.payload["issued_at"]),
                timedelta(days=days),
            )

    def test_invalid_inputs_do_not_sign(self):
        valid = dict(device_id=DEVICE_ID, customer="Customer", edition="Pro", days=30)
        cases = [dict(days=value) for value in (0, -1, 3651, True, 1.5, "30", None)]
        cases += [dict(device_id=value) for value in ("bad", None)]
        cases += [dict(customer=value) for value in (" ", None)]
        cases += [dict(edition=value) for value in ("", 42)]
        with patch("core.services.license_service.create_activation_token") as sign:
            for invalid in cases:
                with self.subTest(invalid=invalid), self.assertRaises(ActivationValidationError):
                    LicenseService(self.key).issue_activation(**(valid | invalid))
            sign.assert_not_called()

    def test_cli_public_api_and_tuple_result(self):
        for name in ("ACTIVATION_VERSION", "DEVICE_ID_RE", "b64url_encode",
                     "canonical_payload_bytes", "create_activation_payload",
                     "create_activation_token", "format_utc", "validate_days",
                     "validate_device_id", "validate_non_empty"):
            self.assertIs(getattr(cli, name), getattr(activation, name))
        self.assertIs(cli.load_private_key, load_private_key)
        self.assertEqual(cli.DEFAULT_OUTPUT_DIR, Path("output"))
        issued_at = datetime(2026, 10, 8, tzinfo=UTC)
        result = cli.generate_activation_key(
            device_id=DEVICE_ID, customer="A", edition="Pro", days=30,
            private_key=self.key, issued_at=issued_at,
        )
        self.assertIsInstance(result, tuple)
        token, payload = result
        self.assertEqual(validate_like_vcode(
            token, expected_device_id=DEVICE_ID, public_key=self.key.public_key(),
            now=issued_at + timedelta(seconds=1),
        ), payload)

    def test_cli_arguments_getpass_output_and_local_time(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "activation.txt"
            args = ["generate_activation.py", "--device-id", DEVICE_ID,
                    "--customer", "A", "--edition", "Pro", "--days", "30",
                    "--private-key", "temporary-test.pem", "--output-file", str(output)]
            stream = StringIO()
            with (
                patch("sys.argv", args),
                patch.object(cli.getpass, "getpass", return_value="test-only") as prompt,
                patch.object(cli, "load_private_key", return_value=self.key) as loader,
                redirect_stdout(stream),
            ):
                cli.main()
            prompt.assert_called_once_with("Private key passphrase: ")
            loader.assert_called_once_with(Path("temporary-test.pem"), b"test-only")
            token = output.read_text(encoding="utf-8").strip()
            self.assertIn(token, stream.getvalue())
            self.assertTrue(output.read_bytes().endswith(b"\n"))
            self.assertIn("Issued local :", stream.getvalue())
            self.assertIn("Expires local:", stream.getvalue())


class KeyLoaderTests(unittest.TestCase):
    def test_encrypted_loading_and_safe_failures(self):
        key = make_private_key()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.pem"
            path.write_bytes(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(PASSPHRASE),
            ))
            loaded = load_private_key(path, PASSPHRASE)
            self.assertEqual(loaded.public_key().public_numbers(), key.public_key().public_numbers())
            for target, password in ((path, b"wrong-test-password"),
                                     (path, b""), (path, None),
                                     (Path(directory) / "missing.pem", PASSPHRASE)):
                with self.subTest(target=target, password_type=type(password)):
                    with self.assertRaises(PrivateKeyLoadError) as caught:
                        load_private_key(target, password)
                    rendered = "".join(traceback.format_exception(caught.exception))
                    self.assertNotIn(str(target), rendered)
                    self.assertNotIn("wrong-test-password", rendered)
                    self.assertNotIn(PASSPHRASE.decode(), rendered)
            path.write_bytes(b"invalid test key")
            with self.assertRaises(PrivateKeyLoadError):
                load_private_key(path, PASSPHRASE)
            for material, encryption in (
                (key, serialization.NoEncryption()),
                (ec.generate_private_key(ec.SECP256R1()), serialization.BestAvailableEncryption(PASSPHRASE)),
            ):
                path.write_bytes(material.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption,
                ))
                with self.assertRaises(PrivateKeyLoadError):
                    load_private_key(path, PASSPHRASE)


if __name__ == "__main__":
    unittest.main()
