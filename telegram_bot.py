import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
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


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "Chưa cấu hình TELEGRAM_BOT_TOKEN trong .env"
        )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))

    print("Bot đang chạy. Nhấn Ctrl+C để dừng.")
    app.run_polling()


if __name__ == "__main__":
    main()