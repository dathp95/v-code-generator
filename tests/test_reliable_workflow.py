"""Deterministic workflow tests; no real configuration, keys, or messages."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from telegram.error import Forbidden, RetryAfter, TimedOut

import telegram_bot as bot
from core.services.license_service import LicenseService
from services.delivery_service import recorded_send
from services.issuance_service import RequestUnavailable
from storage.database import Database, StorageError
from storage.repositories.delivery_repository import DeliveryBlocked
from test_activation import DEVICE_ID, make_private_key


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 16, 12, tzinfo=UTC)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class ReliableWorkflowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = make_private_key()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "test.sqlite3"
        self.clock = Clock()
        self.database = Database(self.path, clock=self.clock)
        self.core = Mock(wraps=LicenseService(self.key))
        self.data = {"admin_id": 1}
        bot.configure_storage(self.data, self.core, self.database)
        self.context = SimpleNamespace(
            application=SimpleNamespace(bot_data=self.data), user_data={}, args=[],
            bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=12))),
        )
        self.message = SimpleNamespace(text="30", reply_text=AsyncMock())
        self.update = SimpleNamespace(
            effective_message=self.message, effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=1, type="private"), callback_query=None,
        )

    def request(self, user=22):
        return self.data["request_repository"].create_or_get_pending(
            channel="telegram", recipient_id=user * 10, user_id=user,
            customer=f"Customer {user}", device_id=DEVICE_ID,
        )

    def query(self, callback, *, user=1):
        self.update.callback_query = SimpleNamespace(
            data=callback, from_user=SimpleNamespace(id=user), answer=AsyncMock(),
        )

    async def preview(self, request):
        self.query(f"request:{request['request_id']}")
        await bot.select_request(self.update, self.context)
        self.message.text = "30"
        await bot.receive_form(self.update, self.context)
        return self.message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data

    async def confirm(self, callback, *, user=1):
        self.query(callback, user=user)
        await bot.approval_decision(self.update, self.context)

    def issued(self, request):
        return self.data["issuance_service"].approve_request(request["request_id"], days=30, issuer=1)

    def delivery_args(self, request, record):
        return dict(request_id=request["request_id"], license_id=record["license_id"],
                    destination=request["recipient_id"], purpose="customer_license")

    async def test_selecting_b_invalidates_a_and_requires_explicit_confirmation(self):
        a, b = self.request(), self.request(33)
        callback_a = await self.preview(a)
        text = self.message.reply_text.await_args.args[0]
        for value in (a["customer"], DEVICE_ID, a["recipient_id"], "Professional", "30"):
            self.assertIn(value, text)
        self.assertIsNone(self.data["license_repository"].for_request(a["request_id"]))
        callback_b = await self.preview(b)
        await self.confirm(callback_a)
        self.core.issue_activation.assert_not_called()
        await self.confirm(callback_b)
        self.core.issue_activation.assert_called_once()
        self.assertIsNone(self.data["license_repository"].for_request(a["request_id"]))
        self.assertEqual(self.context.bot.send_message.await_args.kwargs["chat_id"], 330)
        await self.confirm(callback_b)
        self.core.issue_activation.assert_called_once()
        history = self.data["delivery_repository"].history(
            request_id=b["request_id"], license_id=self.data["license_repository"].for_request(b["request_id"])["license_id"],
            purpose="customer_license",
        )
        self.assertEqual(len(history), 1)

    async def test_unauthorized_group_expired_and_cancelled_forms_cannot_issue(self):
        request = self.request()
        callback = await self.preview(request)
        await self.confirm(callback, user=99)
        self.update.effective_chat.type = "group"
        await self.confirm(callback)
        self.update.effective_chat.type = "private"
        self.clock.advance(601)
        await self.confirm(callback)
        self.core.issue_activation.assert_not_called()
        callback = await self.preview(request)
        await self.confirm(callback.replace(":confirm:", ":cancel:"))
        await self.confirm(callback)
        self.core.issue_activation.assert_not_called()

    async def test_confirmation_rechecks_license_snapshot(self):
        request = self.request()
        callback = await self.preview(request)
        record = self.issued(request)
        self.context.bot.send_message.reset_mock()
        await self.confirm(callback)
        self.context.bot.send_message.assert_not_awaited()
        self.assertEqual(self.data["license_repository"].for_request(request["request_id"]), record)
        self.core.issue_activation.assert_called_once()

    async def test_confirmation_rechecks_delivery_state_and_invalid_selection_cancels_old_form(self):
        request = self.request()
        record = self.issued(request)
        callback = await self.preview(request)
        claim = self.data["delivery_repository"].start(**self.delivery_args(request, record))
        self.data["delivery_repository"].finish(claim, status="unknown", error_category="timeout")
        self.context.bot.send_message.reset_mock()
        await self.confirm(callback)
        self.context.bot.send_message.assert_not_awaited()
        callback = await self.preview(request)
        self.query("request:missing")
        await bot.select_request(self.update, self.context)
        await self.confirm(callback)
        self.assertTrue(self.update.callback_query.answer.await_args.kwargs["show_alert"])
        self.core.issue_activation.assert_called_once()

    async def test_claim_rechecks_reviewed_state_after_confirmation_before_io(self):
        request = self.request()
        callback = await self.preview(request)
        confirmed_request, record = self.data["approval_service"].confirm(
            callback.split(":")[-1], admin_id=1, chat_id=1,
        )
        claim = self.data["delivery_repository"].start(**self.delivery_args(request, record))
        self.data["delivery_repository"].finish(claim, status="unknown", error_category="timeout")
        self.context.bot.send_message.reset_mock()
        with self.assertRaises(DeliveryBlocked) as blocked:
            await bot.deliver_customer(confirmed_request, record, self.update, self.context)
        self.assertEqual(blocked.exception.code, "state_changed")
        self.context.bot.send_message.assert_not_awaited()

    async def test_restart_selects_persisted_request_and_unknown_retry_keeps_key(self):
        request = self.request()
        record = self.issued(request)
        args = self.delivery_args(request, record)
        self.data["delivery_repository"].start(**args)
        self.clock.advance(121)
        self.database = Database(self.path, clock=self.clock)
        self.data = {"admin_id": 1}
        bot.configure_storage(self.data, self.core, self.database)
        self.context.application.bot_data = self.data
        self.context.user_data.clear()
        self.core.reset_mock()
        self.context.bot.send_message.reset_mock()
        with self.assertRaises(DeliveryBlocked) as blocked:
            await recorded_send(self.data["delivery_repository"], send=AsyncMock(), **args)
        self.assertEqual(blocked.exception.code, "unknown")
        callback = await self.preview(request)
        self.assertIn("trùng", self.message.reply_text.await_args.args[0])
        self.assertIn("Có", self.message.reply_text.await_args.args[0])
        self.core.issue_activation.assert_not_called()
        await self.confirm(callback)
        self.core.issue_activation.assert_not_called()
        self.assertEqual(self.data["license_repository"].for_request(request["request_id"]), record)
        self.assertIn(record["activation_key"], self.context.bot.send_message.await_args.kwargs["text"])
        history = self.data["delivery_repository"].history(
            request_id=request["request_id"], license_id=record["license_id"], purpose="customer_license",
        )
        self.assertEqual([row["status"] for row in history], ["unknown", "succeeded"])

    async def test_concurrent_workers_have_one_sender_and_no_network_transaction(self):
        request = self.request()
        record = self.issued(request)
        args = self.delivery_args(request, record)
        entered, release = asyncio.Event(), asyncio.Event()

        async def send():
            with self.database.transaction() as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM licenses").fetchone()[0], 1)
            entered.set()
            await release.wait()
            return SimpleNamespace(message_id=18)

        first = asyncio.create_task(recorded_send(self.data["delivery_repository"], send=send, **args))
        await entered.wait()
        second_send = AsyncMock()
        try:
            with self.assertRaises(DeliveryBlocked) as blocked:
                await recorded_send(self.data["delivery_repository"], send=second_send, **args)
            self.assertEqual(blocked.exception.code, "in_progress")
            second_send.assert_not_awaited()
        finally:
            release.set()
            self.assertEqual(await first, "succeeded")

    async def test_local_deadline_is_unknown_and_restart_keeps_live_lease(self):
        request = self.request()
        args = self.delivery_args(request, self.issued(request))
        repository = self.data["delivery_repository"]
        with patch("services.delivery_service.NETWORK_TIMEOUT_SECONDS", 0):
            self.assertEqual(await recorded_send(repository, send=AsyncMock(), **args), "unknown")
        repository.start(**args, explicit_retry=True)
        bot.configure_storage(self.data, self.core, Database(self.path, clock=self.clock))
        with self.assertRaises(DeliveryBlocked) as blocked:
            await recorded_send(self.data["delivery_repository"], send=AsyncMock(), explicit_retry=True, **args)
        self.assertEqual(blocked.exception.code, "in_progress")
        self.clock.advance(121)
        self.data["delivery_repository"].recover_stale()
        latest = self.data["delivery_repository"].latest(
            request_id=args["request_id"], license_id=args["license_id"], purpose=args["purpose"],
        )
        self.assertEqual(latest["status"], "unknown")

    def test_atomic_claims_and_old_owner_cannot_finalize_new_claim(self):
        request = self.request()
        args = self.delivery_args(request, self.issued(request))
        repository = self.data["delivery_repository"]

        def claim(_):
            try:
                return repository.start(**args)
            except DeliveryBlocked:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = [value for value in pool.map(claim, range(2)) if value]
        self.assertEqual(len(claims), 1)
        old = claims[0]
        self.clock.advance(121)
        repository.recover_stale()
        newer = repository.start(**args, explicit_retry=True)
        for fenced in (old, replace(old, delivery_id=newer.delivery_id)):
            with self.assertRaises(DeliveryBlocked):
                repository.finish(fenced, status="succeeded")
        repository.finish(newer, status="succeeded", message_id=29)
        history = repository.history(request_id=args["request_id"], license_id=args["license_id"], purpose=args["purpose"])
        self.assertEqual([row["status"] for row in history], ["unknown", "succeeded"])
        self.assertEqual(history[-1]["telegram_message_id"], 29)

    async def test_retry_after_permanent_unknown_and_success_policies(self):
        request = self.request()
        args = self.delivery_args(request, self.issued(request))
        repository = self.data["delivery_repository"]
        send = AsyncMock(side_effect=RetryAfter(timedelta(seconds=10)))
        self.assertEqual(await recorded_send(repository, send=send, **args), "retryable_failed")
        send.side_effect = None
        with self.assertRaises(DeliveryBlocked) as blocked:
            await recorded_send(repository, send=send, explicit_retry=True, **args)
        self.assertEqual(blocked.exception.code, "cooldown")
        self.assertEqual(send.await_count, 1)
        self.clock.advance(10)
        send.side_effect = Forbidden("sensitive-test-sentinel")
        self.assertEqual(await recorded_send(repository, send=send, **args), "permanent_failed")
        with self.assertRaises(DeliveryBlocked):
            await recorded_send(repository, send=send, **args)
        send.side_effect = TimedOut("sensitive-test-sentinel")
        self.assertEqual(await recorded_send(repository, send=send, explicit_retry=True, **args), "unknown")
        with self.assertRaises(DeliveryBlocked):
            await recorded_send(repository, send=send, **args)
        send.side_effect = None
        send.return_value = SimpleNamespace(message_id=17)
        self.assertEqual(await recorded_send(repository, send=send, explicit_retry=True, **args), "succeeded")
        with self.assertRaises(DeliveryBlocked) as blocked:
            await recorded_send(repository, send=send, explicit_retry=True, **args)
        self.assertEqual(blocked.exception.code, "already_succeeded")
        history = repository.history(request_id=args["request_id"], license_id=args["license_id"], purpose=args["purpose"])
        self.assertNotIn("sensitive-test-sentinel", str(history))

    async def test_rejection_records_admin_time_reason_and_cannot_issue(self):
        request = self.request()
        self.query(f"request:{request['request_id']}")
        await bot.select_request(self.update, self.context)
        selection = self.context.user_data["key_form"]["selection_id"]
        self.query(f"reject:{selection}")
        await bot.reject_request(self.update, self.context)
        self.message.text = "Duplicate customer request"
        await bot.receive_form(self.update, self.context)
        callback = self.message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        await self.confirm(callback)
        rejected = self.data["request_repository"].get(request["request_id"])
        self.assertEqual(rejected["status"], "closed")
        self.assertEqual(rejected["rejected_by"], "1")
        self.assertEqual(datetime.fromisoformat(rejected["rejected_at"]), self.clock())
        self.assertEqual(rejected["rejection_reason"], "Duplicate customer request")
        with self.assertRaises(RequestUnavailable):
            self.issued(request)
        self.core.issue_activation.assert_not_called()

    async def test_bounded_paginated_requests_and_customer_actual_state(self):
        requests = [self.request(user=number) for number in range(22, 32)]
        self.update.callback_query = None
        await bot.requests_command(self.update, self.context)
        keyboard = self.message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(sum(button.callback_data.startswith("request:") for row in keyboard for button in row), 8)
        self.assertTrue(any(button.callback_data == "requests:1" for row in keyboard for button in row))
        self.query("requests:1")
        await bot.requests_page(self.update, self.context)
        keyboard = self.message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(sum(button.callback_data.startswith("request:") for row in keyboard for button in row), 2)
        target = requests[0]
        self.update.callback_query = None
        self.update.effective_user = SimpleNamespace(id=22, username=None, full_name="Customer 22")
        self.update.effective_chat.id = 220
        self.message.text = DEVICE_ID
        await bot.receive_form(self.update, self.context)
        self.assertIn("chờ Admin duyệt", self.message.reply_text.await_args.args[0])
        record = self.issued(target)
        await bot.receive_form(self.update, self.context)
        self.assertIn("đã được cấp", self.message.reply_text.await_args.args[0])
        claim = self.data["delivery_repository"].start(**self.delivery_args(target, record))
        self.data["delivery_repository"].finish(claim, status="succeeded")
        await bot.receive_form(self.update, self.context)
        self.assertIn("gửi thành công", self.message.reply_text.await_args.args[0])
        self.message.reply_text.reset_mock()
        await bot.requests_command(self.update, self.context)
        self.assertNotIn("reply_markup", self.message.reply_text.await_args.kwargs)

    async def test_failed_manual_deliveries_are_listed_for_admin_recovery(self):
        record = self.data["issuance_service"].issue_manual(
            device_id=DEVICE_ID, customer="Manual Customer", edition="Professional", days=30,
            issuer=1, source="telegram_manual",
        )
        claim = self.data["delivery_repository"].start(
            license_id=record["license_id"], destination=1, purpose="manual_license",
        )
        self.data["delivery_repository"].finish(claim, status="unknown", error_category="timeout")
        await bot.requests_command(self.update, self.context)
        text = self.message.reply_text.await_args.args[0]
        self.assertIn(f"/retry {record['license_id']}", text)
        self.assertIn("unknown", text)
        self.assertNotIn(record["activation_key"], text)


class MigrationTests(unittest.TestCase):
    def test_commit_12_data_preserved_and_upgrade_rolls_back_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            database = Database(path)
            schema = Path(__file__).resolve().parents[1] / "storage" / "migrations" / "001_initial.sql"
            key = LicenseService(make_private_key()).issue_activation(
                DEVICE_ID, "Customer", "Professional", 30, Clock()(),
            )
            from core.crypto.activation import canonical_payload_bytes
            with database.connection() as connection:
                connection.executescript(schema.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("r1", "telegram", "220", "22", "Customer", DEVICE_ID, "approved", "2026-10-16T12:00:00Z", "2026-10-16T12:00:00Z"),
                )
                connection.execute(
                    "INSERT INTO licenses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (key.payload["license_id"], "r1", key.activation_key, canonical_payload_bytes(key.payload).decode(),
                     "Professional", 30, key.payload["issued_at"], key.payload["expires_at"], "1", "telegram_request"),
                )
                for number, status in ((1, "retryable_failed"), (2, "attempting")):
                    connection.execute(
                        """INSERT INTO deliveries (request_id, license_id, destination, purpose,
                           attempt_number, status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        ("r1", key.payload["license_id"], "220", "customer_license", number, status, "2026-10-16T12:00:00Z"),
                    )
            with patch("storage.database.Path.read_text", return_value="ALTER TABLE requests ADD COLUMN partial TEXT; INVALID SQL;"):
                with self.assertRaises(StorageError):
                    database.initialize()
            with database.connection() as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertNotIn("partial", [row[1] for row in connection.execute("PRAGMA table_info(requests)")])
            database.initialize()
            database.initialize()
            with database.connection() as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
                saved = dict(connection.execute("SELECT * FROM licenses").fetchone())
                self.assertEqual(saved["activation_key"], key.activation_key)
                self.assertEqual(saved["payload_json"], canonical_payload_bytes(key.payload).decode())
                self.assertEqual([row[0] for row in connection.execute("SELECT status FROM deliveries ORDER BY delivery_id")],
                                 ["retryable_failed", "unknown"])
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
