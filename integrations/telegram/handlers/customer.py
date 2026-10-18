"""Owner-only optional Company registration step. No payment or issuance rules."""
from uuid import uuid4
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from services.customer_service import CustomerValidationError
from services.subscription_service import SubscriptionError
from storage.database import StorageError


def active_form(update, context, nonce=None):
    user = update.callback_query.from_user if update.callback_query else update.effective_user
    chat = update.effective_chat
    form = context.user_data.get('subscription_form')
    if (chat.type != 'private' or not form or form.get('step') not in ('company', 'company_text')
            or form.get('owner_id') != str(user.id) or form.get('chat_id') != str(chat.id)
            or (nonce is not None and form.get('company_nonce') != nonce)):
        raise CustomerValidationError('Bước Company đã cũ hoặc không thuộc bạn. Hãy đăng ký lại.')
    return form, user


def emit_changed(context):
    callback = context.application.bot_data.get('desktop_event')
    if callback:
        callback(status='customer_changed')


async def offer_company(update, context, device):
    customers = context.application.bot_data['customer_service']
    user, chat = update.effective_user, update.effective_chat
    if chat.type != 'private':
        raise CustomerValidationError('Company chỉ được cập nhật trong chat riêng của khách.')
    profile = customers.ensure('telegram', user.id, user.full_name)
    nonce = uuid4().hex
    context.user_data['subscription_form'].update(step='company', device_id=device, company_nonce=nonce,
                                                 owner_id=str(user.id), chat_id=str(chat.id))
    buttons = []
    if profile['company']:
        buttons.append([InlineKeyboardButton('Use saved company / Dùng đã lưu', callback_data=f'company:use:{nonce}')])
    buttons.append([InlineKeyboardButton('Change / Thay đổi', callback_data=f'company:change:{nonce}'),
                    InlineKeyboardButton('Skip / Bỏ qua', callback_data=f'company:skip:{nonce}')])
    await update.effective_message.reply_text(f"Company (tùy chọn): {profile['company'] or '—'}\nNhập Company (tối đa 150 ký tự), dùng đã lưu hoặc Skip. Skip chỉ bỏ Company khỏi đơn mới.",
                                               reply_markup=InlineKeyboardMarkup(buttons))


async def months_step(update, context, form, company):
    config = context.application.bot_data['subscription_service'].configuration()
    config.qr_bytes()
    form.update(step='months', company=company)
    form.pop('company_nonce', None)
    await update.effective_message.reply_text(f'Nhập số tháng nguyên từ 1 đến {config.max_months}. 1 tháng = {config.days_per_month} ngày.')


async def receive_company(update, context):
    form, user = active_form(update, context)
    profile = context.application.bot_data['customer_service'].update_owned_company(
        channel='telegram', user_id=form['owner_id'], actor_user_id=user.id,
        chat_type=update.effective_chat.type, company=update.effective_message.text or '')
    emit_changed(context)
    await months_step(update, context, form, profile['company'])


async def company_action(update, context):
    query = update.callback_query
    try:
        _, action, nonce = (query.data or '').split(':')
        form, user = active_form(update, context, nonce)
        if action not in ('use', 'change', 'skip'):
            raise CustomerValidationError('Thao tác Company không hợp lệ.')
        await query.answer()
        if action == 'change':
            form['step'] = 'company_text'
            await update.effective_message.reply_text('Nhập Company mới (tối đa 150 ký tự), hoặc Skip trên nút cũ để bỏ qua.')
        else:
            profile = context.application.bot_data['customer_service'].customers.get('telegram', user.id)
            await months_step(update, context, form, profile['company'] if action == 'use' and profile else '')
    except (CustomerValidationError, SubscriptionError) as error:
        await update.effective_message.reply_text(str(error))
    except (StorageError, ValueError):
        await update.effective_message.reply_text('Chưa cập nhật được Company. Hãy thử lại hoặc /cancel.')
