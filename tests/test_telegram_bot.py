"""Offline handler tests; all delivery and startup dependencies are mocked."""

import unittest
import tempfile
import sqlite3
from datetime import UTC, datetime
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut

import telegram_bot as bot
from core.services.license_service import LicenseService
from storage.database import Database
from storage.repositories.license_repository import LicenseRepository
from test_activation import DEVICE_ID, make_private_key


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = Mock(wraps=LicenseService(make_private_key()))
        self.data = {"admin_id": 1, "license_service": self.service}
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(Path(directory.name) / "test.sqlite3")
        bot.configure_storage(self.data, self.service, self.database)
        self.context = SimpleNamespace(
            application=SimpleNamespace(bot_data=self.data),
            user_data={}, args=[DEVICE_ID, "30"],
            bot=SimpleNamespace(send_message=AsyncMock()),
        )
        self.message = SimpleNamespace(text=DEVICE_ID, reply_text=AsyncMock())
        self.update = SimpleNamespace(
            effective_message=self.message,
            effective_user=SimpleNamespace(id=1, full_name="Admin", username="admin"),
            effective_chat=SimpleNamespace(id=1, type="private"),
            callback_query=None,
        )

    async def test_unauthorized_command_form_and_callback_never_issue(self):
        self.update.effective_user.id = 2
        await bot.generate(self.update, self.context)
        self.context.user_data["key_form"] = {"step": "days", "device_id": DEVICE_ID}
        self.message.text = "30"
        await bot.receive_form(self.update, self.context)
        query = SimpleNamespace(from_user=self.update.effective_user,
                                data="request:any", answer=AsyncMock())
        self.update.callback_query = query
        await bot.select_request(self.update, self.context)
        query.data = "menu:generate"
        await bot.menu_click(self.update, self.context)
        self.service.issue_activation.assert_not_called()
        self.context.bot.send_message.assert_not_awaited()
        self.assertTrue(query.answer.await_args.kwargs["show_alert"])

    async def test_group_admin_cannot_issue(self):
        self.update.effective_chat.type = "group"
        self.context.user_data["key_form"] = {"step": "days", "device_id": DEVICE_ID}
        self.message.text = "30"
        await bot.generate(self.update, self.context)
        await bot.receive_form(self.update, self.context)
        self.service.issue_activation.assert_not_called()

    async def test_customer_request_requires_admin_and_retry_uses_cached_key(self):
        self.update.effective_user = SimpleNamespace(id=22, full_name="Customer", username=None)
        self.update.effective_chat.id = 222
        await bot.receive_form(self.update, self.context)
        self.service.issue_activation.assert_not_called()
        callback = self.context.bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        request_id = callback.split(":", 1)[1]
        request = self.data["request_repository"].get(request_id)
        self.assertEqual(request["recipient_id"], "222")
        self.assertEqual(request["status"], "pending")
        self.assertEqual(self.context.bot.send_message.await_args.kwargs["chat_id"], 1)
        self.assertEqual(callback, f"request:{request_id}")
        # A separate pending request must never affect this request's recipient.
        self.data["request_repository"].create_or_get_pending(
            channel="telegram", recipient_id=333, user_id=33, customer="Other", device_id=DEVICE_ID,
        )
        self.update.effective_user = SimpleNamespace(id=1)
        self.update.effective_chat.id = 1
        self.update.callback_query = SimpleNamespace(
            from_user=self.update.effective_user, data=callback, answer=AsyncMock(),
        )
        await bot.select_request(self.update, self.context)
        self.message.text = "30"
        self.context.bot.send_message.reset_mock()
        self.context.bot.send_message.side_effect = TelegramError("mock delivery failure")
        await bot.receive_form(self.update, self.context)
        request = self.data["request_repository"].get(request_id)
        self.assertEqual(request["status"], "approved")
        record = self.data["license_repository"].for_request(request_id)
        cached_key = record["activation_key"]
        cached_payload = record["payload"]
        failed_delivery = self.context.bot.send_message.await_args.kwargs
        self.assertEqual(failed_delivery["chat_id"], 222)
        self.assertIn(cached_key, failed_delivery["text"])
        self.service.issue_activation.assert_called_once_with(
            device_id=DEVICE_ID, customer="Customer", edition="Professional", days=30,
        )
        self.message.text = "31"
        await bot.receive_form(self.update, self.context)
        self.assertEqual(self.context.bot.send_message.await_count, 1)
        self.message.text = "30"
        self.context.bot.send_message.side_effect = None
        await bot.receive_form(self.update, self.context)
        self.service.issue_activation.assert_called_once()
        self.assertEqual(self.context.bot.send_message.await_args.kwargs, failed_delivery)
        record = self.data["license_repository"].for_request(request_id)
        self.assertEqual(record["activation_key"], cached_key)
        self.assertEqual(record["payload"], cached_payload)
        self.assertEqual(self.data["request_repository"].get(request_id)["status"], "approved")
        self.assertTrue(self.data["delivery_repository"].succeeded(
            request_id=request_id, license_id=record["license_id"], purpose="customer_license",
        ))
        self.assertNotIn("key_form", self.context.user_data)
        await bot.select_request(self.update, self.context)
        self.assertNotIn("key_form", self.context.user_data)
        self.service.issue_activation.assert_called_once()

    async def test_manual_command_and_form_use_service(self):
        await bot.generate(self.update, self.context)
        self.service.issue_activation.assert_called_once_with(
            device_id=DEVICE_ID, customer="Test Customer", edition="Professional", days=30,
        )
        self.service.reset_mock()
        self.context.user_data["key_form"] = {"step": "device"}
        await bot.receive_form(self.update, self.context)
        self.assertEqual(self.context.user_data["key_form"]["step"], "days")
        self.message.text = "30"
        await bot.receive_form(self.update, self.context)
        self.service.issue_activation.assert_called_once_with(
            device_id=DEVICE_ID, customer="Test Customer", edition="Professional", days=30,
        )
        self.assertNotIn("key_form", self.context.user_data)
        self.context.bot.send_message.assert_not_awaited()
        with self.database.connection() as connection:
            rows = connection.execute("SELECT * FROM licenses ORDER BY rowid").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["source"] == "telegram_manual" for row in rows))
            self.assertTrue(all(row["request_id"] is None for row in rows))
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM deliveries WHERE purpose = 'manual_license' AND status = 'succeeded'",
            ).fetchone()[0], 2)

    def make_request(self):
        return self.data["request_repository"].create_or_get_pending(
            channel="telegram", recipient_id=222, user_id=22, customer="Customer", device_id=DEVICE_ID,
        )

    def reopen(self):
        self.database = Database(self.database.path)
        bot.configure_storage(self.data, self.service, self.database)
        self.context.user_data.clear()

    async def select(self, request_id):
        self.update.callback_query = SimpleNamespace(
            from_user=self.update.effective_user, data=f"request:{request_id}", answer=AsyncMock(),
        )
        await bot.select_request(self.update, self.context)

    async def test_pending_callback_after_database_reopen(self):
        request = self.make_request()
        self.reopen()
        await self.select(request["request_id"])
        self.assertEqual(self.context.user_data["key_form"]["request_id"], request["request_id"])
        self.service.issue_activation.assert_not_called()
        self.assertEqual(self.context.bot.send_message.await_args.kwargs["chat_id"], 1)

    async def test_failed_delivery_restart_retry_identical_token_and_license_id(self):
        request = self.make_request()
        await self.select(request["request_id"])
        self.message.text = "30"
        self.context.bot.send_message.side_effect = TimedOut("sensitive-test-sentinel")
        await bot.receive_form(self.update, self.context)
        saved = self.data["license_repository"].for_request(request["request_id"])
        original_delivery = self.context.bot.send_message.await_args.kwargs
        self.service.issue_activation.assert_called_once()
        self.reopen()
        self.service.reset_mock()
        self.context.bot.send_message.side_effect = None
        await self.select(request["request_id"])

        async def acknowledged(**kwargs):
            # A write transaction is possible while the network coroutine runs.
            with self.database.transaction() as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM licenses").fetchone()[0], 1)
            return SimpleNamespace(message_id=456)

        self.context.bot.send_message.side_effect = acknowledged
        await bot.receive_form(self.update, self.context)
        retried = self.data["license_repository"].for_request(request["request_id"])
        self.assertEqual(retried, saved)
        self.assertEqual(self.context.bot.send_message.await_args.kwargs, original_delivery)
        self.service.issue_activation.assert_not_called()
        history = self.data["delivery_repository"].history(
            request_id=request["request_id"], license_id=saved["license_id"], purpose="customer_license",
        )
        self.assertEqual([row["status"] for row in history], ["retryable_failed", "succeeded"])
        self.assertEqual(history[0]["error_category"], "timeout")
        self.assertEqual(history[1]["telegram_message_id"], 456)
        self.assertNotIn("sensitive-test-sentinel", str(history))
        await self.select(request["request_id"])
        self.assertNotIn("key_form", self.context.user_data)
        self.service.issue_activation.assert_not_called()

    async def test_duplicate_submission_retries_failed_admin_notification(self):
        self.update.effective_user = SimpleNamespace(id=22, full_name="Customer", username=None)
        self.update.effective_chat.id = 222
        self.context.bot.send_message.side_effect = NetworkError("sensitive-test-sentinel")
        await bot.receive_form(self.update, self.context)
        callback = self.context.bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        request_id = callback.split(":", 1)[1]
        self.reopen()
        self.context.bot.send_message.side_effect = None
        await bot.receive_form(self.update, self.context)
        retry_callback = self.context.bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        self.assertEqual(callback, retry_callback)
        await bot.receive_form(self.update, self.context)
        self.assertEqual(self.context.bot.send_message.await_count, 2)
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM requests").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM licenses").fetchone()[0], 0)
        history = self.data["delivery_repository"].history(request_id=request_id, purpose="admin_notification")
        self.assertEqual([row["status"] for row in history], ["retryable_failed", "succeeded"])
        self.service.issue_activation.assert_not_called()

    async def test_license_write_failure_rolls_back_approval_and_prevents_delivery(self):
        request = self.make_request()
        await self.select(request["request_id"])
        self.message.text = "30"
        self.context.bot.send_message.reset_mock()
        with patch.object(LicenseRepository, "add", side_effect=sqlite3.OperationalError("sensitive-test-sentinel")):
            await bot.receive_form(self.update, self.context)
        self.context.bot.send_message.assert_not_awaited()
        self.assertIsNone(self.data["license_repository"].for_request(request["request_id"]))
        self.assertEqual(self.data["request_repository"].get(request["request_id"])["status"], "pending")
        self.assertEqual(self.message.reply_text.await_args.args[0], bot.STORAGE_FAILURE)

    async def test_attempt_write_failure_prevents_customer_delivery(self):
        request = self.make_request()
        await self.select(request["request_id"])
        self.message.text = "30"
        self.context.bot.send_message.reset_mock()
        with patch.object(self.data["delivery_repository"], "start", side_effect=bot.StorageError("safe failure")):
            await bot.receive_form(self.update, self.context)
        self.context.bot.send_message.assert_not_awaited()
        self.assertIsNotNone(self.data["license_repository"].for_request(request["request_id"]))

    async def test_request_write_failure_prevents_admin_notification(self):
        self.update.effective_user = SimpleNamespace(id=22, full_name="Customer", username=None)
        with patch.object(self.data["request_repository"], "create_or_get_pending", side_effect=bot.StorageError("safe failure")):
            await bot.receive_form(self.update, self.context)
        self.context.bot.send_message.assert_not_awaited()
        self.service.issue_activation.assert_not_called()

    async def test_old_ram_callback_explains_resubmission(self):
        await self.select("old-ram-only-id")
        self.assertEqual(self.update.callback_query.answer.await_args.args[0], bot.MISSING_REQUEST)
        self.assertNotIn("key_form", self.context.user_data)
        self.service.issue_activation.assert_not_called()

    async def test_expired_callback_and_form_never_silently_reissue(self):
        request = self.make_request()
        expired = LicenseService(make_private_key()).issue_activation(
            DEVICE_ID, "Customer", "Professional", 1, datetime(2020, 1, 1, tzinfo=UTC),
        )
        with self.database.transaction() as connection:
            self.data["license_repository"].add(
                expired, days=1, issuer=1, source="telegram_request",
                request_id=request["request_id"], connection=connection,
            )
            self.data["request_repository"].approve(request["request_id"], connection=connection)
        await self.select(request["request_id"])
        self.assertNotIn("key_form", self.context.user_data)
        self.assertTrue(self.update.callback_query.answer.await_args.kwargs["show_alert"])
        self.context.user_data["key_form"] = {
            "step": "days", "device_id": DEVICE_ID, "request_id": request["request_id"],
        }
        self.message.text = "1"
        self.context.bot.send_message.reset_mock()
        await bot.receive_form(self.update, self.context)
        self.context.bot.send_message.assert_not_awaited()
        self.service.issue_activation.assert_not_called()

    async def test_unknown_crash_outcome_retries_stored_key(self):
        request = self.make_request()
        record = self.data["issuance_service"].approve_request(request["request_id"], days=30, issuer=1)
        self.data["delivery_repository"].start(
            request_id=request["request_id"], license_id=record["license_id"],
            destination=222, purpose="customer_license",
        )
        self.reopen()
        self.service.reset_mock()
        await self.select(request["request_id"])
        self.message.text = "30"
        await bot.receive_form(self.update, self.context)
        self.service.issue_activation.assert_not_called()
        self.assertIn(record["activation_key"], self.context.bot.send_message.await_args.kwargs["text"])
        history = self.data["delivery_repository"].history(
            request_id=request["request_id"], license_id=record["license_id"], purpose="customer_license",
        )
        self.assertEqual([row["status"] for row in history], ["attempting", "succeeded"])

    async def test_unhandled_errors_log_only_fixed_category(self):
        context = SimpleNamespace(error=NetworkError("sensitive-test-sentinel"))
        with self.assertLogs(bot.__name__, level="WARNING") as captured:
            await bot.handle_error(self.update, context)
        self.assertNotIn("sensitive-test-sentinel", str(captured.output))
        self.assertIn("network", str(captured.output))

    async def test_manual_retry_after_restart_reuses_persisted_license(self):
        await bot.generate(self.update, self.context)
        with self.database.connection() as connection:
            record = dict(connection.execute("SELECT * FROM licenses").fetchone())
        self.reopen()
        self.service.reset_mock()
        self.context.args = [record["license_id"]]
        await bot.retry_manual(self.update, self.context)
        self.service.issue_activation.assert_not_called()
        self.assertEqual(self.message.reply_text.await_args.args[0], record["activation_key"])
        history = self.data["delivery_repository"].history(
            license_id=record["license_id"], purpose="manual_license",
        )
        self.assertEqual([row["attempt_number"] for row in history], [1, 2])
        self.update.effective_user.id = 2
        self.message.reply_text.reset_mock()
        await bot.retry_manual(self.update, self.context)
        self.assertNotEqual(self.message.reply_text.await_args.args[0], record["activation_key"])

    def test_error_classification_uses_only_safe_categories(self):
        for error, expected in (
            (TimedOut("test"), ("retryable_failed", "timeout")),
            (NetworkError("test"), ("retryable_failed", "network")),
            (RetryAfter(5), ("retryable_failed", "rate_limited")),
            (Forbidden("test"), ("permanent_failed", "forbidden")),
            (BadRequest("test"), ("permanent_failed", "bad_request")),
            (TelegramError("test"), ("permanent_failed", "telegram")),
        ):
            with self.subTest(error_type=type(error)):
                self.assertEqual(bot.delivery_error(error), expected)

    def test_startup_creates_one_service_without_real_config_or_network(self):
        app = Mock(bot_data={})
        # Only synthetic values are supplied; no .env file is opened.
        config = {"TELEGRAM_BOT_TOKEN": "test-only", "TELEGRAM_ADMIN_ID": "1",
                  "VC_PRIVATE_KEY": "temporary-test.pem", "VC_KEY_PASSWORD": "test-only"}
        with (
            patch.object(bot, "load_dotenv"),
            patch.object(bot.os, "getenv", side_effect=lambda name, default=None: config.get(name, default)),
            patch.object(bot, "load_private_key", return_value=object()) as loader,
            patch.object(bot, "LicenseService") as service,
            patch.object(bot, "Application") as application,
            patch.object(bot, "Database", return_value=self.database),
            redirect_stdout(StringIO()),
        ):
            application.builder.return_value.token.return_value.build.return_value = app
            bot.main()
        loader.assert_called_once()
        service.assert_called_once_with(loader.return_value)
        self.assertIs(app.bot_data["license_service"], service.return_value)
        app.run_polling.assert_called_once()


if __name__ == "__main__":
    unittest.main()
