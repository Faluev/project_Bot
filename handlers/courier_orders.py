from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes

from config import ADMIN_TELEGRAM_ID
from database import (
    get_orders_waiting_for_courier,
    get_employee_by_telegram_id,
    get_order_by_id,
    pickup_order,
    deliver_order,
    reject_order,
    get_employee_shift_report,
)


def build_client_contact_message(order, custom_message):
    """
    Формирует текст для клиента в рамках связи через курьера.
    """
    payment_text = "Наличные" if order["payment_method"] == "cash" else order["payment_method"]

    return (
        "📦 Уведомление по заказу\n\n"
        f"Заказ № {order['order_number']}\n"
        f"Клиент: {order['client_name']}\n"
        f"Адрес: {order['delivery_address']}\n"
        f"Способ оплаты: {payment_text}\n"
        f"Сумма: {order['payment_amount']}\n\n"
        f"Сообщение курьера:\n{custom_message}"
    )


async def show_courier_shift_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    employee = get_employee_by_telegram_id(update.effective_user.id)

    if employee is None or employee["role"] != "courier":
        await update.message.reply_text("⛔ Раздел доступен только курьерам.")
        return

    report = get_employee_shift_report(employee["id"])
    await update.message.reply_text(
        "📋 Доставки за смену\n\n"
        f"Доставлено заказов: {report['delivery_count']}\n"
        f"Отклонено заказов: {report['rejection_count']}\n"
        f"Действий за сегодня: {len(report['actions'])}"
    )


async def show_courier_orders(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_id = update.effective_user.id

    # Получаем сотрудника
    employee = get_employee_by_telegram_id(
        telegram_id
    )

    if employee is None:
        await update.message.reply_text(
            "⛔ Вы не зарегистрированы "
            "как сотрудник."
        )
        return

    # Проверяем одобрение
    if employee["application_status"] != "approved":
        await update.message.reply_text(
            "⛔ Ваша заявка ещё не одобрена."
        )
        return

    # Проверяем активность
    if employee["is_active"] != 1:
        await update.message.reply_text(
            "⛔ Ваш рабочий доступ отключён."
        )
        return

    if employee["is_on_shift"] != 1:
        await update.message.reply_text("🔴 Сначала начните смену.")
        return

    # Проверяем роль
    if employee["role"] != "courier":
        await update.message.reply_text(
            "⛔ Этот раздел предназначен "
            "только для курьеров."
        )
        return

    # Получаем заказы
    orders = get_orders_waiting_for_courier()

    if not orders:
        await update.message.reply_text(
            "🚚 Сейчас заказов для курьера нет."
        )
        return

    for order in orders:

        message = (
            f"🚚 Заказ № {order['order_number']}\n\n"
            f"👤 Клиент: {order['client_name']}\n"
            f"📞 Телефон: {order['client_phone']}\n\n"
            f"📍 Адрес доставки:\n"
            f"{order['delivery_address']}\n"
        )

        if order["client_comment"]:
            message += (
                f"\n💬 Комментарий клиента:\n"
                f"{order['client_comment']}\n"
            )

        if order["payment_method"] == "cash":
            payment_text = "Наличные"
        else:
            payment_text = order["payment_method"]

        message += (
            f"\n💳 Оплата: {payment_text}\n"
            f"💰 Сумма: {order['payment_amount']}\n"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "🚚 Забрал заказ",
                    callback_data=(
                        f"pickup_order:{order['id']}"
                    ),
                )
            ]
        ]

        reply_markup = InlineKeyboardMarkup(
            keyboard
        )

        await update.message.reply_text(
            message,
            reply_markup=reply_markup,
        )


async def handle_pickup_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    telegram_id = query.from_user.id

    employee = get_employee_by_telegram_id(telegram_id)

    if employee is None:
        await query.answer("Вы не зарегистрированы.", show_alert=True)
        return

    if employee["application_status"] != "approved":
        await query.answer("Ваша заявка не одобрена.", show_alert=True)
        return

    if employee["is_active"] != 1:
        await query.answer("Ваш рабочий доступ отключён.", show_alert=True)
        return

    if employee["is_on_shift"] != 1:
        await query.answer("Сначала начните смену.", show_alert=True)
        return

    if employee["role"] != "courier":
        await query.answer("Эта кнопка доступна только курьеру.", show_alert=True)
        return

    try:
        order_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer("Некорректный номер заказа.", show_alert=True)
        return

    result = pickup_order(order_id, employee["id"])

    if not result["success"]:
        if result["reason"] == "already_taken":
            message = "⚠️ Этот заказ уже забрал другой курьер."
        elif result["reason"] == "not_found":
            message = "❌ Заказ не найден."
        else:
            message = "❌ Не удалось забрать заказ."

        await query.answer(message, show_alert=True)
        return

    order_number = result["order_number"]
    delivery_keyboard = [[
        InlineKeyboardButton(
            "✅ Доставлен",
            callback_data=f"deliver_order:{order_id}",
        ),
        InlineKeyboardButton(
            "❌ Заказ отклонён",
            callback_data=f"reject_order:{order_id}",
        ),
    ], [
        InlineKeyboardButton(
            "📨 Связаться с клиентом",
            callback_data=f"contact_client:{order_id}",
        )
    ]]

    await query.answer("✅ Заказ у вас!", show_alert=True)

    await query.edit_message_text(
        f"🚚 Заказ № {order_number}\n\n"
        "✅ Вы забрали заказ. Статус: «В пути».\n\n"
        "Когда клиент получит заказ, нажмите кнопку ниже.",
        reply_markup=InlineKeyboardMarkup(delivery_keyboard),
    )


async def handle_contact_client(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Отправляет короткое уведомление клиенту через Telegram,
    если у заказа есть идентификатор клиента.
    Если нет — направляет уведомление администратору.
    """
    query = update.callback_query
    telegram_id = query.from_user.id

    employee = get_employee_by_telegram_id(telegram_id)
    if employee is None:
        await query.answer("Вы не зарегистрированы.", show_alert=True)
        return

    if employee["application_status"] != "approved":
        await query.answer("Ваша заявка не одобрена.", show_alert=True)
        return

    if employee["role"] != "courier":
        await query.answer("Эта кнопка доступна только курьеру.", show_alert=True)
        return

    try:
        order_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer("Некорректный номер заказа.", show_alert=True)
        return

    order = get_order_by_id(order_id)
    if order is None:
        await query.answer("❌ Заказ не найден.", show_alert=True)
        return

    message_text = build_client_contact_message(
        order,
        "Курьер уже в пути и будет рядом в ближайшее время."
    )

    client_telegram_id = None
    if "client_telegram_id" in order.keys() and order["client_telegram_id"]:
        client_telegram_id = order["client_telegram_id"]

    if client_telegram_id:
        await context.bot.send_message(
            chat_id=client_telegram_id,
            text=message_text,
        )
        await query.answer("✅ Сообщение отправлено клиенту.", show_alert=True)
        await query.edit_message_text(
            f"🚚 Заказ № {order['order_number']}\n\n"
            "✅ Сообщение отправлено клиенту."
        )
        return

    await context.bot.send_message(
        chat_id=ADMIN_TELEGRAM_ID,
        text=(
            "📩 У курьера нет Telegram-канала для клиента\n\n"
            f"Заказ: {order['order_number']}\n"
            f"Клиент: {order['client_name']}\n"
            f"Телефон: {order['client_phone']}\n"
            f"Адрес: {order['delivery_address']}\n\n"
            "Нужно связаться с клиентом по телефону или другим доступным каналом."
        ),
    )

    await query.answer(
        "ℹ️ Клиент не привязан к Telegram-чату, уведомление отправлено администратору.",
        show_alert=True,
    )
    await query.edit_message_text(
        f"🚚 Заказ № {order['order_number']}\n\n"
        "ℹ️ Для этого заказа нет Telegram-клиента, поэтому уведомление направлено администратору."
    )


async def handle_deliver_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    telegram_id = query.from_user.id
    employee = get_employee_by_telegram_id(telegram_id)

    if employee is None:
        await query.answer("Вы не зарегистрированы.", show_alert=True)
        return

    if employee["application_status"] != "approved":
        await query.answer("Ваша заявка не одобрена.", show_alert=True)
        return

    if employee["is_active"] != 1:
        await query.answer("Ваш рабочий доступ отключён.", show_alert=True)
        return

    if employee["is_on_shift"] != 1:
        await query.answer("Сначала начните смену.", show_alert=True)
        return

    if employee["role"] != "courier":
        await query.answer("Эта кнопка доступна только курьеру.", show_alert=True)
        return

    try:
        order_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer("Некорректный номер заказа.", show_alert=True)
        return

    result = deliver_order(order_id, employee["id"])

    if not result["success"]:
        if result["reason"] == "not_found":
            message = "❌ Заказ не найден."
        elif result["reason"] == "wrong_status":
            message = "⚠️ Заказ уже не находится в доставке."
        elif result["reason"] == "not_courier":
            message = "⛔ Вы не являетесь курьером этого заказа."
        else:
            message = "❌ Не удалось завершить доставку."

        await query.answer(message, show_alert=True)
        return

    order_number = result["order_number"]

    await query.answer("✅ Заказ доставлен!", show_alert=True)

    await query.edit_message_text(
        f"🚚 Заказ № {order_number}\n\n"
        "✅ Заказ успешно доставлен клиенту."
    )


async def handle_reject_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    telegram_id = query.from_user.id

    employee = get_employee_by_telegram_id(telegram_id)

    if employee is None:
        await query.answer("Вы не зарегистрированы.", show_alert=True)
        return

    if employee["application_status"] != "approved":
        await query.answer("Ваша заявка не одобрена.", show_alert=True)
        return

    if employee["is_active"] != 1:
        await query.answer("Ваш рабочий доступ отключён.", show_alert=True)
        return

    if employee["is_on_shift"] != 1:
        await query.answer("Сначала начните смену.", show_alert=True)
        return

    if employee["role"] != "courier":
        await query.answer("Эта кнопка доступна только курьеру.", show_alert=True)
        return

    try:
        order_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer("Некорректный номер заказа.", show_alert=True)
        return

    reason_keyboard = [
        [
            InlineKeyboardButton("Клиент не вышел", callback_data=f"reject_reason:{order_id}:client_no_show"),
            InlineKeyboardButton("Отказался", callback_data=f"reject_reason:{order_id}:refused"),
        ],
        [
            InlineKeyboardButton("Неверный адрес", callback_data=f"reject_reason:{order_id}:wrong_address"),
            InlineKeyboardButton("Другое", callback_data=f"reject_reason:{order_id}:other"),
        ],
    ]

    await query.answer("Выберите причину отклонения.", show_alert=True)
    await query.edit_message_text(
        "⚠️ Выберите причину отклонения заказа:",
        reply_markup=InlineKeyboardMarkup(reason_keyboard),
    )


async def handle_reject_reason(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    telegram_id = query.from_user.id

    employee = get_employee_by_telegram_id(telegram_id)

    if employee is None:
        await query.answer("Вы не зарегистрированы.", show_alert=True)
        return

    if employee["application_status"] != "approved":
        await query.answer("Ваша заявка не одобрена.", show_alert=True)
        return

    if employee["is_active"] != 1:
        await query.answer("Ваш рабочий доступ отключён.", show_alert=True)
        return

    if employee["role"] != "courier":
        await query.answer("Эта кнопка доступна только курьеру.", show_alert=True)
        return

    try:
        _, order_id_text, reason_code = query.data.split(":", 2)
        order_id = int(order_id_text)
    except (ValueError, TypeError, IndexError):
        await query.answer("Некорректная причина отклонения.", show_alert=True)
        return

    reason_map = {
        "client_no_show": "Клиент не вышел / не отвечает",
        "refused": "Клиент отказался от заказа",
        "wrong_address": "Неверный адрес",
        "other": "Другое (указано вручную)",
    }

    reason = reason_map.get(reason_code, "Причина не указана")
    result = reject_order(order_id, employee["id"], reason)

    if not result["success"]:
        if result["reason"] == "not_found":
            message = "❌ Заказ не найден."
        elif result["reason"] == "wrong_status":
            message = "⚠️ Заказ уже не находится в доставке."
        elif result["reason"] == "not_courier":
            message = "⛔ Вы не являетесь курьером этого заказа."
        else:
            message = "❌ Не удалось отклонить заказ."

        await query.answer(message, show_alert=True)
        return

    await query.answer("✅ Заказ отклонён.", show_alert=True)
    await query.edit_message_text(
        f"🚚 Заказ № {result['order_number']}\n\n"
        "❌ Заказ отклонён.\n\n"
        f"Причина: {reason}\n\n"
        "Администратор получил уведомление для решения по возврату товара."
    )
