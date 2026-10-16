"""Shared handler registration for standalone and managed polling."""
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from integrations.telegram.handlers.workflow import *
from integrations.telegram.handlers.navigation import start, menu_click, myid

def register_handlers(app):
    app.add_error_handler(handle_error)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("generate", generate))
    app.add_handler(CommandHandler("retry", retry_manual))
    app.add_handler(CommandHandler("requests", requests_command))
    app.add_handler(CallbackQueryHandler(requests_page, pattern=r"^requests:"))
    app.add_handler(CallbackQueryHandler(reject_request, pattern=r"^reject:"))
    app.add_handler(CallbackQueryHandler(approval_decision, pattern=r"^decision:"))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(
        CallbackQueryHandler(menu_click, pattern=r"^menu:")
    )

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


def build_application(token, admin_id, license_service, database, *, application_factory=Application):
    app = application_factory.builder().token(token).build()
    app.bot_data["admin_id"] = admin_id
    configure_storage(app.bot_data, license_service, database)
    register_handlers(app)
    return app
