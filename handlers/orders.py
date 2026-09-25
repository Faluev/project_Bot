from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes

from database import (
    get_new_orders,
    get_order_items,
    start_order_assembly,
    complete_order_assembly,
    mark_order_missing_item,
    get_employee_by_telegram_id,
    get_employee_shift_report,
)


# ==========================================
# ПОКАЗ ТЕКУЩИХ НОВЫХ ЗАКАЗОВ
# ==========================================

async def show_current_orders(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_id = update.effective_user.id

    # Получаем сотрудника
    employee = get_employee_by_telegram_id(telegram_id)

    if employee is None:
        await update.message.reply_text(
            "⛔ Вы не зарегистрированы "
            "как сотрудник."
        )
        return

    # Проверяем одобрение заявки
    if employee["application_status"] != "approved":
        await update.message.reply_text(
            "⛔ Ваша заявка ещё не одобрена "
            "администратором."
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
    if employee["role"] != "picker":
        await update.message.reply_text(
            "⛔ Этот раздел предназначен "
            "только для сборщиков."
        )
        return

    # Получаем новые заказы
    orders = get_new_orders()

    if not orders:
        await update.message.reply_text(
            "📦 Сейчас новых заказов нет."
        )
        return

    # Показываем каждый заказ
    for order in orders:

        items = get_order_items(order["id"])

        message = (
            f"📦 Заказ № {order['order_number']}\n\n"
            f"👤 Клиент: {order['client_name']}\n"
            f"📞 Телефон: {order['client_phone']}\n\n"
            f"📍 Адрес:\n"
            f"{order['delivery_address']}\n\n"
            f"🛒 Товары:\n"
        )

        # Добавляем товары
        for item in items:
            message += (
                f"• {item['product_name']} — "
                f"{item['quantity']} шт.\n"
            )

        # Комментарий клиента
        if order["client_comment"]:
            message += (
                f"\n💬 Комментарий клиента:\n"
                f"{order['client_comment']}\n"
            )

        # Способ оплаты
        if order["payment_method"] == "cash":
            payment_text = "Наличные"
        else:
            payment_text = order["payment_method"]

        message += (
            f"\n💳 Оплата: {payment_text}\n"
            f"💰 Сумма: {order['payment_amount']}\n"
        )

        # Кнопка "Начать сборку"
        keyboard = [
            [
                InlineKeyboardButton(
                    "▶️ Начать сборку",
                    callback_data=(
                        f"start_assembly:{order['id']}"
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


# ==========================================
# НАЧАТЬ СБОРКУ
# ==========================================

async def start_assembly(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    telegram_id = query.from_user.id

    # Получаем сотрудника
    employee = get_employee_by_telegram_id(
        telegram_id
    )

    if employee is None:
        await query.answer(
            "Вы не зарегистрированы.",
            show_alert=True,
        )
        return

    # Проверяем заявку
    if employee["application_status"] != "approved":
        await query.answer(
            "Ваша заявка не одобрена.",
            show_alert=True,
        )
        return

    # Проверяем активность
    if employee["is_active"] != 1:
        await query.answer(
            "Ваш рабочий доступ отключён.",
            show_alert=True,
        )
        return

    if employee["is_on_shift"] != 1:
        await query.answer("Сначала начните смену.", show_alert=True)
        return

    # Проверяем роль
    if employee["role"] != "picker":
        await query.answer(
            "Эта кнопка доступна только сборщику.",
            show_alert=True,
        )
        return

    # Получаем ID заказа
    try:
        order_id = int(
            query.data.split(":")[1]
        )

    except (IndexError, ValueError):
        await query.answer(
            "Некорректный номер заказа.",
            show_alert=True,
        )
        return

    # Пытаемся назначить заказ сборщику
    result = start_order_assembly(
        order_id,
        employee["id"],
    )

    # Если назначить не получилось
    if not result["success"]:

        if result["reason"] == "already_taken":
            message = (
                "⚠️ Этот заказ уже взят "
                "другим сборщиком."
            )

        elif result["reason"] == "not_found":
            message = "❌ Заказ не найден."

        else:
            message = (
                "❌ Не удалось принять заказ."
            )

        await query.answer(
            message,
            show_alert=True,
        )
        return

    # Заказ успешно принят
    order_number = result["order_number"]

    # Кнопка "Заказ собран" и "Товара нет"
    keyboard = [
        [
            InlineKeyboardButton(
                "📦 Заказ собран",
                callback_data=(
                    f"complete_assembly:{order_id}"
                ),
            ),
            InlineKeyboardButton(
                "⚠️ Товара нет",
                callback_data=(
                    f"missing_item:{order_id}"
                ),
            ),
        ]
    ]

    reply_markup = InlineKeyboardMarkup(
        keyboard
    )

    await query.answer(
        "✅ Заказ принят!",
        show_alert=True,
    )

    # Обновляем сообщение
    await query.edit_message_text(
        f"🔨 Заказ № {order_number}\n\n"
        "Вы начали сборку этого заказа.\n\n"
        "Когда закончите сборку, "
        "нажмите кнопку ниже.",
        reply_markup=reply_markup,
    )


# ==========================================
# ЗАКАЗ СОБРАН
# ==========================================

async def complete_assembly(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    telegram_id = query.from_user.id

    # Получаем сотрудника
    employee = get_employee_by_telegram_id(
        telegram_id
    )

    if employee is None:
        await query.answer(
            "Вы не зарегистрированы.",
            show_alert=True,
        )
        return

    # Проверяем заявку
    if employee["application_status"] != "approved":
        await query.answer(
            "Ваша заявка не одобрена.",
            show_alert=True,
        )
        return

    # Проверяем активность
    if employee["is_active"] != 1:
        await query.answer(
            "Ваш рабочий доступ отключён.",
            show_alert=True,
        )
        return

    # Проверяем роль
    if employee["role"] != "picker":
        await query.answer(
            "Эта функция доступна только сборщику.",
            show_alert=True,
        )
        return

    # Получаем ID заказа
    try:
        order_id = int(
            query.data.split(":")[1]
        )

    except (IndexError, ValueError):
        await query.answer(
            "Некорректный номер заказа.",
            show_alert=True,
        )
        return

    # Завершаем сборку
    result = complete_order_assembly(
        order_id,
        employee["id"],
    )

    # Если завершить не получилось
    if not result["success"]:

        if result["reason"] == "not_found":
            message = "❌ Заказ не найден."

        elif result["reason"] == "not_picker":
            message = (
                "⛔ Вы не являетесь сборщиком "
                "этого заказа."
            )

        elif result["reason"] == "wrong_status":
            message = (
                "⚠️ Этот заказ уже не находится "
                "на этапе сборки."
            )

        else:
            message = (
                "❌ Не удалось завершить сборку."
            )

        await query.answer(
            message,
            show_alert=True,
        )
        return

    # Сборка успешно завершена
    order_number = result["order_number"]

    await query.answer(
        "✅ Заказ собран!",
        show_alert=True,
    )

    await query.edit_message_text(
        f"📦 Заказ № {order_number}\n\n"
        "✅ Заказ собран.\n\n"
        "🚚 Теперь заказ ожидает курьера."
    )


async def handle_missing_item(
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

    if employee["role"] != "picker":
        await query.answer("Эта кнопка доступна только сборщику.", show_alert=True)
        return

    try:
        order_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer("Некорректный номер заказа.", show_alert=True)
        return

    result = mark_order_missing_item(order_id, employee["id"])

    if not result["success"]:
        if result["reason"] == "not_found":
            message = "❌ Заказ не найден."
        elif result["reason"] == "not_picker":
            message = "⛔ Вы не являетесь сборщиком этого заказа."
        elif result["reason"] == "wrong_status":
            message = "⚠️ Заказ уже не находится на этапе сборки."
        else:
            message = "❌ Не удалось отметить отсутствие товара."

        await query.answer(message, show_alert=True)
        return

    order_number = result["order_number"]

    await query.answer("⚠️ Отмечено: товара нет.", show_alert=True)
    await query.edit_message_text(
        f"📦 Заказ № {order_number}\n\n"
        "⚠️ Сборщик отметил, что товара нет.\n\n"
        "Администратор получил уведомление для принятия решения."
    )


async def show_picker_shift_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    employee = get_employee_by_telegram_id(update.effective_user.id)

    if employee is None or employee["role"] != "picker":
        await update.message.reply_text("⛔ Раздел доступен только сборщикам.")
        return

    report = get_employee_shift_report(employee["id"])
    await update.message.reply_text(
        "📋 История смены\n\n"
        f"Собрано заказов: {report['assembly_count']}\n"
        f"Отмечено отсутствий товара: {report['rejection_count']}\n"
        f"Действий за сегодня: {len(report['actions'])}"
    )