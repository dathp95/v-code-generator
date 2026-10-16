"""Offline handler tests; all delivery and startup dependencies are mocked."""

import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram.error import TelegramError

import telegram_bot as bot
from core.services.license_service import LicenseService
from test_activation import DEVICE_ID, make_private_key


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = Mock(wraps=LicenseService(make_private_key()))
        self.data = {"admin_id": 1, "license_service": self.service}
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
        request_id, request = next(iter(self.data["activation_requests"].items()))
        self.assertEqual(request["chat_id"], 222)
        self.assertEqual(request["status"], "pending")
        self.assertEqual(self.context.bot.send_message.await_args.kwargs["chat_id"], 1)
        callback = self.context.bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        self.assertEqual(callback, f"request:{request_id}")
        # A separate pending request must never affect this request's recipient.
        self.data["activation_requests"]["other"] = dict(request, chat_id=333)
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
        self.assertEqual(request["status"], "pending")
        cached_key = request["activation_key"]
        cached_payload = request["payload"]
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
        self.assertEqual(request["activation_key"], cached_key)
        self.assertIs(request["payload"], cached_payload)
        self.assertEqual(request["status"], "sent")
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
