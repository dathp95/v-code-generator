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
    logging.getLogger("telegram_bot").warning("Telegram update failed (%s).", category)

def configure_storage(bot_data, license_service, database):
    database.initialize()
    bot_data["license_service"] = license_service
    bot_data["request_repository"] = RequestRepository(database)
    bot_data["license_repository"] = LicenseRepository(database)
    bot_data["delivery_repository"] = DeliveryRepository(database)
    bot_data["issuance_service"] = IssuanceService(license_service, database)
    bot_data["approval_service"] = ApprovalService(bot_data["issuance_service"])
    bot_data["delivery_repository"].recover_stale()
    from services.subscription_service import SubscriptionService
    bot_data['subscription_service'] = SubscriptionService(database, bot_data['admin_id'])
    bot_data['subscription_service'].recover_notifications()

def blocked_message(error):
    messages = {
        "already_succeeded": "Key/thông báo đã gửi thành công; không gửi lại.",
        "in_progress": "Đang có lượt gửi khác. Chờ kết thúc hoặc hết lease 120 giây rồi kiểm tra /requests.",
        "unknown": "Kết quả gửi chưa rõ; gửi lại có thể trùng tin. Admin cần xác nhận trong /requests.",
        "permanent_failed": "Lỗi gửi cố định. Sửa quyền gửi/chat rồi Admin xác nhận thử lại.",
        "lost_claim": "Lượt gửi đã mất quyền hoàn tất; kết quả chưa rõ. Kiểm tra /requests trước khi thử lại.",
        "state_changed": "Trạng thái gửi đã thay đổi. Mở /requests, xem lại và xác nhận mới trước khi gửi.",
        "cooldown": f"Telegram yêu cầu chờ đến {error.retry_at} trước khi thử lại.",
    }
    return messages.get(error.code, "Chưa thể gửi. Kiểm tra /requests.")

def invalidate_form(update, context):
    user, chat = update.effective_user, update.effective_chat
    if user and chat and user.id == context.application.bot_data["admin_id"]:
        context.application.bot_data["approval_service"].invalidate(user.id, chat.id)
    context.user_data.pop("key_form", None)
    context.user_data.pop('subscription_form', None)

def license_summary(record, label="Machine ID"):
    payload = record["payload"]
    expires_local = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00")).astimezone()
    return (
        f"{label}: {payload['device_id']}\n"
        f"Edition: {payload['edition']}\n"
        f"Duration: {record['duration_days']} ngày\n"
        f"Expires: {expires_local:%d/%m/%Y %H:%M:%S %z}"
    )

async def deliver_manual(record, update, context, *, explicit_retry=False):
    require_unexpired(record, context.application.bot_data["issuance_service"].database.now())
    data = context.application.bot_data
    emit_records(data, license_id=record["license_id"], status="issued")
    deliveries = data["delivery_repository"]
    async def send():
        await update.effective_message.reply_text(
            f"{license_summary(record, 'Device ID')}\nLicense ID: {record['license_id']}"
        )
        return await update.effective_message.reply_text(record["activation_key"])

    status = await recorded_send(
        deliveries, destination=update.effective_chat.id, purpose="manual_license",
        license_id=record["license_id"],
        send=send, explicit_retry=explicit_retry,
    )
    emit_records(data, license_id=record["license_id"], status=status)
    if status != "succeeded":
        detail = (
            "Kết quả chưa rõ; gửi lại có thể trùng tin."
            if status == "unknown" else "Kiểm tra lỗi gửi/chat trước khi thử lại."
        )
        await update.effective_message.reply_text(
            f"Chưa gửi được key. {detail} Dùng /retry {record['license_id']} để gửi cùng key."
        )
    return status


def emit_records(data, **values):
    callback = data.get("desktop_event")
    if callback:
        callback(**values)

async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if message is None or user is None or chat is None:
        return
    if user.id != context.application.bot_data["admin_id"] or chat.type != "private":
        await message.reply_text("Bạn không có quyền cấp activation key.")
        return
    invalidate_form(update, context)
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
    except DeliveryBlocked as error:
        await message.reply_text(blocked_message(error))
    except ValueError:
        await message.reply_text("Không tạo được key. Kiểm tra Device ID và số ngày từ 1 đến 3650.")

async def retry_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if message is None or user is None or chat is None:
        return
    if user.id != context.application.bot_data["admin_id"] or chat.type != "private":
        await message.reply_text("Bạn không có quyền cấp activation key.")
        return
    if len(context.args) not in (1, 2) or (len(context.args) == 2 and context.args[1] != "confirm"):
        await message.reply_text("Cách dùng: /retry LICENSE_ID [confirm] (key tạo thủ công).")
        return
    try:
        record = context.application.bot_data["license_repository"].get(context.args[0])
        if record is None or record["source"] != "telegram_manual" or record["issuer"] != str(user.id):
            await message.reply_text("Không tìm thấy key tạo thủ công của Admin.")
            return
        latest = context.application.bot_data["delivery_repository"].latest(
            license_id=record["license_id"], purpose="manual_license",
        )
        if latest and latest["status"] in ("unknown", "permanent_failed") and len(context.args) == 1:
            await message.reply_text(
                "Kết quả chưa rõ hoặc lỗi cố định. Sửa lỗi trước; gửi lại có thể trùng tin.\n"
                f"Xác nhận thủ công: /retry {record['license_id']} confirm"
            )
            return
        await deliver_manual(record, update, context, explicit_retry=len(context.args) == 2)
    except StorageError:
        await message.reply_text(STORAGE_FAILURE)
    except DeliveryBlocked as error:
        await message.reply_text(blocked_message(error))
    except RequestUnavailable as error:
        await message.reply_text(str(error))

async def cancel_form(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    invalidate_form(update, context)

    if update.effective_message is not None:
        await update.effective_message.reply_text("Đã hủy nhập thông tin.")

    from integrations.telegram.handlers.navigation import start
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

    if context.user_data.get('subscription_form'):
        from integrations.telegram.handlers.subscription import receive_input
        await receive_input(update, context)
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
            emit_records(data, request_id=request_id, status="submitted")
            existing = data["license_repository"].for_request(request_id)
            if existing:
                sent = data["delivery_repository"].succeeded(
                    request_id=request_id, license_id=existing["license_id"], purpose="customer_license",
                )
                await message.reply_text(
                    "Key đã được cấp và gửi thành công."
                    if sent else "Key đã được cấp; đang chờ Admin xử lý gửi key."
                )
                return
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
        except DeliveryBlocked as error:
            await message.reply_text("Yêu cầu đã lưu, đang chờ duyệt. " + blocked_message(error))
            return
        except StorageError:
            await message.reply_text(STORAGE_FAILURE)
            return

        await message.reply_text(
            "✅ Yêu cầu đã lưu, đang chờ Admin duyệt.\n\n"
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

    if form.get("step") == "reject_reason":
        try:
            preview = context.application.bot_data["approval_service"].prepare(
                form.get("selection_id"), admin_id=user.id, chat_id=chat.id, reason=text,
            )
            context.user_data["key_form"] = {"step": "confirm", "form_id": preview["form_id"]}
            await show_confirmation(message, preview, reason=text)
        except (RequestUnavailable, StorageError) as error:
            await message.reply_text(STORAGE_FAILURE if isinstance(error, StorageError) else str(error))
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
        invalidate_form(update, context)
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
            preview = data["approval_service"].prepare(
                form.get("selection_id"), admin_id=user.id, chat_id=chat.id, days=days,
            )
            context.user_data["key_form"] = {"step": "confirm", "form_id": preview["form_id"]}
            await show_confirmation(message, preview, days=days)
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
        if await deliver_manual(record, update, context, explicit_retry=bool(form.get("license_id"))) == "succeeded":
            context.user_data.pop("key_form", None)
            await message.reply_text("Chọn chức năng tiếp theo:", reply_markup=build_menu(is_admin=True))
    except DeliveryBlocked as error:
        await message.reply_text(blocked_message(error))
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
        data["approval_service"].invalidate(query.from_user.id, chat.id)
        context.user_data.pop("key_form", None)
        request = data["request_repository"].get(request_id)
        if request is None:
            await query.answer(MISSING_REQUEST, show_alert=True)
            return
        record = data["license_repository"].for_request(request_id)
        if request["status"] == "closed":
            await query.answer("Yêu cầu đã đóng. Hãy nhờ khách gửi lại Device ID.", show_alert=True)
            return
        if record is not None:
            require_unexpired(record, data["issuance_service"].database.now())
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

    try:
        selection_id = data["approval_service"].select(request_id, admin_id=query.from_user.id, chat_id=chat.id)
    except (RequestUnavailable, StorageError) as error:
        await query.answer(STORAGE_FAILURE if isinstance(error, StorageError) else str(error), show_alert=True)
        return
    await query.answer()

    context.user_data["key_form"] = {
        "step": "days",
        "device_id": request["device_id"],
        "request_id": request_id,
        "selection_id": selection_id,
    }

    order = data['subscription_service'].orders.for_request(request_id)
    suggestion = f"Đơn {order['reference']} · đã mua {order['purchased_days']} ngày. Gợi ý Days: {order['purchased_days']}.\n" if order else ''

    await context.bot.send_message(
        chat_id=chat.id,
        text=(
            f"Khách: {request['customer']}\n"
            f"Machine ID: {request['device_id']}\n\n"
            f"{suggestion}"
            "Nhập số ngày từ 1 đến 3650.\n"
            "Tiếp theo sẽ xem lại và bấm Confirm trước khi cấp/gửi key.\n"
            "Gửi /cancel để hủy."
        ),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Từ chối yêu cầu", callback_data=f"reject:{selection_id}"),
            InlineKeyboardButton("Cancel", callback_data=f"decision:cancel:{selection_id}"),
        ]]) if record is None else None,
    )

async def show_confirmation(message, preview, *, days=None, reason=None):
    request, record, delivery = preview["request"], preview["license"], preview["delivery"]
    text = (
        f"Khách: {request['customer']}\nDevice ID: {request['device_id']}\n"
        f"Recipient: {request['recipient_id']}\nEdition: {record['edition'] if record else 'Professional'}\n"
    )
    label = "Confirm"
    if preview["action"] == "reject":
        text += f"Từ chối yêu cầu chưa cấp key.\nLý do: {reason}\nKhông thu hồi license offline."
    else:
        if request.get('subscription_days'):
            text += f"Đơn: {request['order_reference']}\nĐã mua: {request['subscription_days']} ngày · Thực cấp: {days} ngày\n"
        text += f"Duration: {days} ngày\nDùng lại key đã lưu: {'Có' if record else 'Không, sẽ cấp key mới'}\n"
        if delivery and delivery["status"] == "unknown":
            text += "Kết quả lần gửi trước chưa rõ. Thử lại có thể tạo tin nhắn trùng với cùng key.\n"
            label = "Confirm retry (may duplicate)"
        if delivery and delivery["status"] == "permanent_failed":
            text += "Lỗi gửi cố định: hãy sửa quyền gửi/chat trước khi xác nhận thử lại.\n"
        if delivery and delivery["retry_not_before"]:
            text += f"Chỉ được thử lại sau {delivery['retry_not_before']}.\n"
        text += "Chỉ bấm Confirm khi đồng ý cấp/gửi key tới recipient trên."
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup([[
        InlineKeyboardButton(label, callback_data=f"decision:confirm:{preview['form_id']}"),
        InlineKeyboardButton("Cancel", callback_data=f"decision:cancel:{preview['form_id']}"),
    ]]))

def is_private_admin(update, context):
    user = update.callback_query.from_user if update.callback_query else update.effective_user
    chat = update.effective_chat
    return user is not None and chat is not None and chat.type == "private" and user.id == context.application.bot_data["admin_id"]

async def deliver_customer(request, record, update, context):
    require_unexpired(record, context.application.bot_data["issuance_service"].database.now())
    emit_records(context.application.bot_data, request_id=request["request_id"], license_id=record["license_id"], status="issued")
    status = await recorded_send(
        context.application.bot_data["delivery_repository"], destination=request["recipient_id"],
        purpose="customer_license", request_id=request["request_id"], license_id=record["license_id"],
        explicit_retry=True, expected_version=record.get("_delivery_version"),
        send=lambda: context.bot.send_message(
            chat_id=int(request["recipient_id"]),
            text=(
                "✅ License V-CODE của bạn đã được cấp!\n\n"
                f"{license_summary(record)}\n\nActivation Key:\n{record['activation_key']}\n\n"
                "Copy toàn bộ key. Mở V-CODE → License Support → dán vào Activation Key → Activate."
            ), parse_mode=None,
        ),
    )
    emit_records(context.application.bot_data, request_id=request["request_id"], license_id=record["license_id"], status=status)
    messages = {
        "succeeded": "✅ Telegram đã xác nhận gửi key. Việc này không xác nhận khách đã Activate V-CODE.",
        "unknown": "Kết quả gửi chưa rõ. Thử lại có thể trùng tin. Chọn lại trong /requests và Confirm retry để gửi cùng key.",
        "retryable_failed": "Chưa gửi được key (lỗi tạm thời). Chọn lại trong /requests để xác nhận thử lại cùng key, sau thời gian Telegram yêu cầu chờ.",
        "permanent_failed": "Lỗi gửi cố định. Sửa quyền gửi/chat rồi chọn lại trong /requests để xác nhận thử lại cùng key.",
    }
    await update.effective_message.reply_text(messages[status], reply_markup=build_menu(is_admin=True))

async def approval_decision(update, context):
    query = update.callback_query
    if query is None:
        return
    if not is_private_admin(update, context):
        await query.answer("Chỉ Admin trong chat riêng được xác nhận.", show_alert=True)
        return
    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[1] not in ("confirm", "cancel"):
        await query.answer(STALE_FORM, show_alert=True)
        return
    service = context.application.bot_data["approval_service"]
    try:
        if parts[1] == "cancel":
            service.cancel(parts[2], admin_id=query.from_user.id, chat_id=update.effective_chat.id)
            context.user_data.pop("key_form", None)
            await query.answer("Đã hủy.")
            return
        request, record = service.confirm(
            parts[2], admin_id=query.from_user.id, chat_id=update.effective_chat.id,
        )
        context.user_data.pop("key_form", None)
        await query.answer()
        if record is None:
            await update.effective_message.reply_text("Đã từ chối yêu cầu chưa cấp key. Không có license offline nào bị thu hồi.")
            return
        await deliver_customer(request, record, update, context)
    except StorageError:
        await update.effective_message.reply_text(STORAGE_FAILURE)
    except DeliveryBlocked as error:
        await update.effective_message.reply_text(blocked_message(error))
    except RequestUnavailable as error:
        await query.answer(str(error), show_alert=True)

async def reject_request(update, context):
    query = update.callback_query
    if query is None:
        return
    if not is_private_admin(update, context):
        await query.answer("Chỉ Admin trong chat riêng được từ chối.", show_alert=True)
        return
    selection_id = (query.data or "").split(":", 1)[-1]
    try:
        request = context.application.bot_data["approval_service"].selected_request(
            selection_id, admin_id=query.from_user.id, chat_id=update.effective_chat.id,
        )
        context.user_data["key_form"] = {
            "step": "reject_reason", "selection_id": selection_id, "request_id": request["request_id"],
        }
        await query.answer()
        await update.effective_message.reply_text("Nhập lý do từ chối (1–300 ký tự, không nhập bí mật), hoặc /cancel.")
    except (RequestUnavailable, StorageError) as error:
        await query.answer(STORAGE_FAILURE if isinstance(error, StorageError) else str(error), show_alert=True)

async def show_requests(update, context, page):
    data = context.application.bot_data
    data["delivery_repository"].recover_stale()
    invalidate_form(update, context)
    rows, has_more = data["request_repository"].list_actionable(page=page, admin_id=update.effective_user.id)
    text = f"Yêu cầu cần xử lý — trang {page + 1}\n"
    buttons = []
    for row in rows:
        state = "Chờ duyệt" if not row["license_id"] else "Đã cấp, chờ gửi"
        if row["delivery_status"] in ("unknown", "retryable_failed", "permanent_failed", "attempting"):
            state += f" ({row['delivery_status']})"
        text += f"\n{row['customer'][:60]} • {row['device_id']}\n{state}\n"
        if row["request_id"]:
            buttons.append([InlineKeyboardButton(
                f"Chọn {row['customer'][:25]} • {row['request_id'][:8]}", callback_data=f"request:{row['request_id']}",
            )])
        else:
            text += f"Key tạo thủ công: /retry {row['license_id']}\n"
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton("← Trước", callback_data=f"requests:{page - 1}"))
    if has_more:
        navigation.append(InlineKeyboardButton("Sau →", callback_data=f"requests:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    await update.effective_message.reply_text(text if rows else text + "Không có yêu cầu cần xử lý.",
                                              reply_markup=InlineKeyboardMarkup(buttons))

async def requests_command(update, context):
    if update.effective_message is None:
        return
    if not is_private_admin(update, context):
        await update.effective_message.reply_text("Chỉ Admin trong chat riêng được xem yêu cầu.")
        return
    try:
        page = int(context.args[0]) - 1 if context.args else 0
        if not 0 <= page <= 10000:
            raise ValueError
        await show_requests(update, context, page)
    except ValueError:
        await update.effective_message.reply_text("Cách dùng: /requests [số trang từ 1].")
    except StorageError:
        await update.effective_message.reply_text(STORAGE_FAILURE)

async def requests_page(update, context):
    query = update.callback_query
    if query is None:
        return
    if not is_private_admin(update, context):
        await query.answer("Chỉ Admin trong chat riêng được xem yêu cầu.", show_alert=True)
        return
    try:
        page = int((query.data or "").split(":", 1)[-1])
        if not 0 <= page <= 10000:
            raise ValueError
        await query.answer()
        await show_requests(update, context, page)
    except ValueError:
        await query.answer("Trang không hợp lệ.", show_alert=True)
    except StorageError:
        await update.effective_message.reply_text(STORAGE_FAILURE)
