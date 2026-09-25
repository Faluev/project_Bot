from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)

from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import ADMIN_TELEGRAM_ID

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
    log_order_timeout_alert,
)
from config import ADMIN_TELEGRAM_ID, ORDER_TIMEOUT_MINUTES


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
        f"1489820047: {telegram_id}"
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

    # Обязательно отвечаем на callback.
    await query.answer()

    # Проверяем администратора
    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await query.answer(
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
        await context.bot.send_message(
            chat_id=employee["telegram_id"],
            text=(
                "🎉 Ваша заявка одобрена!\n\n"
                "Теперь вы можете пользоваться "
                "рабочими функциями бота."
            ),
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
        await context.bot.send_message(
            chat_id=employee["telegram_id"],
            text=(
                "❌ Ваша заявка на регистрацию "
                "отклонена.\n\n"
                "Если вы считаете, что это ошибка, "
                "свяжитесь с администратором."
            ),
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

        text = (
            f"📦 Заказ № {order['order_number']}\n"
            f"Статус: {status}\n"
            f"Клиент: {order['client_name']}\n"
            f"Адрес: {order['delivery_address']}\n"
            f"Сборщик: {picker}\n"
            f"Курьер: {courier}\n"
            f"Сумма: {order['payment_amount']}\n"
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
    await query.answer()

    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await query.answer("У вас нет доступа.", show_alert=True)
        return

    try:
        order_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer("Некорректный ID заказа.", show_alert=True)
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
                callback_data=f"employee_access:{employee['id']}",
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
    await query.answer()

    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await query.answer("У вас нет доступа.", show_alert=True)
        return

    try:
        employee_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer("Некорректный ID сотрудника.", show_alert=True)
        return

    employee = toggle_employee_access(employee_id)
    if employee is None:
        await query.edit_message_text("ℹ️ Сотрудник не найден или уже не активен.")
        return

    access_label = "включён" if employee["is_active"] else "отключён"
    await query.edit_message_text(
        f"✅ Доступ сотрудника {employee['first_name']} "
        f"{employee['last_name']} {access_label}."
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

        await context.bot.send_message(
            chat_id=ADMIN_TELEGRAM_ID,
            text=text,
        )

        log_order_timeout_alert(
            order["id"],
            order["status"],
            ORDER_TIMEOUT_MINUTES,
        )


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
            pattern=r"^employee_access:\d+$",
        ),
    ]