from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest

from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import ADMIN_TELEGRAM_ID, COURIER_BOT_TOKEN

from database import (
    get_pending_applications,
    update_application_status,
    get_admin_orders_feed,
    get_order_history,
    get_order_items,
    get_order_timeouts,
    get_employee_stats,
    get_approved_employees,
    toggle_employee_access,
    set_employee_access,
    get_rejected_orders,
    resolve_rejected_order,
    log_order_timeout_alert,
    get_pending_missing_item_alerts,
    log_missing_item_alert_sent,
)
from config import ADMIN_TELEGRAM_ID, ORDER_TIMEOUT_MINUTES
from handlers.retry import send_message_with_retry
from handlers.i18n import get_message


async def answer_callback_safely(query, text=None, show_alert=False):
    try:
        await query.answer(text=text, show_alert=show_alert)
        return True
    except BadRequest:
        return False


async def edit_callback_message_safely(query, text, reply_markup=None):
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
        return True
    except BadRequest:
        return False


async def notify_employee_application_result(context, employee, text):
    if employee["role"] != "courier":
        await send_message_with_retry(
            context.bot,
            chat_id=employee["telegram_id"],
            text=text,
        )
        return

    courier_bot = Bot(token=COURIER_BOT_TOKEN)
    await courier_bot.initialize()
    try:
        await send_message_with_retry(
            courier_bot,
            chat_id=employee["telegram_id"],
            text=text,
        )
    finally:
        await courier_bot.shutdown()


def is_admin(update: Update) -> bool:
    """
    Проверяет, является ли пользователь администратором.
    """

    return (
        update.effective_user is not None
        and update.effective_user.id == ADMIN_TELEGRAM_ID
    )


async def show_my_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Показывает Telegram ID текущего пользователя.
    """

    telegram_id = update.effective_user.id

    await update.message.reply_text(
        f"Ваш Telegram ID: {telegram_id}"
    )


async def show_applications(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Показывает администратору заявки,
    ожидающие рассмотрения.
    """

    # Проверяем права администратора
    if not is_admin(update):
        await update.message.reply_text(
            "⛔ У вас нет доступа к этому разделу."
        )
        return

    applications = get_pending_applications()

    if not applications:
        await update.message.reply_text(
            "📋 Новых заявок нет."
        )
        return

    await update.message.reply_text(
        f"📋 Заявок на рассмотрении: {len(applications)}"
    )

    for employee in applications:

        role_name = {
            "picker": "👷 Сборщик",
            "courier": "🚚 Курьер",
        }.get(
            employee["role"],
            employee["role"],
        )

        if employee["telegram_username"]:
            telegram_name = (
                f"@{employee['telegram_username']}"
            )
        else:
            telegram_name = "не указан"

        text = (
            "👤 Заявка сотрудника\n\n"
            f"Имя: {employee['first_name']} "
            f"{employee['last_name']}\n"
            f"📱 Телефон: {employee['phone']}\n"
            f"💬 Telegram: {telegram_name}\n"
            f"🎯 Роль: {role_name}\n"
            f"🚲 Транспорт: {employee['transport_type'] or '—'}\n"
            f"📅 Заявка: {employee['created_at']}"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Одобрить",
                    callback_data=f"approve:{employee['id']}",
                ),
                InlineKeyboardButton(
                    "❌ Отклонить",
                    callback_data=f"reject:{employee['id']}",
                ),
            ]
        ])

        await update.message.reply_text(
            text,
            reply_markup=keyboard,
        )


async def process_application(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Обрабатывает нажатие кнопки
    Одобрить / Отклонить.
    """

    query = update.callback_query

    await answer_callback_safely(query)

    # Проверяем администратора
    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await answer_callback_safely(
            query,
            "У вас нет доступа.",
            show_alert=True,
        )
        return

    data = query.data

    action, employee_id_text = data.split(":", 1)

    employee_id = int(employee_id_text)

    # ==========================================
    # ОДОБРЕНИЕ
    # ==========================================

    if action == "approve":

        employee = update_application_status(
            employee_id,
            "approved",
        )

        if employee is None:
            await query.edit_message_text(
                "ℹ️ Эта заявка уже была обработана."
            )
            return

        await query.edit_message_text(
            "✅ Заявка одобрена.\n\n"
            f"Сотрудник: "
            f"{employee['first_name']} "
            f"{employee['last_name']}"
        )

        # Сообщаем сотруднику
        await notify_employee_application_result(
            context,
            employee,
            get_message(employee["language"], "application_approved_notice"),
        )

        return

    # ==========================================
    # ОТКЛОНЕНИЕ
    # ==========================================

    if action == "reject":

        employee = update_application_status(
            employee_id,
            "rejected",
        )

        if employee is None:
            await query.edit_message_text(
                "ℹ️ Эта заявка уже была обработана."
            )
            return

        await query.edit_message_text(
            "❌ Заявка отклонена.\n\n"
            f"Сотрудник: "
            f"{employee['first_name']} "
            f"{employee['last_name']}"
        )

        # Сообщаем сотруднику
        await notify_employee_application_result(
            context,
            employee,
            get_message(employee["language"], "application_rejected_notice"),
        )

        return


def get_status_label(status: str) -> str:
    labels = {
        "new": "Новый",
        "accepted": "Принят",
        "assembling": "Собирается",
        "awaiting_courier": "Ожидает курьера",
        "in_delivery": "В пути",
        "delivered": "Доставлен",
        "rejected": "Отклонён",
        "missing_item": "Товар отсутствует",
    }
    return labels.get(status, status)


async def show_orders_feed(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Показывает администратору ленту заказов с текущим статусом.
    """
    if not is_admin(update):
        await update.message.reply_text("⛔ У вас нет доступа к этому разделу.")
        return

    orders = get_admin_orders_feed()

    if not orders:
        await update.message.reply_text("📋 Заказов пока нет.")
        return

    for order in orders:
        status = get_status_label(order["status"])
        picker = order["picker_name"] or "—"
        courier = order["courier_name"] or "—"
        total_time = (
            f"Общее время: {order['total_delivery_minutes']} мин.\n"
            if order["total_delivery_minutes"] is not None
            else ""
        )

        text = (
            f"📦 Заказ № {order['order_number']}\n"
            f"Статус: {status}\n"
            f"Клиент: {order['client_name']}\n"
            f"Адрес: {order['delivery_address']}\n"
            f"Сборщик: {picker}\n"
            f"Курьер: {courier}\n"
            f"Сумма: {order['payment_amount']}\n"
            f"{total_time}"
            f"Обновлён: {order['updated_at']}\n"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📜 История заказа",
                    callback_data=f"order_history:{order['id']}",
                )
            ]
        ])

        await update.message.reply_text(
            text,
            reply_markup=keyboard,
        )


async def show_order_history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Показывает историю статусов конкретного заказа.
    """
    query = update.callback_query
    await answer_callback_safely(query)

    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await answer_callback_safely(
            query,
            "У вас нет доступа.",
            show_alert=True,
        )
        return

    try:
        order_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await answer_callback_safely(
            query,
            "Некорректный ID заказа.",
            show_alert=True,
        )
        return

    order_history = get_order_history(order_id)

    if not order_history:
        await query.edit_message_text("ℹ️ История заказа пуста.")
        return

    order = get_admin_orders_feed()
    current_order = next((item for item in order if item["id"] == order_id), None)
    if current_order is None:
        await query.edit_message_text("❌ Заказ не найден.")
        return

    items = get_order_items(order_id)
    item_text = "\n".join(
        f"• {product['product_name']} — {product['quantity']} шт."
        for product in items
    ) or "—"

    message = (
        f"📦 Заказ № {current_order['order_number']}\n"
        f"Клиент: {current_order['client_name']}\n"
        f"Адрес: {current_order['delivery_address']}\n\n"
        f"Товары:\n{item_text}\n\n"
        "История:\n"
    )

    for event in order_history:
        employee = event["employee_name"] or "система"
        message += (
            f"• {event['created_at']} | {employee}\n"
            f"  {event['details']}\n"
        )

    await query.edit_message_text(message)


async def show_order_timeouts(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Показывает заказы, которые слишком долго не взяли в работу.
    """
    if not is_admin(update):
        await update.message.reply_text("⛔ У вас нет доступа к этому разделу.")
        return

    orders = get_order_timeouts(ORDER_TIMEOUT_MINUTES)

    if not orders:
        await update.message.reply_text("⏳ Превышений таймаутов сейчас нет.")
        return

    for order in orders:
        status_label = get_status_label(order["status"])
        text = (
            f"⏰ Таймаут заказа\n\n"
            f"№ {order['order_number']}\n"
            f"Статус: {status_label}\n"
            f"Клиент: {order['client_name']}\n"
            f"Адрес: {order['delivery_address']}\n"
            f"Не взят в работу более {ORDER_TIMEOUT_MINUTES} минут."
        )
        await update.message.reply_text(text)


async def show_employee_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Показывает статистику по сотрудникам.
    """
    if not is_admin(update):
        await update.message.reply_text("⛔ У вас нет доступа к этому разделу.")
        return

    stats = get_employee_stats()

    if not stats:
        await update.message.reply_text("📊 Статистика сотрудников пока пуста.")
        return

    lines = ["📊 Статистика сотрудников\n"]

    for entry in stats:
        role_label = "👷 Сборщик" if entry["role"] == "picker" else "🚚 Курьер"
        if entry["role"] == "picker":
            line = (
                f"{role_label} — {entry['name']}\n"
                f"Сборок: {entry['assembly_count']}"
            )
        else:
            avg_text = (
                f"{entry['avg_delivery_minutes']} мин."
                if entry["avg_delivery_minutes"] is not None
                else "—"
            )
            line = (
                f"{role_label} — {entry['name']}\n"
                f"Доставок: {entry['delivery_count']}\n"
                f"Среднее время: {avg_text}"
            )

        lines.append(line)

    await update.message.reply_text("\n\n".join(lines))


async def show_employees(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update):
        await update.message.reply_text("⛔ У вас нет доступа к этому разделу.")
        return

    employees = get_approved_employees()
    if not employees:
        await update.message.reply_text("👥 Одобренных сотрудников пока нет.")
        return

    for employee in employees:
        role_label = "Сборщик" if employee["role"] == "picker" else "Курьер"
        access_label = "включён" if employee["is_active"] else "отключён"
        action_label = "Отключить" if employee["is_active"] else "Включить"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"{'⛔' if employee['is_active'] else '✅'} {action_label}",
                callback_data=(
                    f"employee_access:{employee['id']}:"
                    f"{0 if employee['is_active'] else 1}"
                ),
            )]
        ])
        await update.message.reply_text(
            f"👤 {employee['first_name']} {employee['last_name']}\n"
            f"Роль: {role_label}\n"
            f"Доступ: {access_label}",
            reply_markup=keyboard,
        )


async def process_employee_access(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await answer_callback_safely(query)

    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await answer_callback_safely(
            query,
            "У вас нет доступа.",
            show_alert=True,
        )
        return

    try:
        _, employee_id_text, desired_access_text = query.data.split(":", 2)
        employee_id = int(employee_id_text)
        desired_access = int(desired_access_text)
    except (IndexError, ValueError):
        await edit_callback_message_safely(
            query,
            "ℹ️ Кнопка устарела. Откройте список заново командой /employees.",
        )
        return

    employee = set_employee_access(employee_id, desired_access)
    if employee is None:
        await edit_callback_message_safely(
            query,
            "ℹ️ Сотрудник не найден или больше не одобрен.",
        )
        return

    access_label = "включён" if employee["is_active"] else "отключён"
    await edit_callback_message_safely(
        query,
        f"✅ Доступ сотрудника {employee['first_name']} "
        f"{employee['last_name']} {access_label}."
    )


async def show_rejected_orders(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update):
        await update.message.reply_text("⛔ У вас нет доступа к этому разделу.")
        return

    orders = get_rejected_orders()
    if not orders:
        await update.message.reply_text("✅ Необработанных отклонённых заказов нет.")
        return

    for order in orders:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "↩️ Вернуть на склад",
                    callback_data=f"rejected_resolution:{order['id']}:return_to_stock",
                ),
                InlineKeyboardButton(
                    "🗑 Списать",
                    callback_data=f"rejected_resolution:{order['id']}:write_off",
                ),
            ]
        ])
        await update.message.reply_text(
            f"⚠️ Заказ № {order['order_number']} отклонён\n"
            f"Клиент: {order['client_name']}\n"
            f"Причина: {order['rejection_details'] or 'не указана'}",
            reply_markup=keyboard,
        )


async def process_rejected_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await answer_callback_safely(
            query,
            "У вас нет доступа.",
            show_alert=True,
        )
        return

    await answer_callback_safely(query)
    try:
        _, order_id_text, resolution = query.data.split(":", 2)
        order_id = int(order_id_text)
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Некорректное решение по заказу.")
        return

    result = resolve_rejected_order(
        order_id,
        resolution,
        admin_telegram_id=query.from_user.id,
    )
    if not result["success"]:
        await query.edit_message_text("ℹ️ По этому заказу решение уже принято.")
        return

    resolution_text = (
        "товар возвращён на склад"
        if resolution == "return_to_stock"
        else "товар списан"
    )
    await query.edit_message_text(
        f"✅ По заказу № {result['order_number']} принято решение: {resolution_text}."
    )


async def notify_admin_about_timeouts(context: ContextTypes.DEFAULT_TYPE):
    """
    Автоматически шлёт уведомления админу,
    если заказ слишком долго не берут в работу.
    """
    orders = get_order_timeouts(ORDER_TIMEOUT_MINUTES)

    if not orders:
        return

    for order in orders:
        status_label = get_status_label(order["status"])
        text = (
            "⏰ Уведомление о таймауте\n\n"
            f"Заказ № {order['order_number']}\n"
            f"Статус: {status_label}\n"
            f"Клиент: {order['client_name']}\n"
            f"Адрес: {order['delivery_address']}\n"
            f"Заказ не был взят в работу более {ORDER_TIMEOUT_MINUTES} минут."
        )

        await send_message_with_retry(
            context.bot,
            chat_id=ADMIN_TELEGRAM_ID,
            text=text,
        )

        log_order_timeout_alert(
            order["id"],
            order["status"],
            ORDER_TIMEOUT_MINUTES,
        )


async def notify_admin_about_missing_items(
    context: ContextTypes.DEFAULT_TYPE,
):
    for event in get_pending_missing_item_alerts():
        text = (
            f"⚠️ Сборщик отметил отсутствие товара в заказе № {event['order_number']}\n"
            f"Позиция: {event['details']}\n"
            f"Сборщик: {event['picker_name'] or 'неизвестен'}\n"
            f"Время: {event['created_at']}\n"
            "Заказ остался на сборке; можно продолжить сборку или уточнить замену."
        )
        await send_message_with_retry(
            context.bot,
            chat_id=ADMIN_TELEGRAM_ID,
            text=text,
        )
        log_missing_item_alert_sent(event["event_id"], event["order_id"])


def get_admin_handlers():
    """
    Возвращает обработчики административных команд.
    """

    return [
        CommandHandler("id", show_my_id),
        CommandHandler(
            "applications",
            show_applications,
        ),
        CommandHandler(
            "orders",
            show_orders_feed,
        ),
        CommandHandler(
            "timeouts",
            show_order_timeouts,
        ),
        CommandHandler(
            "stats",
            show_employee_stats,
        ),
        CommandHandler("employees", show_employees),
        CommandHandler("rejected", show_rejected_orders),
        CallbackQueryHandler(
            process_application,
            pattern=r"^(approve|reject):\d+$",
        ),
        CallbackQueryHandler(
            show_order_history,
            pattern=r"^order_history:\d+$",
        ),
        CallbackQueryHandler(
            process_employee_access,
            pattern=r"^employee_access:\d+:[01]$",
        ),
        CallbackQueryHandler(
            process_rejected_order,
            pattern=r"^rejected_resolution:\d+:(return_to_stock|write_off)$",
        ),
    ]