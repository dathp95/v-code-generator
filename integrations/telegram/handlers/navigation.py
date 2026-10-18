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


from integrations.telegram.keyboards import build_menu
from integrations.telegram.handlers.workflow import invalidate_form

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if message is None or user is None or chat is None:
        return

    if chat.type != "private":
        await message.reply_text("Vui lòng mở chat riêng với bot.")
        return

    is_admin = user.id == context.application.bot_data["admin_id"]

    invalidate_form(update, context)

    await message.reply_text(
        "🚗 Chào mừng bạn đến với V-CODE!\n\n"
        "Chọn chức năng bên dưới để tiếp tục.",

        reply_markup=build_menu(is_admin),
    )

async def menu_click(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    chat = update.effective_chat

    if query is None:
        return

    is_admin = (
        query.from_user.id == context.application.bot_data["admin_id"]
    )

    if chat is None or chat.type != "private":
        await query.answer(
            "Vui lòng sử dụng bot trong chat riêng.",
            show_alert=True,
        )
        return

    if query.data == 'menu:subscribe':
        from integrations.telegram.handlers.subscription import begin
        await begin(update, context)
        return

    if query.data == "menu:generate" and not is_admin:
        await query.answer(
            "Chỉ Admin được sử dụng chức năng này.",
            show_alert=True,
        )
        return

    await query.answer()

    # Selecting any menu item cancels the previous input flow.
    invalidate_form(update, context)

    if query.data == "menu:generate":
        context.user_data["key_form"] = {"step": "device"}

        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                "🔑 TẠO ACTIVATION KEY\n\n"
                "Bước 1/2: Dán Machine ID cần kích hoạt.\n\n"
                "Ví dụ: VC-06CF-43DB-D96E-1BED-B3E0\n"
                "Gửi /cancel để hủy."
            ),
        )
        return

    content = {
        "menu:guide": (
            "📖 HƯỚNG DẪN KÍCH HOẠT\n\n"
            "1. Mở V-CODE → License Support.\n"
            "2. Nhấn Copy ID để lấy Device ID.\n"
            "3. Gửi Device ID cho người hỗ trợ cấp license.\n"
            "4. Khi nhận key, copy toàn bộ chuỗi key.\n"
            "5. Dán vào ô Activation Key → nhấn Activate.\n\n"
            "Khi thành công, trạng thái hiển thị Activated "
            "và ngày hết hạn được cập nhật."
        ),
        "menu:device": (
            "🆔 CÁCH LẤY DEVICE ID\n\n"
            "Mở V-CODE → License Support → Copy ID.\n\n"
            "Ví dụ:\nVC-06CF-43DB-D96E-1BED-B3E0\n\n"
            "Lấy ID trên chính máy cần kích hoạt."
        ),
        "menu:support": (
            "💬 LIÊN HỆ HỖ TRỢ\n\n"
            "Email: tranducdat.eng@gmail.com\n\n"
            "Vui lòng gửi Device ID, tên khách hàng "
            "và thời hạn muốn đăng ký.\n\n"
            "Nếu gặp lỗi, gửi thêm ảnh thông báo lỗi."
        ),
        "menu:faq": (
            "❓ CÂU HỎI THƯỜNG GẶP\n\n"
            "• Key dùng được cho máy khác không?\n"
            "Key chỉ dùng cho Device ID đã được cấp.\n\n"
            "• Thời hạn tính từ khi nào?\n"
            "Tính từ lúc key được tạo.\n\n"
            "• Gia hạn như thế nào?\n"
            "Liên hệ hỗ trợ để nhận key mới, rồi Activate lại.\n\n"
            "• Cần Internet khi Activate không?\n"
            "V-CODE kiểm tra key offline; Telegram cần Internet."
        ),
        "menu:generate": (
            "🔑 TẠO KEY — ADMIN\n\n"
            "Gửi lệnh theo mẫu:\n"
            "/generate DEVICE_ID DAYS\n\n"
            "Ví dụ:\n"
            "/generate VC-06CF-43DB-D96E-1BED-B3E0 30\n\n"
            "Edition: Professional\n"
            "Customer: Test Customer\n"
            "Số ngày: từ 1 đến 3650."
        ),
    }

    text = content.get(query.data)
    if text is None:
        return

    await context.bot.send_message(
        chat_id=chat.id,
        text=text,
        reply_markup=build_menu(is_admin),
    )

async def myid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if (
        update.effective_message is None
        or update.effective_user is None
    ):
        return

    await update.effective_message.reply_text(
        f"Telegram User ID: {update.effective_user.id}"
    )
