import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from datetime import datetime

from generate_activation import (
    generate_activation_key,
    load_private_key,
    validate_device_id,
)

load_dotenv(Path(__file__).resolve().parent / ".env")


def build_menu(is_admin=False):
    buttons = [
        [
            InlineKeyboardButton(
                "📖 Hướng dẫn kích hoạt",
                callback_data="menu:guide",
            )
        ],
        [
            InlineKeyboardButton(
                "🆔 Cách lấy Device ID",
                callback_data="menu:device",
            )
        ],
        [
            InlineKeyboardButton(
                "💬 Liên hệ hỗ trợ",
                callback_data="menu:support",
            ),
            InlineKeyboardButton(
                "❓ FAQ",
                callback_data="menu:faq",
            ),
        ],
    ]

    if is_admin:
        buttons.append([
            InlineKeyboardButton(
                "🔑 Tạo key — Admin",
                callback_data="menu:generate",
            )
        ])

    return InlineKeyboardMarkup(buttons)


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

    context.user_data.pop("key_form", None)
    
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

    if query.data == "menu:generate" and not is_admin:
        await query.answer(
            "Chỉ Admin được sử dụng chức năng này.",
            show_alert=True,
        )
        return

    await query.answer()
    
    # Selecting any menu item cancels the previous input flow.
    context.user_data.pop("key_form", None)

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

async def generate(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    ):
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat

        if message is None or user is None or chat is None:
            return

        admin_id = context.application.bot_data["admin_id"]

        # Only the administrator can generate keys in a private chat.
        if user.id != admin_id or chat.type != "private":
            await message.reply_text("Bạn không có quyền cấp activation key.")
            return
        # Hủy luồng nhập Machine ID / Days đang dang dở.
        context.user_data.pop("key_form", None)

        if len(context.args) != 2:
            await message.reply_text(
                "Cách dùng:\n"
                "/generate DEVICE_ID DAYS\n\n"
                "Ví dụ:\n"
                "/generate VC-06CF-43DB-D96E-1BED-B3E0 30"
            )
            return

        device_id, days_text = context.args

        try:
            days = int(days_text)
            if not 1 <= days <= 3650:
                raise ValueError("Số ngày phải từ 1 đến 3650.")

            activation_key, payload = generate_activation_key(
                device_id=device_id,
                customer="Test Customer",
                edition="Professional",
                days=days,
                private_key=context.application.bot_data["private_key"],
            )
        except ValueError as error:
            await message.reply_text(f"Không tạo được key: {error}")
            return

        expires_local = datetime.fromisoformat(
            payload["expires_at"].replace("Z", "+00:00")
        ).astimezone()

        await message.reply_text(
            f"Device ID: {payload['device_id']}\n"
            f"Edition: {payload['edition']}\n"
            f"Duration: {days} ngày\n"
            f"Expires: {expires_local:%d/%m/%Y %H:%M:%S %z}"
        )

        # Send the key separately for easy copying.
        await message.reply_text(activation_key)

async def cancel_form(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop("key_form", None)

    if update.effective_message is not None:
        await update.effective_message.reply_text("Đã hủy nhập thông tin.")

    await start(update, context)


async def receive_form(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if message is None or user is None or chat is None:
        return

    # Check permission again when processing each input.
    if (
        user.id != context.application.bot_data["admin_id"]
        or chat.type != "private"
    ):
        return

    form = context.user_data.get("key_form")
    if form is None:
        await message.reply_text(
            "Gửi /menu rồi chọn Tạo key — Admin để bắt đầu."
        )
        return

    text = (message.text or "").strip()

    if form["step"] == "device":
        try:
            device_id = validate_device_id(text)
        except ValueError:
            await message.reply_text(
                "Machine ID không đúng định dạng. Hãy dán lại:\n"
                "VC-XXXX-XXXX-XXXX-XXXX-XXXX"
            )
            return

        form["device_id"] = device_id
        form["step"] = "days"

        await message.reply_text(
            f"Machine ID: {device_id}\n\n"
            "Bước 2/2: Nhập số ngày, ví dụ 30.\n"
            "Cho phép từ 1 đến 3650 ngày.\n"
            "Gửi /cancel để hủy."
        )
        return

    if form["step"] != "days":
        return

    try:
        days = int(text)
        if not 1 <= days <= 3650:
            raise ValueError
    except ValueError:
        await message.reply_text(
            "Hãy nhập số nguyên từ 1 đến 3650, ví dụ: 30."
        )
        return

    try:
        activation_key, payload = generate_activation_key(
            device_id=form["device_id"],
            customer="Test Customer",
            edition="Professional",
            days=days,
            private_key=context.application.bot_data["private_key"],
        )
    except ValueError as error:
        await message.reply_text(f"Không tạo được key: {error}")
        return

    # Finish the input flow before sending the result.
    context.user_data.pop("key_form", None)

    expires_local = datetime.fromisoformat(
        payload["expires_at"].replace("Z", "+00:00")
    ).astimezone()

    await message.reply_text(
        "✅ Đã tạo activation key\n\n"
        f"Machine ID: {payload['device_id']}\n"
        f"Edition: {payload['edition']}\n"
        f"Duration: {days} ngày\n"
        f"Expires: {expires_local:%d/%m/%Y %H:%M:%S %z}"
    )

    await message.reply_text(activation_key)

    await message.reply_text(
        "Chọn chức năng tiếp theo:",
        reply_markup=build_menu(is_admin=True),
    )

def main():
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

    app = Application.builder().token(token).build()

    app.bot_data["admin_id"] = admin_id
    app.bot_data["private_key"] = private_key

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("generate", generate))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(
        CallbackQueryHandler(menu_click, pattern=r"^menu:")
    )

    print("Bot đang chạy. Nhấn Ctrl+C để dừng.")
    app.add_handler(CommandHandler("cancel", cancel_form))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_form,
        )
    )
    app.run_polling()


if __name__ == "__main__":
    main()