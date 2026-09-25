from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN

from handlers.registration import (
    FIRST_LAST_NAME,
    PHONE,
    ROLE,
    ADMIN_CONTACT,
    start_registration,
    get_name,
    get_phone,
    get_role,
    start_contact_admin,
    handle_contact_admin_message,
    cancel_contact_admin,
    cancel_registration,
)

from handlers.admin import (
    get_admin_handlers,
    notify_admin_about_timeouts,
    show_my_id,
)

from handlers.orders import (
    show_current_orders,
    start_assembly,
    complete_assembly,
    handle_missing_item,
    show_picker_shift_report,
)
from handlers.shift import toggle_shift


# ==========================================
# СОЗДАЁМ TELEGRAM-БОТА
# ==========================================

application = (
    ApplicationBuilder()
    .token(BOT_TOKEN)
    .build()
)


# ==========================================
# РЕГИСТРАЦИЯ СОТРУДНИКА
# ==========================================

registration_handler = ConversationHandler(
    entry_points=[
        CommandHandler(
            "start",
            start_registration,
        )
    ],

    states={
        FIRST_LAST_NAME: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                get_name,
            )
        ],

        PHONE: [
            MessageHandler(
                filters.CONTACT
                | (filters.TEXT & ~filters.COMMAND),
                get_phone,
            )
        ],

        ROLE: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                get_role,
            )
        ],
    },

    fallbacks=[
        CommandHandler(
            "cancel",
            cancel_registration,
        )
    ],
)

contact_admin_handler = ConversationHandler(
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
        CommandHandler(
            "cancel",
            cancel_contact_admin,
        )
    ],
)


# ==========================================
# ПОДКЛЮЧАЕМ РЕГИСТРАЦИЮ
# ==========================================

application.add_handler(
    registration_handler
)
application.add_handler(
    contact_admin_handler
)


# ==========================================
# АДМИНИСТРАТИВНЫЕ ФУНКЦИИ
# ==========================================

for handler in get_admin_handlers():
    application.add_handler(handler)


# ==========================================
# КОМАНДА /id
# ==========================================

application.add_handler(
    CommandHandler(
        "id",
        show_my_id,
    )
)


# ==========================================
# ТЕКУЩИЕ ЗАКАЗЫ
# ==========================================

application.add_handler(
    MessageHandler(
        filters.TEXT
        & filters.Regex(
            r"^📦 Текущие заказы$"
        ),
        show_current_orders,
    )
)

application.add_handler(
    MessageHandler(
        filters.TEXT
        & filters.Regex(r"^📋 История смены$"),
        show_picker_shift_report,
    )
)

application.add_handler(
    MessageHandler(
        filters.TEXT
        & filters.Regex(r"^(🟢 Завершить смену|🔴 Начать смену)$"),
        toggle_shift,
    )
)


# ==========================================
# НАЧАТЬ СБОРКУ
# ==========================================

application.add_handler(
    CallbackQueryHandler(
        start_assembly,
        pattern=r"^start_assembly:\d+$",
    )
)


# ==========================================
# ЗАКАЗ СОБРАН
# ==========================================

application.add_handler(
    CallbackQueryHandler(
        complete_assembly,
        pattern=r"^complete_assembly:\d+$",
    )
)

# ==========================================
# ТОВАРА НЕТ
# ==========================================

application.add_handler(
    CallbackQueryHandler(
        handle_missing_item,
        pattern=r"^missing_item:\d+$",
    )
)

# ==========================================
# НАБЛЮДЕНИЕ ЗА ТАЙМАУТАМИ ЗАКАЗОВ
# ==========================================

application.job_queue.run_repeating(
    notify_admin_about_timeouts,
    interval=60,
    first=30,
)

# ==========================================
# ЗАПУСКАЕМ БОТА
# ==========================================

print(
    "Бот сборщика запущен. "
    "Ожидаю сообщения..."
)

application.run_polling()