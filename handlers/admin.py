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
    get_admin_audit_log,
    get_order_items,
    get_order_timeouts,
    process_order_timeouts,
    get_employee_stats,
    get_admin_stats_summary,
    get_employee_by_id,
    get_approved_employees,
    toggle_employee_access,
    set_employee_access,
    get_rejected_orders,
    resolve_rejected_order,
    retry_rejected_order,
    get_retryable_rejected_orders,
    log_order_timeout_alert,
    get_pending_missing_item_alerts,
    log_missing_item_alert_sent,
)
from config import ADMIN_TELEGRAM_ID, ORDER_TIMEOUT_MINUTES
from handlers.retry import send_message_with_retry
from handlers.i18n import get_message
from handlers.menu import get_work_menu_expanded


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
    """
    Отправляет сотруднику результат заявки через правильного бота
    и сразу показывает рабочее меню после одобрения.
    """
    bot = context.bot
    own_bot = True

    if employee["role"] == "courier":
        bot = Bot(token=COURIER_BOT_TOKEN)
        await bot.initialize()
        own_bot = False

    try:
        reply_markup = None
        if employee["application_status"] == "approved":
            reply_markup = get_work_menu(
                employee["role"],
                employee["is_on_shift"],
                employee["language"],
            )

        await send_message_with_retry(
            bot,
            chat_id=employee["telegram_id"],
            text=text,
            reply_markup=reply_markup,
        )
    finally:
        if not own_bot:
            await bot.shutdown()


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

        role_label = {
            "picker": "👷 Сборщик",
            "courier": "🚚 Курьер",
        }.get(employee["role"], employee["role"])

        await query.edit_message_text(
            "✅ Заявка одобрена.\n\n"
            f"Сотрудник: {employee['first_name']} {employee['last_name']}\n"
            f"🎯 Роль: {role_label}"
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

        role_label = {
            "picker": "👷 Сборщик",
            "courier": "🚚 Курьер",
        }.get(employee["role"], employee["role"])

        await query.edit_message_text(
            "❌ Заявка отклонена.\n\n"
            f"Сотрудник: {employee['first_name']} {employee['last_name']}\n"
            f"🎯 Роль: {role_label}"
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
            f"Способ оплаты: {order['payment_method'] or '—'}\n"
            f"Сумма: {order['payment_amount'] if order['payment_amount'] is not None else '—'}\n"
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



async def show_admin_audit(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Показывает администратору последние действия системы."""
    if not is_admin(update):
        await update.message.reply_text("⛔ У вас нет доступа к этому разделу.")
        return

    events = get_admin_audit_log(20)
    if not events:
        await update.message.reply_text("📜 Журнал действий пока пуст.")
        return

    lines = ["📜 Журнал действий", ""]
    for event in events:
        employee = event["employee_name"]
        if employee:
            role = "сборщик" if event["employee_role"] == "picker" else "курьер"
            actor = f"{employee} ({role})"
        else:
            actor = "система"

        order_label = (
            f"заказ № {event['order_number']}"
            if event["order_number"]
            else "без заказа"
        )
        transition = ""
        if event["old_status"] or event["new_status"]:
            transition = f" [{event['old_status'] or '—'} → {event['new_status'] or '—'}]"

        lines.append(
            f"• {event['created_at']} — {actor}"
        )
        lines.append(
            f"  {order_label} — {event['action']}{transition}"
        )
        if event["details"]:
            lines.append(f"  {event['details']}")
        lines.append("")

    await update.message.reply_text("\n".join(lines))


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
    if not is_admin(update):
        await update.message.reply_text("⛔ У вас нет доступа к этому разделу.")
        return

    summary = get_admin_stats_summary()
    stats = get_employee_stats()

    avg_text = (
        f"{summary['avg_delivery_minutes']} мин."
        if summary["avg_delivery_minutes"] is not None
        else "—"
    )

    lines = [
        "📊 Общая статистика",
        "",
        f"📦 Всего заказов: {summary['total_orders']}",
        f"✅ Доставлено: {summary['delivered_orders']}",
        f"❌ Отклонено: {summary['rejected_orders']}",
        f"⏱ Среднее время доставки: {avg_text}",
        "",
        f"👥 Активных сотрудников: {summary['active_employees']}",
        f"🟢 На смене: {summary['on_shift_employees']}",
    ]

    if stats:
        lines.extend(["", "👤 По сотрудникам:"])
        for entry in stats:
            role_label = "👷 Сборщик" if entry["role"] == "picker" else "🚚 Курьер"
            if entry["role"] == "picker":
                lines.append(
                    f"{role_label} — {entry['name']}: "
                    f"{entry['assembly_count']} сборок"
                )
            else:
                employee_avg = (
                    f"{entry['avg_delivery_minutes']} мин."
                    if entry["avg_delivery_minutes"] is not None
                    else "—"
                )
                lines.append(
                    f"{role_label} — {entry['name']}: "
                    f"{entry['delivery_count']} доставок, "
                    f"среднее {employee_avg}"
                )

    await update.message.reply_text("\n".join(lines))


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

    await update.message.reply_text(f"👥 Сотрудники\n\nВсего ролей: {len(employees)}")

    for employee in employees:
        role_label = "👷 Сборщик" if employee["role"] == "picker" else "🚚 Курьер"
        access_label = "✅ включён" if employee["is_active"] else "⛔ отключён"
        shift_label = "🟢 на смене" if employee["is_on_shift"] else "🔴 не на смене"
        action_label = "⛔ Отключить доступ" if employee["is_active"] else "✅ Включить доступ"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                action_label,
                callback_data=f"employee_access:{employee['id']}:{0 if employee['is_active'] else 1}",
            )],
            [InlineKeyboardButton(
                "📊 Статистика",
                callback_data=f"employee_stats:{employee['id']}",
            )],
        ])
        await update.message.reply_text(
            f"👤 {employee['first_name']} {employee['last_name']}\n"
            f"Роль: {role_label}\n"
            f"Доступ: {access_label}\n"
            f"Смена: {shift_label}",
            reply_markup=keyboard,
        )


async def process_employee_access(
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
        _, employee_id_text, is_active_text = query.data.split(":", 2)
        employee_id = int(employee_id_text)
        is_active = int(is_active_text)
    except (ValueError, IndexError):
        await edit_callback_message_safely(
            query,
            "❌ Некорректные параметры доступа сотрудника.",
        )
        return

    employee = set_employee_access(employee_id, is_active)
    if employee is None:
        await edit_callback_message_safely(
            query,
            "ℹ️ Сотрудник не найден или больше не одобрен.",
        )
        return

    access_label = "✅ включён" if employee["is_active"] else "⛔ отключён"
    shift_label = "🟢 на смене" if employee["is_on_shift"] else "🔴 не на смене"
    action_label = "⛔ Отключить доступ" if employee["is_active"] else "✅ Включить доступ"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            action_label,
            callback_data=f"employee_access:{employee['id']}:{0 if employee['is_active'] else 1}",
        )],
        [InlineKeyboardButton(
            "📊 Статистика",
            callback_data=f"employee_stats:{employee['id']}",
        )],
    ])

    await edit_callback_message_safely(
        query,
        f"👤 {employee['first_name']} {employee['last_name']}\n"
        f"Роль: {'👷 Сборщик' if employee['role'] == 'picker' else '🚚 Курьер'}\n"
        f"Доступ: {access_label}\n"
        f"Смена: {shift_label}",
        reply_markup=keyboard,
    )


async def show_employee_details_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await answer_callback_safely(query)

    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await answer_callback_safely(query, "У вас нет доступа.", show_alert=True)
        return

    try:
        employee_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await answer_callback_safely(query, "Некорректный ID сотрудника.", show_alert=True)
        return

    employee = get_employee_by_id(employee_id)
    if employee is None or employee["application_status"] != "approved":
        await edit_callback_message_safely(query, "ℹ️ Сотрудник не найден или больше не одобрен.")
        return

    stats = get_employee_stats(employee_id)
    entry = stats[0] if stats else None
    role_label = "👷 Сборщик" if employee["role"] == "picker" else "🚚 Курьер"
    access_label = "✅ включён" if employee["is_active"] else "⛔ отключён"
    shift_label = "🟢 на смене" if employee["is_on_shift"] else "🔴 не на смене"

    if entry is None:
        stats_text = "Статистика пока пуста."
    elif employee["role"] == "picker":
        stats_text = f"Сборок: {entry['assembly_count']}"
    else:
        avg_text = f"{entry['avg_delivery_minutes']} мин." if entry["avg_delivery_minutes"] is not None else "—"
        stats_text = f"Доставок: {entry['delivery_count']}\nСреднее время доставки: {avg_text}"

    action_label = "⛔ Отключить доступ" if employee["is_active"] else "✅ Включить доступ"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            action_label,
            callback_data=f"employee_access:{employee['id']}:{0 if employee['is_active'] else 1}",
        )],
        [InlineKeyboardButton(
            "📊 Обновить статистику",
            callback_data=f"employee_stats:{employee['id']}",
        )],
    ])

    await edit_callback_message_safely(
        query,
        f"👤 {employee['first_name']} {employee['last_name']}\n"
        f"Роль: {role_label}\n"
        f"Доступ: {access_label}\n"
        f"Смена: {shift_label}\n\n"
        f"📊 {stats_text}",
        reply_markup=keyboard,
    )


async def show_rejected_orders(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update):
        await update.message.reply_text("⛔ У вас нет доступа к этому разделу.")
        return

    orders = get_rejected_orders()
    retryable = get_retryable_rejected_orders()
    if not orders and not retryable:
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

    for order in retryable:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🚚 Разрешить повторную доставку",
                callback_data=f"retry_delivery:{order['id']}",
            )
        ]])
        await update.message.reply_text(
            f"🔁 Заказ № {order['order_number']} готов к повторной доставке\n"
            f"Клиент: {order['client_name']}",
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


async def process_retry_delivery(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    if query.from_user.id != ADMIN_TELEGRAM_ID:
        await answer_callback_safely(query, "У вас нет доступа.", show_alert=True)
        return

    await answer_callback_safely(query)
    try:
        order_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await edit_callback_message_safely(query, "❌ Некорректный ID заказа.")
        return

    result = retry_rejected_order(
        order_id,
        admin_telegram_id=query.from_user.id,
    )
    if not result["success"]:
        messages = {
            "not_found": "Заказ не найден.",
            "wrong_status": "Заказ уже не находится в статусе «Отклонён».",
            "resolution_required": "Сначала нужно принять решение по товару.",
            "written_off": "Для списанного заказа повторная доставка недоступна.",
            "already_retried": "Повторная доставка уже была запущена.",
        }
        await edit_callback_message_safely(
            query,
            f"ℹ️ {messages.get(result['reason'], 'Не удалось запустить повторную доставку.')}",
        )
        return

    from handlers.courier_orders import notify_couriers_about_waiting_orders
    await notify_couriers_about_waiting_orders(context)
    await edit_callback_message_safely(
        query,
        f"🚚 Заказ № {result['order_number']} возвращён в очередь курьеров для повторной доставки.",
    )


async def notify_admin_about_timeouts(context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает таймауты и сообщает администратору.

    Таймаут 'assembling' принудительно освобождает заказ и возвращает
    его в FIFO-очередь. Таймауты 'new' и 'awaiting_courier' не меняют
    статус, а только эскалируются администратору.
    """
    requeued = process_order_timeouts(ORDER_TIMEOUT_MINUTES)

    for order in requeued:
        if order["action"] == "timeout_requeue":
            text = (
                "⏰ Заказ автоматически возвращён в очередь\n\n"
                f"Заказ № {order['order_number']}\n"
                "Статус: Сборка → Новый\n"
                f"Сборка превысила таймаут {ORDER_TIMEOUT_MINUTES} минут.\n"
                "Заказ снова доступен сборщикам по строгому FIFO."
            )
            await send_message_with_retry(
                context.bot,
                chat_id=ADMIN_TELEGRAM_ID,
                text=text,
            )
            continue

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
        CommandHandler("audit", show_admin_audit),
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
            show_employee_details_stats,
            pattern=r"^employee_stats:\d+$",
        ),
        CallbackQueryHandler(
            process_rejected_order,
            pattern=r"^rejected_resolution:\d+:(return_to_stock|write_off)$",
        ),
        CallbackQueryHandler(
            process_retry_delivery,
            pattern=r"^retry_delivery:\d+$",
        ),
    ]