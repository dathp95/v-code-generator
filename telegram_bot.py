import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from datetime import datetime

from generate_activation import (
    generate_activation_key,
    load_private_key,
)

load_dotenv(Path(__file__).resolve().parent / ".env")


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_message is None:
        return

    await update.effective_message.reply_text(
        "V-CODE bot đã kết nối!\n"
        "Gửi /myid để lấy Telegram User ID của bạn."
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

    print("Bot đang chạy. Nhấn Ctrl+C để dừng.")
    app.run_polling()


if __name__ == "__main__":
    main()