import logging

import asyncio

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

from config import COURIER_BOT_TOKEN
from database import get_employee_by_telegram_id, init_database

logger = logging.getLogger(__name__)
init_database()


async def handle_application_error(update, context):
    logger.error("Unhandled exception while processing update", exc_info=context.error)

from handlers.menu import get_courier_menu
from handlers.registration import (
    ADMIN_CONTACT,
    FIRST_LAST_NAME,
    PHONE,
    COURIER_TRANSPORT,
    start_contact_admin,
    handle_contact_admin_message,
    cancel_contact_admin,
    start_courier_registration,
    get_name,
    get_phone,
    get_courier_transport,
    cancel_registration,
)
from handlers.courier_orders import (
    show_courier_orders,
    handle_pickup_order,
    handle_contact_client,
    handle_deliver_order,
    handle_reject_order,
    handle_reject_reason,
    show_courier_shift_report,
    notify_couriers_about_waiting_orders,
    REJECTION_REASON,
    save_custom_rejection_reason,
    cancel_custom_rejection_reason,
)
from handlers.shift import toggle_shift
from handlers.language import toggle_language
from handlers.i18n import get_message


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_id = update.effective_user.id

    employee = get_employee_by_telegram_id(
        telegram_id
    )

    if employee is None:
        return await start_courier_registration(update, context)

    if (
        employee["role"] == "courier"
        and employee["application_status"] == "rejected"
    ):
        return await start_courier_registration(update, context)

    if employee["application_status"] == "pending":
        await update.message.reply_text(
            "⏳ Ваша заявка ещё находится "
            "на рассмотрении администратора."
        )
        return

    if employee["application_status"] == "rejected":
        await update.message.reply_text(
            "❌ Ваша заявка на регистрацию "
            "отклонена."
        )
        return

    if employee["is_active"] != 1:
        await update.message.reply_text(
                get_message(employee["language"], "disabled_access")
        )
        return

    if employee["role"] != "courier":
        await update.message.reply_text(
            "⛔ Этот бот предназначен только "
            "для курьеров.\n\n"
            "Ваша зарегистрированная роль "
            "не является ролью курьера."
        )
        return

    menu = get_courier_menu(
        employee["is_on_shift"],
        employee["language"],
    )

    await update.message.reply_text(
        get_message(
            employee["language"],
            "courier_login",
            name=employee["first_name"],
        ),
        reply_markup=menu,
    )


application = (
    ApplicationBuilder()
    .token(COURIER_BOT_TOKEN)
    .build()
)
application.add_error_handler(handle_application_error)


courier_registration_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        FIRST_LAST_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_name),
        ],
        PHONE: [
            MessageHandler(
                filters.CONTACT | (filters.TEXT & ~filters.COMMAND),
                get_phone,
            ),
        ],
        COURIER_TRANSPORT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_courier_transport),
        ],
    },
    fallbacks=[CommandHandler("cancel", cancel_registration)],
)

application.add_handler(courier_registration_handler)

admin_contact_handler = ConversationHandler(
    entry_points=[
        MessageHandler(
            filters.TEXT
            & filters.Regex(r"^(👨‍💼 Связаться с администратором|👨‍💼 Тамос бо администратор)$"),
            start_contact_admin,
        )
    ],
    states={
        ADMIN_CONTACT: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                handle_contact_admin_message,
            )
        ]
    },
    fallbacks=[
        CommandHandler("cancel", cancel_contact_admin)
    ],
)

application.add_handler(admin_contact_handler)

application.add_handler(
    MessageHandler(
        filters.TEXT
        & filters.Regex(
            r"^(🚚 Текущий заказ|🚚 Фармоиши ҷорӣ)$"
        ),
        show_courier_orders,
    )
)

application.add_handler(
    MessageHandler(
        filters.TEXT
        & filters.Regex(r"^(📋 Доставки за смену|📋 Доставкаҳои навбат)$"),
        show_courier_shift_report,
    )
)

application.add_handler(
    MessageHandler(
        filters.TEXT
        & filters.Regex(r"^(🟢 Завершить смену|🔴 Начать смену|🟢 Анҷоми навбат|🔴 Оғози навбат)$"),
        toggle_shift,
    )
)

application.add_handler(
    MessageHandler(
        filters.TEXT
        & filters.Regex(r"^(🌐 Язык / Забон|🌐 Забон / Язык)$"),
        toggle_language,
    )
)

application.add_handler(
    CallbackQueryHandler(
        handle_pickup_order,
        pattern=r"^pickup_order:\d+$",
    )
)

application.add_handler(
    CallbackQueryHandler(
        handle_contact_client,
        pattern=r"^contact_client:\d+$",
    )
)

application.add_handler(
    CallbackQueryHandler(
        handle_deliver_order,
        pattern=r"^deliver_order:\d+$",
    )
)

application.add_handler(
    CallbackQueryHandler(
        handle_reject_order,
        pattern=r"^reject_order:\d+$",
    )
)

rejection_reason_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(
            handle_reject_reason,
            pattern=r"^reject_reason:\d+:[a-z_]+$",
        )
    ],
    states={
        REJECTION_REASON: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                save_custom_rejection_reason,
            ),
        ],
    },
    fallbacks=[CommandHandler("cancel", cancel_custom_rejection_reason)],
)
application.add_handler(rejection_reason_handler)

application.job_queue.run_repeating(
    notify_couriers_about_waiting_orders,
    interval=30,
    first=10,
)

if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    print(
        "Бот курьера запущен. "
        "Ожидаю сообщения..."
    )
    application.run_polling()