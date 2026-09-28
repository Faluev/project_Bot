import logging

import asyncio

from telegram import BotCommand, BotCommandScopeChat
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN, ADMIN_TELEGRAM_ID
from database import init_database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
init_database()


async def handle_application_error(update, context):
    logger.error("Unhandled exception while processing update", exc_info=context.error)

from handlers.registration import (
    FIRST_LAST_NAME,
    PHONE,
    ADMIN_CONTACT,
    start_registration,
    get_name,
    get_phone,
    start_contact_admin,
    handle_contact_admin_message,
    cancel_contact_admin,
    cancel_registration,
)

from handlers.admin import (
    get_admin_handlers,
    notify_admin_about_timeouts,
    notify_admin_about_missing_items,
    show_my_id,
    show_applications,
    show_employees,
    show_orders_feed,
    show_employee_stats,
    show_order_timeouts,
    show_admin_audit,
    show_rejected_orders,
)

from handlers.orders import (
    show_current_orders,
    start_assembly,
    complete_assembly,
    handle_missing_item,
    show_picker_shift_report,
    notify_pickers_about_new_orders,
    MISSING_ITEM,
    save_missing_item,
    cancel_missing_item,
)
from handlers.shift import toggle_shift
from handlers.language import toggle_language
from handlers.menu import get_admin_menu, get_registration_menu
from handlers.employee import show_work_menu


# ==========================================
# СОЗДАЁМ TELEGRAM-БОТА
# ==========================================

async def setup_bot_commands(application):
    """Настраивает команды Telegram для сотрудников и администратора."""
    employee_commands = [
        BotCommand("start", "Начать работу или регистрацию"),
        BotCommand("cancel", "Отменить текущий диалог"),
    ]
    admin_commands = [
        BotCommand("start", "Начать работу"),
        BotCommand("cancel", "Отменить текущий диалог"),
        BotCommand("applications", "Заявки сотрудников"),
        BotCommand("employees", "Список сотрудников"),
        BotCommand("orders", "Лента заказов"),
        BotCommand("stats", "Статистика сотрудников"),
        BotCommand("timeouts", "Заказы с таймаутом"),
        BotCommand("audit", "Журнал действий"),
        BotCommand("rejected", "Отклонённые заказы"),
        BotCommand("id", "Показать Telegram ID"),
    ]

    await application.bot.set_my_commands(employee_commands)
    await application.bot.set_my_commands(
        admin_commands,
        scope=BotCommandScopeChat(ADMIN_TELEGRAM_ID),
    )




application = (
    ApplicationBuilder()
    .token(BOT_TOKEN)
    .post_init(setup_bot_commands)
    .build()
)
application.add_error_handler(handle_application_error)
application.bot_data["role"] = "picker"



# ==========================================
# РЕГИСТРАЦИЯ СОТРУДНИКА
# ==========================================

registration_handler = ConversationHandler(
    entry_points=[
        CommandHandler("start", start_registration),
        MessageHandler(
            filters.Regex(r"^(🚀 Начать|👷 Регистрация сборщика)$"),
            start_registration,
        ),
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


application.add_handler(
    MessageHandler(
        filters.TEXT & filters.Regex(r"^☰ Меню$"),
        show_work_menu,
    )
)



# ==========================================
# ПОСТОЯННОЕ МЕНЮ АДМИНИСТРАТОРА
# ==========================================

application.add_handler(
    MessageHandler(
        filters.TEXT & filters.Regex(r"^📋 Заявки$"),
        show_applications,
    )
)
application.add_handler(
    MessageHandler(
        filters.TEXT & filters.Regex(r"^👥 Сотрудники$"),
        show_employees,
    )
)
application.add_handler(
    MessageHandler(
        filters.TEXT & filters.Regex(r"^📦 Заказы$"),
        show_orders_feed,
    )
)
application.add_handler(
    MessageHandler(
        filters.TEXT & filters.Regex(r"^📊 Статистика$"),
        show_employee_stats,
    )
)
application.add_handler(
    MessageHandler(
        filters.TEXT & filters.Regex(r"^⏰ Таймауты$"),
        show_order_timeouts,
    )
)
application.add_handler(
    MessageHandler(
        filters.TEXT & filters.Regex(r"^📜 Аудит$"),
        show_admin_audit,
    )
)
application.add_handler(
    MessageHandler(
        filters.TEXT & filters.Regex(r"^⚠️ Отклонённые$"),
        show_rejected_orders,
    )
)
application.add_handler(
    MessageHandler(
        filters.TEXT & filters.Regex(r"^🆔 Мой ID$"),
        show_my_id,
    )
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
            r"^(📦 Текущие заказы|📦 Фармоишҳои ҷорӣ)$"
        ),
        show_current_orders,
    )
)

application.add_handler(
    MessageHandler(
        filters.TEXT
        & filters.Regex(r"^(📋 История смены|📋 Таърихи навбат)$"),
        show_picker_shift_report,
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

missing_item_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(
            handle_missing_item,
            pattern=r"^missing_item:\d+$",
        )
    ],
    states={
        MISSING_ITEM: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, save_missing_item),
        ],
    },
    fallbacks=[CommandHandler("cancel", cancel_missing_item)],
)
application.add_handler(missing_item_handler)

# ==========================================
# НАБЛЮДЕНИЕ ЗА ТАЙМАУТАМИ ЗАКАЗОВ
# ==========================================

application.job_queue.run_repeating(
    notify_admin_about_timeouts,
    interval=60,
    first=30,
)

application.job_queue.run_repeating(
    notify_admin_about_missing_items,
    interval=30,
    first=15,
)

application.job_queue.run_repeating(
    notify_pickers_about_new_orders,
    interval=30,
    first=10,
)

# ==========================================
# ЗАПУСКАЕМ БОТА
# ==========================================

if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    print(
        "Бот сборщика запущен. "
        "Ожидаю сообщения..."
    )
    application.run_polling()