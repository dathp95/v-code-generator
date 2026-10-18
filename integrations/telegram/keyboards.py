from telegram import InlineKeyboardButton, InlineKeyboardMarkup

GUIDE_CHANNEL_URL = "https://t.me/+xBQZPGeEHpZiM2Q9"


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
                "📺 Video hướng dẫn sử dụng",
                url=GUIDE_CHANNEL_URL,
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