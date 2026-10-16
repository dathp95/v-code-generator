from telegram import InlineKeyboardButton, InlineKeyboardMarkup

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
