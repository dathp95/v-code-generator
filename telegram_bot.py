import os
import logging
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
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut

from datetime import datetime

from core.crypto.activation import validate_device_id
from core.crypto.key_loader import load_private_key
from core.services.license_service import LicenseService
from services.issuance_service import IssuanceService, RequestUnavailable, require_unexpired
from storage.database import Database, StorageError
from storage.repositories.delivery_repository import DeliveryRepository
from storage.repositories.license_repository import LicenseRepository
from storage.repositories.request_repository import RequestRepository


STORAGE_FAILURE = "Không lưu được dữ liệu. Chưa xác nhận gửi key; kiểm tra DB trước khi thử lại."
MISSING_REQUEST = "Không tìm thấy yêu cầu đã lưu (yêu cầu RAM cũ không thể khôi phục). Hãy nhờ khách gửi lại Device ID."


async def handle_error(update, context):
    # PTB's default error handler logs the update and raw exception. Neither is safe here.
    if isinstance(context.error, TelegramError):
        category = delivery_error(context.error)[1]
    elif isinstance(context.error, StorageError):
        category = "storage"
    else:
        category = "unexpected"
    logging.getLogger(__name__).warning("Telegram update failed (%s).", category)


def configure_storage(bot_data, license_service, database):
    database.initialize()
    bot_data["license_service"] = license_service
    bot_data["request_repository"] = RequestRepository(database)
    bot_data["license_repository"] = LicenseRepository(database)
    bot_data["delivery_repository"] = DeliveryRepository(database)
    bot_data["issuance_service"] = IssuanceService(license_service, database)


def delivery_error(error):
    """Only fixed categories enter storage; never retain exception text."""
    if isinstance(error, TimedOut):
        return "retryable_failed", "timeout"
    if isinstance(error, RetryAfter):
        return "retryable_failed", "rate_limited"
    if isinstance(error, Forbidden):
        return "permanent_failed", "forbidden"
    if isinstance(error, BadRequest):
        return "permanent_failed", "bad_request"
    if isinstance(error, NetworkError):
        return "retryable_failed", "network"
    return "permanent_failed", "telegram"


async def recorded_send(deliveries, *, destination, purpose, send, request_id=None, license_id=None):
    # start() commits before the network await; finish() uses a separate transaction.
    attempt = deliveries.start(
        destination=destination, purpose=purpose, request_id=request_id, license_id=license_id,
    )
    try:
        sent = await send()
    except TelegramError as error:
        status, category = delivery_error(error)
        deliveries.finish(attempt, status=status, error_category=category)
        return status
    deliveries.finish(attempt, status="succeeded", message_id=getattr(sent, "message_id", None))
    return "succeeded"


def license_summary(record, label="Machine ID"):
    payload = record["payload"]
    expires_local = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00")).astimezone()
    return (
        f"{label}: {payload['device_id']}\n"
        f"Edition: {payload['edition']}\n"
        f"Duration: {record['duration_days']} ngày\n"
        f"Expires: {expires_local:%d/%m/%Y %H:%M:%S %z}"
    )


async def deliver_manual(record, update, context):
    require_unexpired(record)
    data = context.application.bot_data
    deliveries = data["delivery_repository"]
    async def send():
        await update.effective_message.reply_text(
            f"{license_summary(record, 'Device ID')}\nLicense ID: {record['license_id']}"
        )
        return await update.effective_message.reply_text(record["activation_key"])

    status = await recorded_send(
        deliveries, destination=update.effective_chat.id, purpose="manual_license",
        license_id=record["license_id"],
        send=send,
    )
    if status != "succeeded":
        detail = "Lỗi mạng/tạm thời." if status == "retryable_failed" else "Kiểm tra quyền gửi/chat trước khi thử lại."
        await update.effective_message.reply_text(
            f"Chưa gửi được key. {detail} Dùng /retry {record['license_id']} để gửi cùng key."
        )
    return status


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

async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if message is None or user is None or chat is None:
        return
    if user.id != context.application.bot_data["admin_id"] or chat.type != "private":
        await message.reply_text("Bạn không có quyền cấp activation key.")
        return
    context.user_data.pop("key_form", None)
    if len(context.args) != 2:
        await message.reply_text(
            "Cách dùng:\n/generate DEVICE_ID DAYS\n\n"
            "Ví dụ:\n/generate VC-06CF-43DB-D96E-1BED-B3E0 30"
        )
        return
    device_id, days_text = context.args
    try:
        record = context.application.bot_data["issuance_service"].issue_manual(
            device_id=device_id, customer="Test Customer", edition="Professional",
            days=int(days_text), issuer=user.id, source="telegram_manual",
        )
        await deliver_manual(record, update, context)
    except StorageError:
        await message.reply_text(STORAGE_FAILURE)
    except ValueError:
        await message.reply_text("Không tạo được key. Kiểm tra Device ID và số ngày từ 1 đến 3650.")


async def retry_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if message is None or user is None or chat is None:
        return
    if user.id != context.application.bot_data["admin_id"] or chat.type != "private":
        await message.reply_text("Bạn không có quyền cấp activation key.")
        return
    if len(context.args) != 1:
        await message.reply_text("Cách dùng: /retry LICENSE_ID (key tạo thủ công).")
        return
    try:
        record = context.application.bot_data["license_repository"].get(context.args[0])
        if record is None or record["source"] != "telegram_manual" or record["issuer"] != str(user.id):
            await message.reply_text("Không tìm thấy key tạo thủ công của Admin.")
            return
        await deliver_manual(record, update, context)
    except StorageError:
        await message.reply_text(STORAGE_FAILURE)
    except RequestUnavailable as error:
        await message.reply_text(str(error))


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

    if chat.type != "private":
        return

    admin_id = context.application.bot_data["admin_id"]
    text = (message.text or "").strip()

    # KHÁCH: gửi yêu cầu kích hoạt tới Admin.
    if user.id != admin_id:
        try:
            device_id = validate_device_id(text)
        except ValueError:
            await message.reply_text(
                "Để yêu cầu kích hoạt, hãy dán Machine ID từ V-CODE:\n"
                "License Support → Copy ID.\n\n"
                "Định dạng: VC-XXXX-XXXX-XXXX-XXXX-XXXX"
            )
            return

        username = (
            f"@{user.username}"
            if user.username
            else "Không có username"
        )
        
        data = context.application.bot_data
        try:
            request = data["request_repository"].create_or_get_pending(
                channel="telegram", recipient_id=chat.id, user_id=user.id,
                customer=user.full_name, device_id=device_id,
            )
            request_id = request["request_id"]
            deliveries = data["delivery_repository"]
            if not deliveries.succeeded(request_id=request_id, purpose="admin_notification"):
                status = await recorded_send(
                    deliveries, destination=admin_id, purpose="admin_notification", request_id=request_id,
                    send=lambda: context.bot.send_message(
                        chat_id=admin_id,
                        text=(
                            "📩 YÊU CẦU KÍCH HOẠT V-CODE\n\n"
                            f"Khách: {request['customer']}\n"
                            f"Username: {username}\n"
                            f"Telegram User ID: {user.id}\n"
                            f"Machine ID: {device_id}\n\n"
                            "Chọn Tạo key — Admin để cấp license."
                        ),
                        parse_mode=None,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🔑 Tạo key — Admin", callback_data=f"request:{request_id}")
                        ]]),
                    ),
                )
                if status != "succeeded":
                    await message.reply_text(
                        "⚠️ Chưa gửi được yêu cầu tới Admin.\n"
                        "Yêu cầu đã lưu. Gửi lại Device ID để thử thông báo lại; không tạo yêu cầu trùng."
                    )
                    return
        except StorageError:
            await message.reply_text(STORAGE_FAILURE)
            return

        await message.reply_text(
            "✅ Yêu cầu đã được gửi tới Admin.\n\n"
            f"Machine ID: {device_id}\n\n"
            "Vui lòng chờ người hỗ trợ liên hệ.",
            reply_markup=build_menu(is_admin=False),
        )
        return

    # ADMIN: xử lý luồng nhập Machine ID → Days.
    form = context.user_data.get("key_form")

    if form is None:
        await message.reply_text(
            "Gửi /menu rồi chọn Tạo key — Admin để bắt đầu.",
            reply_markup=build_menu(is_admin=True),
        )
        return

    if form.get("step") == "device":
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

    if form.get("step") != "days":
        context.user_data.pop("key_form", None)
        await message.reply_text(
            "Phiên nhập không hợp lệ. Gửi /menu để bắt đầu lại."
        )
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

    request_id = form.get("request_id")
    data = context.application.bot_data
    try:
        if request_id:
            request = data["request_repository"].get(request_id)
            if request is None:
                context.user_data.pop("key_form", None)
                await message.reply_text(MISSING_REQUEST)
                return
            record = data["issuance_service"].approve_request(request_id, days=days, issuer=user.id)
            deliveries = data["delivery_repository"]
            if deliveries.succeeded(
                request_id=request_id, license_id=record["license_id"], purpose="customer_license",
            ):
                context.user_data.pop("key_form", None)
                await message.reply_text("Key đã được gửi cho yêu cầu này.")
                return
            summary = license_summary(record)
            status = await recorded_send(
                deliveries, destination=request["recipient_id"], purpose="customer_license",
                request_id=request_id, license_id=record["license_id"],
                send=lambda: context.bot.send_message(
                    chat_id=int(request["recipient_id"]),
                    text=(
                        "✅ License V-CODE của bạn đã được cấp!\n\n"
                        f"{summary}\n\nActivation Key:\n{record['activation_key']}\n\n"
                        "Copy toàn bộ chuỗi key ở trên.\n"
                        "Mở V-CODE → License Support → dán vào Activation Key → Activate."
                    ),
                    parse_mode=None,
                ),
            )
            if status != "succeeded":
                detail = (
                    "Lỗi mạng/tạm thời."
                    if status == "retryable_failed"
                    else "Lỗi gửi cố định; kiểm tra khách chặn bot hoặc chat không hợp lệ trước khi thử lại."
                )
                await message.reply_text(
                    f"⚠️ Đã lưu key nhưng chưa xác nhận gửi thành công. {detail}\n"
                    f"Nhập lại {days} để thử gửi cùng key; sau khởi động lại, chọn lại nút yêu cầu."
                )
                return
            context.user_data.pop("key_form", None)
            await message.reply_text(
                f"✅ Đã gửi key cho {request['customer']}.\n\n{summary}",
                reply_markup=build_menu(is_admin=True),
            )
            return

        # Manual issuance is recorded too; a form retry retains the persisted license ID.
        record = data["license_repository"].get(form["license_id"]) if form.get("license_id") else None
        if form.get("license_id") and record is None:
            raise RequestUnavailable("Không tìm thấy key đã lưu. Kiểm tra DB trước khi tạo key mới.")
        if record is None:
            record = data["issuance_service"].issue_manual(
                device_id=form["device_id"], customer="Test Customer", edition="Professional",
                days=days, issuer=user.id, source="telegram_manual",
            )
            form["license_id"] = record["license_id"]
        elif days != record["duration_days"]:
            await message.reply_text(f"Nhập lại {record['duration_days']} ngày để gửi cùng key.")
            return
        if await deliver_manual(record, update, context) == "succeeded":
            context.user_data.pop("key_form", None)
            await message.reply_text("Chọn chức năng tiếp theo:", reply_markup=build_menu(is_admin=True))
    except StorageError:
        await message.reply_text(STORAGE_FAILURE)
    except RequestUnavailable as error:
        await message.reply_text(str(error))
    except ValueError:
        await message.reply_text("Không tạo được key. Kiểm tra Device ID và số ngày từ 1 đến 3650.")


async def select_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    chat = update.effective_chat

    if query is None:
        return

    admin_id = context.application.bot_data["admin_id"]

    if (
        query.from_user.id != admin_id
        or chat is None
        or chat.type != "private"
    ):
        await query.answer("Chỉ Admin được cấp key.", show_alert=True)
        return

    request_id = (query.data or "").split(":", 1)[-1]
    data = context.application.bot_data
    try:
        request = data["request_repository"].get(request_id)
        if request is None:
            await query.answer(MISSING_REQUEST, show_alert=True)
            return
        record = data["license_repository"].for_request(request_id)
        if request["status"] == "closed":
            await query.answer("Yêu cầu đã đóng. Hãy nhờ khách gửi lại Device ID.", show_alert=True)
            return
        if record is not None:
            require_unexpired(record)
            if data["delivery_repository"].succeeded(
                request_id=request_id, license_id=record["license_id"], purpose="customer_license",
            ):
                await query.answer("Key đã được gửi cho yêu cầu này.", show_alert=True)
                return
    except StorageError:
        await query.answer(STORAGE_FAILURE, show_alert=True)
        return
    except RequestUnavailable as error:
        await query.answer(str(error), show_alert=True)
        return

    await query.answer()

    context.user_data["key_form"] = {
        "step": "days",
        "device_id": request["device_id"],
        "request_id": request_id,
    }

    await context.bot.send_message(
        chat_id=chat.id,
        text=(
            f"Khách: {request['customer']}\n"
            f"Machine ID: {request['device_id']}\n\n"
            "Nhập số ngày từ 1 đến 3650.\n"
            "Bot sẽ gửi key trực tiếp cho khách sau khi tạo.\n"
            "Gửi /cancel để hủy."
        ),
    )

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

    app = Application.builder().token(token).build()

    app.bot_data["admin_id"] = admin_id
    try:
        configure_storage(app.bot_data, LicenseService(private_key), Database())
    except StorageError:
        raise SystemExit("Không mở được DB license. Kiểm tra đường dẫn và quyền truy cập.") from None

    app.add_error_handler(handle_error)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("generate", generate))
    app.add_handler(CommandHandler("retry", retry_manual))
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
    app.add_handler(
        CallbackQueryHandler(select_request, pattern=r"^request:")
    )
    app.run_polling()


if __name__ == "__main__":
    main()
