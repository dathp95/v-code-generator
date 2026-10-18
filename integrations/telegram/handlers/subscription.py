"""Vietnamese checkout/payment UI. SubscriptionService owns all payment transitions."""
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError, TimedOut
from core.crypto.activation import validate_device_id
from services.subscription_service import SubscriptionError
from services.customer_service import CustomerValidationError
from storage.database import StorageError


def service(context):
    return context.application.bot_data['subscription_service']


def identity(update):
    user = update.callback_query.from_user if update.callback_query else update.effective_user
    return dict(user_id=user.id, chat_id=update.effective_chat.id,
                chat_type=update.effective_chat.type)


def admin_identity(update):
    user = update.callback_query.from_user if update.callback_query else update.effective_user
    return dict(admin_id=user.id, chat_id=update.effective_chat.id,
                chat_type=update.effective_chat.type)


def changed(context):
    callback = context.application.bot_data.get('desktop_event')
    if callback:
        callback(status='order_changed')


def summary(order):
    bank = order['payment_details']
    status = {'awaiting_payment': 'Chờ chuyển khoản', 'payment_reported': 'Khách đã báo chuyển khoản, chưa xác nhận nhận tiền',
              'paid': 'Admin đã xác nhận nhận tiền', 'cancelled': 'Đã hủy'}[order['payment_status']]
    money = lambda amount: f'{amount:,}'.replace(',', '.')
    return (f"Đơn: {order['reference']}\nCompany: {order.get('company') or '—'}\nMachine ID: {order['device_id']}\nEdition: {order['edition']}\n"
            f"Tháng: {order['months']} · {order['purchased_days']} ngày\n1 tháng = {bank['days_per_month']} ngày\n"
            f"Giá/tháng: {money(order['unit_price_vnd'])} VND\nTổng: {money(order['total_vnd'])} VND\n"
            f"Ngân hàng: {bank['bank']}\nChủ tài khoản: {bank['account_name']}\nSố tài khoản: {bank['account_number']}\n"
            f"Nội dung chuyển khoản: {bank['transfer_reference']}\nThanh toán: {status}")


def customer_buttons(order):
    oid = order['order_id']
    rows = [[InlineKeyboardButton('📋 Xem đơn', callback_data=f'order:view:{oid}')]]
    if order['payment_status'] in ('awaiting_payment', 'payment_reported'):
        rows.insert(0, [InlineKeyboardButton('✅ Đã chuyển khoản', callback_data=f'order:report:{oid}')])
        rows.append([InlineKeyboardButton('❌ Hủy đơn', callback_data=f'order:cancel:{oid}')])
    return InlineKeyboardMarkup(rows)


async def show_customer(update, order):
    image = BytesIO(order['qr_image'])
    image.name = 'payment_qr.png'
    text = summary(order)
    # Telegram limits captions to 1024 UTF-16 code units; keep the full details intact.
    if len(text.encode('utf-16-le')) // 2 <= 1024:
        await update.effective_message.reply_photo(photo=image, caption=text, reply_markup=customer_buttons(order))
    else:
        await update.effective_message.reply_photo(photo=image, caption=f"Đơn {order['reference']} · Chi tiết ở tin nhắn tiếp theo.")
        await update.effective_message.reply_text(text, reply_markup=customer_buttons(order))


async def begin(update, context):
    from integrations.telegram.handlers.workflow import invalidate_form
    if update.effective_chat.type != 'private':
        await update.callback_query.answer('Chỉ sử dụng trong chat riêng.', show_alert=True)
        return
    await update.callback_query.answer()
    invalidate_form(update, context)
    try:
        service(context).configuration().qr_bytes()
    except SubscriptionError as error:
        await update.effective_message.reply_text(str(error) + '\nXem đơn đã lưu bằng /orders.')
        return
    context.user_data['subscription_form'] = {'step': 'device'}
    await update.effective_message.reply_text('💳 Đăng ký theo tháng\nDán Machine ID từ V-CODE → License Support → Copy ID. Gửi /cancel để hủy nhập.')


async def receive_input(update, context):
    form = context.user_data.get('subscription_form')
    if not form:
        return
    try:
        if form['step'] == 'device':
            device = validate_device_id((update.effective_message.text or '').strip())
            existing = service(context).orders.unpaid(update.effective_user.id, update.effective_chat.id, device)
            if existing:
                context.user_data.pop('subscription_form', None)
                await show_customer(update, existing)
                return
            from integrations.telegram.handlers.customer import offer_company
            await offer_company(update, context, device)
            return
        if form['step'] in ('company', 'company_text'):
            from integrations.telegram.handlers.customer import receive_company
            await receive_company(update, context)
            return
        months = int((update.effective_message.text or '').strip())
        order = service(context).checkout(**identity(update), customer=update.effective_user.full_name,
                                         device_id=form['device_id'], months=months, company=form.get('company'))
        context.user_data.pop('subscription_form', None)
        changed(context)
        await show_customer(update, order)
    except StorageError:
        await update.effective_message.reply_text('Không lưu được đơn. Chưa gửi hướng dẫn thanh toán. Hãy thử lại sau.')
    except ValueError as error:
        await update.effective_message.reply_text(str(error) if isinstance(error, (SubscriptionError, CustomerValidationError)) else 'Machine ID hoặc số tháng không hợp lệ. Nhập lại hoặc /cancel.')


async def notify_admin(context, order):
    orders = service(context)
    claim = orders.claim_notification(order['order_id'])
    if not claim:
        return
    status = 'sent'
    try:
        await context.bot.send_message(chat_id=int(orders.admin_id),
            text=f"Khách: {order['customer']}\nUser ID: {order['user_id']}\nChat: {order['recipient_id']}\n" + summary(order) + '\nChỉ xác nhận sau khi tự kiểm tra tiền trong ngân hàng.',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💰 Xác nhận đã nhận tiền', callback_data=f"order:review:{order['order_id']}")]]))
    except TimedOut:
        status = 'unknown'
    except TelegramError:
        status = 'failed'
    orders.finish_notification(order['order_id'], claim, status)


async def order_action(update, context):
    query = update.callback_query
    try:
        _, action, oid = (query.data or '').split(':')
        orders = service(context)
        if action in ('view', 'report', 'cancel'):
            order = orders.owned(oid, **identity(update))
            if action == 'report':
                order = orders.report_payment(oid, **identity(update))
                changed(context)
                await notify_admin(context, order)
                await update.effective_message.reply_text('Đã ghi nhận bạn báo chuyển khoản. Đây chưa phải xác nhận đã nhận tiền và chưa cấp key.')
            elif action == 'cancel':
                order = orders.cancel(oid, **identity(update))
                changed(context)
            await query.answer()
            await show_customer(update, order)
            return
        if action == 'review':
            order, nonce = orders.review_payment(oid, **admin_identity(update))
            await query.answer()
            await update.effective_message.reply_text(summary(order) +
                f"\nKhách: {order['customer']} · User ID: {order['user_id']} · Chat: {order['recipient_id']}\n"
                'Tự kiểm tra giao dịch ngân hàng. Xác nhận này chỉ đánh dấu đã nhận tiền, KHÔNG cấp key.',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton('Confirm · đã nhận đủ tiền', callback_data=f'order:confirm:{nonce}'),
                    InlineKeyboardButton('Cancel', callback_data=f'order:abort:{nonce}')]]))
            return
        if action == 'abort':
            orders.cancel_review(oid, **admin_identity(update))
            await query.answer('Đã hủy xác nhận. Không thay đổi thanh toán.')
            return
        if action == 'confirm':
            order = orders.confirm_payment(oid, **admin_identity(update))
            changed(context)
            await query.answer()
            await update.effective_message.reply_text(f"Đơn {order['reference']}: đã xác nhận nhận tiền. Chưa cấp key.\n"
                f"Đã mua: {order['purchased_days']} ngày. Chọn yêu cầu, nhập Days, xem lại và Confirm để cấp/gửi key.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Chọn yêu cầu · cấp key', callback_data=f"request:{order['request_id']}")]]))
            return
        raise SubscriptionError('Thao tác đơn không hợp lệ.')
    except SubscriptionError as error:
        await query.answer(str(error), show_alert=True)
    except (ValueError, StorageError):
        await query.answer('Không xử lý được đơn. Kiểm tra /orders trước khi thử lại.', show_alert=True)


async def orders_command(update, context):
    if update.effective_chat.type != 'private':
        await update.effective_message.reply_text('Chỉ xem đơn trong chat riêng.')
        return
    try:
        page = 0
        if update.callback_query:
            page = int(update.callback_query.data.split(':')[-1])
            await update.callback_query.answer()
        if not 0 <= page <= 10000:
            raise ValueError
        orders = service(context)
        admin = str(update.effective_user.id) == orders.admin_id
        orders.recover_notifications()
        filters = {} if admin else dict(user_id=update.effective_user.id, recipient_id=update.effective_chat.id)
        result = orders.orders.list_page(page=page, size=8, **filters)
        rows = [[InlineKeyboardButton(f"{r['reference']} · {r['payment_status']}",
                    callback_data=f"order:{'review' if admin else 'view'}:{r['order_id']}")] for r in result.records]
        navigation = []
        if result.number:
            navigation.append(InlineKeyboardButton('←', callback_data=f'orders:{result.number - 1}'))
        if (result.number + 1) * result.size < result.total:
            navigation.append(InlineKeyboardButton('→', callback_data=f'orders:{result.number + 1}'))
        if navigation:
            rows.append(navigation)
        await update.effective_message.reply_text(f"Đơn đăng ký · trang {result.number + 1}\n" +
            ('Admin tự kiểm tra tiền, xem lại và Confirm. Cấp key là xác nhận riêng sau đó.' if admin else 'Chỉ hiển thị đơn thuộc tài khoản và chat của bạn.'),
            reply_markup=InlineKeyboardMarkup(rows) if rows else None)
    except (StorageError, ValueError):
        await update.effective_message.reply_text('Chưa đọc được danh sách đơn. Thử lại /orders.')
