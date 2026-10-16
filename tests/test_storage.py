"""Persistence tests use only temporary databases and generated test keys."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from datetime import UTC, datetime, timedelta
from io import StringIO
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import generate_activation as cli
from core.crypto.activation import canonical_payload_bytes
from core.services.license_service import LicenseService
from services.issuance_service import IssuanceService, RequestUnavailable
from storage.database import Database, DEFAULT_DB_PATH, REPOSITORY_ROOT, StorageError
from storage.repositories.delivery_repository import DeliveryRepository
from storage.repositories.license_repository import LicenseRepository
from storage.repositories.request_repository import RequestRepository
from test_activation import DEVICE_ID, make_private_key, validate_like_vcode


class StorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = make_private_key()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "test.sqlite3"
        self.database = Database(self.path)
        self.database.initialize()
        self.requests = RequestRepository(self.database)
        self.licenses = LicenseRepository(self.database)
        self.deliveries = DeliveryRepository(self.database)
        self.core = Mock(wraps=LicenseService(self.key))
        self.issuance = IssuanceService(self.core, self.database)

    def request(self, **overrides):
        values = dict(channel="telegram", recipient_id=222, user_id=22,
                      customer="Customer", device_id=DEVICE_ID.lower())
        return self.requests.create_or_get_pending(**(values | overrides))

    def test_initialization_twice_reopen_and_foreign_keys(self):
        self.database.initialize()
        request = self.request()
        reopened = Database(self.path)
        reopened.initialize()
        self.assertEqual(RequestRepository(reopened).get(request["request_id"]), request)
        self.assertEqual(request["device_id"], DEVICE_ID)
        for field in ("created_at", "updated_at"):
            self.assertEqual(datetime.fromisoformat(request[field]).utcoffset(), timedelta(0))
        with reopened.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with self.assertRaises(StorageError):
            self.deliveries.start(destination=1, purpose="customer_license", license_id="missing")

    def test_schema_failure_is_transactional_and_future_schema_refused(self):
        broken = Database(self.path.parent / "broken.sqlite3")
        with patch("storage.database.Path.read_text", return_value="CREATE TABLE partial (id INTEGER); INVALID SQL;"):
            with self.assertRaises(StorageError):
                broken.initialize()
        with broken.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE name = ?", ("partial",),
            ).fetchone())
        with broken.transaction() as connection:
            connection.execute("PRAGMA user_version = 999")
        with self.assertRaises(StorageError):
            broken.initialize()

    def test_online_backup_and_restore_preserve_issued_state(self):
        request = self.request()
        record = self.issuance.approve_request(request["request_id"], days=30, issuer=1)
        backup = self.path.parent / "backup.sqlite3"
        restored = self.path.parent / "restored.sqlite3"
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(backup)) as target:
                source.backup(target)
        with closing(sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(restored)) as target:
                source.backup(target)
                self.assertEqual(target.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(target.execute("PRAGMA foreign_key_check").fetchall(), [])
        database = Database(restored)
        database.initialize()
        self.assertEqual(LicenseRepository(database).for_request(request["request_id"]), record)

    def test_path_configuration_is_independent_of_cwd(self):
        with patch.dict(os.environ, {"LICENSE_DB_PATH": ""}), patch("os.getcwd", return_value="Z:/unrelated"):
            self.assertEqual(Database().path, DEFAULT_DB_PATH)
        with patch.dict(os.environ, {"LICENSE_DB_PATH": "data/alternate.sqlite3"}):
            self.assertEqual(Database().path, REPOSITORY_ROOT / "data/alternate.sqlite3")
        with patch.dict(os.environ, {"LICENSE_DB_PATH": str(self.path)}):
            self.assertEqual(Database().path, self.path)

    def test_duplicate_submissions_and_repeated_concurrent_approval_issue_once(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = list(pool.map(lambda _: self.request(), range(2)))
        request_id = requests[0]["request_id"]
        self.assertEqual(requests[1]["request_id"], request_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            records = list(pool.map(
                lambda _: self.issuance.approve_request(request_id, days=30, issuer=1), range(2),
            ))
        self.assertEqual(records[0]["license_id"], records[1]["license_id"])
        self.assertEqual(records[0]["activation_key"], records[1]["activation_key"])
        self.core.issue_activation.assert_called_once()
        self.assertEqual(self.requests.get(request_id)["status"], "approved")
        issued = LicenseService(self.key).issue_activation(DEVICE_ID, "Customer", "Professional", 30)
        with self.assertRaises(StorageError), self.database.transaction() as connection:
            self.licenses.add(issued, days=30, issuer=1, source="telegram_request",
                              request_id=request_id, connection=connection)
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM licenses").fetchone()[0], 1)
        self.assertEqual(self.request()["request_id"], request_id)

    def test_exact_stored_payload_token_and_delivery_history_survive_restart(self):
        request = self.request(customer="Customer '); DROP TABLE licenses; --")
        record = self.issuance.approve_request(request["request_id"], days=30, issuer=1)
        attempt = self.deliveries.start(
            request_id=request["request_id"], license_id=record["license_id"],
            destination=222, purpose="customer_license",
        )
        self.deliveries.finish(attempt, status="retryable_failed", error_category="timeout")
        reopened = Database(self.path)
        reopened.initialize()
        saved = LicenseRepository(reopened).for_request(request["request_id"])
        self.assertEqual(saved, record)
        self.assertEqual(saved["payload_json"].encode(), canonical_payload_bytes(record["payload"]))
        self.assertEqual(validate_like_vcode(
            saved["activation_key"], expected_device_id=DEVICE_ID,
            public_key=self.key.public_key(), now=datetime.now(UTC) + timedelta(seconds=1),
        ), saved["payload"])
        retry = DeliveryRepository(reopened).start(
            request_id=request["request_id"], license_id=saved["license_id"],
            destination=222, purpose="customer_license",
        )
        DeliveryRepository(reopened).finish(retry, status="succeeded", message_id=42)
        history = DeliveryRepository(reopened).history(
            request_id=request["request_id"], license_id=saved["license_id"], purpose="customer_license",
        )
        self.assertEqual([row["attempt_number"] for row in history], [1, 2])
        self.assertEqual([row["status"] for row in history], ["retryable_failed", "succeeded"])
        self.assertEqual(history[-1]["telegram_message_id"], 42)
        self.assertEqual(self.requests.get(request["request_id"])["status"], "approved")

    def test_expired_cached_key_requires_new_request(self):
        request = self.request()
        old = LicenseService(self.key).issue_activation(
            DEVICE_ID, "Customer", "Professional", 1, datetime(2020, 1, 1, tzinfo=UTC),
        )
        with self.database.transaction() as connection:
            self.licenses.add(old, days=1, issuer=1, source="telegram_request",
                              request_id=request["request_id"], connection=connection)
            self.requests.approve(request["request_id"], connection=connection)
        with self.assertRaises(RequestUnavailable):
            self.issuance.approve_request(request["request_id"], days=1, issuer=1)
        self.core.issue_activation.assert_not_called()
        new = self.request()
        self.assertNotEqual(new["request_id"], request["request_id"])
        self.assertEqual(self.requests.get(request["request_id"])["status"], "closed")

    def test_cli_issuance_recorded_and_3651_rejected_by_service_before_prompt(self):
        args = ["generate_activation.py", "--device-id", DEVICE_ID, "--customer", "Customer",
                "--edition", "Professional", "--days", "30", "--private-key", "test.pem"]
        stream = StringIO()
        with (
            patch("sys.argv", args),
            patch.object(cli.getpass, "getpass", return_value="test-only"),
            patch.object(cli, "load_private_key", return_value=self.key),
            patch.object(cli, "create_activation_token", side_effect=AssertionError("CLI bypassed service")),
            patch.dict(os.environ, {"LICENSE_DB_PATH": str(self.path)}),
            redirect_stdout(stream),
        ):
            cli.main()
        with self.database.connection() as connection:
            record = dict(connection.execute("SELECT * FROM licenses").fetchone())
        self.assertEqual(record["source"], "cli")
        self.assertIsNone(record["request_id"])
        self.assertIn(record["activation_key"], stream.getvalue())
        self.assertTrue(self.deliveries.succeeded(license_id=record["license_id"], purpose="cli_stdout"))
        args[args.index("30")] = "3651"
        with patch("sys.argv", args), patch.object(cli.getpass, "getpass") as prompt:
            with self.assertRaisesRegex(SystemExit, "1 and 3650"):
                cli.main()
            prompt.assert_not_called()

    def test_cli_database_failure_prevents_stdout_and_file_output(self):
        output = self.path.parent / "output.txt"
        args = ["generate_activation.py", "--device-id", DEVICE_ID, "--customer", "Customer",
                "--edition", "Professional", "--days", "30", "--private-key", "test.pem",
                "--output-file", str(output)]
        stream = StringIO()
        with (
            patch("sys.argv", args),
            patch.object(cli.getpass, "getpass", return_value="test-only"),
            patch.object(cli, "load_private_key", return_value=self.key),
            patch.dict(os.environ, {"LICENSE_DB_PATH": str(self.path)}),
            patch.object(LicenseRepository, "add", side_effect=sqlite3.OperationalError("sensitive-test-sentinel")),
            redirect_stdout(stream),
        ):
            with self.assertRaises(SystemExit) as caught:
                cli.main()
        self.assertNotIn("sensitive-test-sentinel", str(caught.exception))
        self.assertEqual(stream.getvalue(), "")
        self.assertFalse(output.exists())
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM licenses").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
