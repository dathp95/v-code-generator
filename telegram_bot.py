"""Compatible standalone Telegram entry point; managed mode never loads .env."""
import os
import logging
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from telegram.error import TelegramError

from datetime import datetime

from core.crypto.activation import validate_device_id
from core.crypto.key_loader import load_private_key
from core.services.license_service import LicenseService
from services.approval_service import ApprovalService, STALE_FORM
from services.delivery_service import delivery_error, recorded_send
from services.issuance_service import IssuanceService, RequestUnavailable, require_unexpired
from storage.database import Database, StorageError
from storage.repositories.delivery_repository import DeliveryBlocked, DeliveryRepository
from storage.repositories.license_repository import LicenseRepository
from storage.repositories.request_repository import RequestRepository


from dotenv import load_dotenv
from integrations.telegram.handlers.workflow import *
from integrations.telegram.handlers.navigation import start, menu_click, myid
from integrations.telegram.keyboards import build_menu
from integrations.telegram.application import build_application, register_handlers

def main():
    load_dotenv(Path(__file__).resolve().parent / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    admin_text = os.getenv("TELEGRAM_ADMIN_ID", "").strip()
    key_path = os.getenv("VC_PRIVATE_KEY", "").strip()
    password = os.getenv("VC_KEY_PASSWORD", "")

    if not all((token, admin_text, key_path, password)):
        raise SystemExit(
            "Thiếu cấu hình token, Admin ID, private key hoặc mật khẩu "
            "trong .env"
        )

    try:
        admin_id = int(admin_text)
        if admin_id <= 0:
            raise ValueError
    except ValueError:
        raise SystemExit("TELEGRAM_ADMIN_ID phải là User ID dạng số dương.")

    try:
        private_key = load_private_key(
            Path(key_path),
            password.encode("utf-8"),
        )
    except ValueError:
        raise SystemExit(
            "Không mở được private key. Kiểm tra đường dẫn và mật khẩu."
        )

    try:
        app = build_application(token, admin_id, LicenseService(private_key), Database(),
                                application_factory=Application)
    except StorageError:
        raise SystemExit("Không mở được DB license. Kiểm tra đường dẫn và quyền truy cập.") from None

    print("Bot đang chạy. Nhấn Ctrl+C để dừng.")
    app.run_polling()

if __name__ == "__main__":
    main()
