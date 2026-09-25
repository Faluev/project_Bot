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
from database import get_employee_by_telegram_id

from handlers.menu import get_courier_menu
from handlers.registration import (
    ADMIN_CONTACT,
    start_contact_admin,
    handle_contact_admin_message,
    cancel_contact_admin,
)
from handlers.courier_orders import (
    show_courier_orders,
    handle_pickup_order,
    handle_contact_client,
    handle_deliver_order,
    handle_reject_order,
    handle_reject_reason,
    show_courier_shift_report,
)
from handlers.shift import toggle_shift


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_id = update.effective_user.id

    employee = get_employee_by_telegram_id(
        telegram_id
    )

    if employee is None:
        await update.message.reply_text(
            "👋 Добро пожаловать!\n\n"
            "Вы ещё не зарегистрированы "
            "как сотрудник.\n\n"
            "Пройдите регистрацию через "
            "бота сборщика."
        )
        return

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
            "⛔ Ваш доступ к рабочей системе "
            "отключён."
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

    menu = get_courier_menu(employee["is_on_shift"])

    await update.message.reply_text(
        f"Здравствуйте, "
        f"{employee['first_name']}! 👋\n\n"
        "🚚 Вы вошли как курьер.\n\n"
        "Выберите нужное действие:",
        reply_markup=menu,
    )


application = (
    ApplicationBuilder()
    .token(COURIER_BOT_TOKEN)
    .build()
)


application.add_handler(
    CommandHandler(
        "start",
        start,
    )
)

admin_contact_handler = ConversationHandler(
    entry_points=[
        MessageHandler(
            filters.TEXT
            & filters.Regex(r"^👨‍💼 Связаться с администратором$"),
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
            r"^🚚 Текущий заказ$"
        ),
        show_courier_orders,
    )
)

application.add_handler(
    MessageHandler(
        filters.TEXT
        & filters.Regex(r"^📋 Доставки за смену$"),
        show_courier_shift_report,
    )
)

application.add_handler(
    MessageHandler(
        filters.TEXT
        & filters.Regex(r"^(🟢 Завершить смену|🔴 Начать смену)$"),
        toggle_shift,
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

application.add_handler(
    CallbackQueryHandler(
        handle_reject_reason,
        pattern=r"^reject_reason:\d+:[a-z_]+$",
    )
)

print(
    "Бот курьера запущен. "
    "Ожидаю сообщения..."
)

application.run_polling()