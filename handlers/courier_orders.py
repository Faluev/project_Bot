import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes, ConversationHandler

from config import ADMIN_TELEGRAM_ID
from database import (
    get_orders_waiting_for_courier,
    get_employee_by_telegram_id,
    get_order_by_id,
    pickup_order,
    deliver_order,
    reject_order,
    get_employee_shift_report,
    get_on_shift_couriers,
    get_unnotified_courier_orders,
    log_courier_order_notification,
)
from handlers.i18n import get_message
from handlers.retry import send_message_with_retry

logger = logging.getLogger(__name__)
REJECTION_REASON = 6


async def notify_customer_status(context, order_id, language, message_key):
    order = get_order_by_id(order_id)
    if order is None or not order["client_telegram_id"]:
        return

    try:
        await send_message_with_retry(
            context.bot,
            chat_id=order["client_telegram_id"],
            text=get_message(
                language,
                message_key,
                order_number=order["order_number"],
            ),
        )
    except Exception:
        logger.exception("Could not notify client for order %s", order_id)


async def notify_admin_about_rejection(context, order_id, order_number, reason):
    try:
        await send_message_with_retry(
            context.bot,
            chat_id=ADMIN_TELEGRAM_ID,
            text=(
                f"⚠️ Курьер отклонил заказ № {order_number}.\n"
                f"Причина: {reason}\n"
                "Решение по товару: команда /rejected"
            ),
        )
    except Exception:
        logger.exception("Could not notify admin about rejected order %s", order_id)


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


async def notify_couriers_about_waiting_orders(
    context: ContextTypes.DEFAULT_TYPE,
):
    """Deliver awaiting-courier orders to active couriers on shift."""
    for employee in get_on_shift_couriers():
        for order in get_unnotified_courier_orders(employee["id"]):
            payment = "Наличные" if order["payment_method"] == "cash" else order["payment_method"]
            comment = (
                get_message(employee["language"], "comment", comment=order["client_comment"])
                if order["client_comment"]
                else ""
            )
            text = get_message(
                employee["language"],
                "courier_order",
                order_number=order["order_number"],
                client_name=order["client_name"],
                phone=order["client_phone"],
                address=order["delivery_address"],
                comment=comment,
                payment=payment,
                amount=order["payment_amount"],
            )
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_message(employee["language"], "pickup_button"),
                    callback_data=f"pickup_order:{order['id']}",
                )
            ]])

            try:
                await send_message_with_retry(
                    context.bot,
                    chat_id=employee["telegram_id"],
                    text=text,
                    reply_markup=keyboard,
                )
            except Exception:
                continue

            log_courier_order_notification(order["id"], employee["id"])


async def show_courier_shift_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    employee = get_employee_by_telegram_id(update.effective_user.id)

    if (
        employee is None
        or employee["role"] != "courier"
        or employee["application_status"] != "approved"
        or employee["is_active"] != 1
    ):
        await update.message.reply_text("⛔ Раздел доступен только курьерам.")
        return

    report = get_employee_shift_report(employee["id"])
    await update.message.reply_text(
        get_message(
            employee["language"],
            "courier_shift",
            deliveries=report["delivery_count"],
            rejections=report["rejection_count"],
            actions=len(report["actions"]),
        )
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
            get_message("ru", "not_registered")
        )
        return

    # Проверяем одобрение
    if employee["application_status"] != "approved":
        await update.message.reply_text(
            get_message(employee["language"], "not_approved")
        )
        return

    # Проверяем активность
    if employee["is_active"] != 1:
        await update.message.reply_text(
            get_message(employee["language"], "access_disabled")
        )
        return

    if employee["is_on_shift"] != 1:
        await update.message.reply_text(
            get_message(employee["language"], "start_shift")
        )
        return

    # Проверяем роль
    if employee["role"] != "courier":
        await update.message.reply_text(
            get_message(employee["language"], "wrong_role")
        )
        return

    # Получаем заказы
    orders = get_orders_waiting_for_courier()

    if not orders:
        await update.message.reply_text(
            get_message(employee["language"], "empty_courier_orders")
        )
        return

    for order in orders:

        if order["payment_method"] == "cash":
            payment_text = "Наличные"
        else:
            payment_text = order["payment_method"]

        comment = (
            f"💬 Комментарий клиента: {order['client_comment']}"
            if order["client_comment"]
            else ""
        )
        message = get_message(
            employee["language"],
            "courier_order",
            order_number=order["order_number"],
            client_name=order["client_name"],
            phone=order["client_phone"],
            address=order["delivery_address"],
            comment=comment,
            payment=payment_text,
            amount=order["payment_amount"],
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    get_message(employee["language"], "pickup_button"),
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
        language = "tg" if query.from_user.language_code == "tg" else "ru"
        await query.answer(get_message(language, "not_registered"), show_alert=True)
        return

    language = employee["language"]

    if employee["application_status"] != "approved":
        await query.answer(get_message(language, "not_approved"), show_alert=True)
        return

    if employee["is_active"] != 1:
        await query.answer(get_message(language, "access_disabled"), show_alert=True)
        return

    if employee["is_on_shift"] != 1:
        await query.answer(get_message(language, "start_shift"), show_alert=True)
        return

    if employee["role"] != "courier":
        await query.answer(get_message(language, "wrong_role"), show_alert=True)
        return

    try:
        order_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer(get_message(language, "invalid_order"), show_alert=True)
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
    await query.answer(
        get_message(employee["language"], "picked_alert"),
        show_alert=True,
    )
    await notify_customer_status(
        context,
        order_id,
        employee["language"],
        "client_in_delivery",
    )
    delivery_keyboard = [[
        InlineKeyboardButton(
            get_message(employee["language"], "deliver_button"),
            callback_data=f"deliver_order:{order_id}",
        ),
        InlineKeyboardButton(
            get_message(employee["language"], "reject_button"),
            callback_data=f"reject_order:{order_id}",
        ),
    ], [
        InlineKeyboardButton(
            get_message(employee["language"], "contact_button"),
            callback_data=f"contact_client:{order_id}",
        )
    ]]

    await query.edit_message_text(
        get_message(
            employee["language"],
            "picked_order",
            order_number=order_number,
        ),
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
        language = "tg" if query.from_user.language_code == "tg" else "ru"
        await query.answer(get_message(language, "not_registered"), show_alert=True)
        return

    language = employee["language"]
    if employee["application_status"] != "approved":
        await query.answer(get_message(language, "not_approved"), show_alert=True)
        return

    if employee["is_active"] != 1 or employee["is_on_shift"] != 1:
        await query.answer(get_message(language, "start_shift"), show_alert=True)
        return

    if employee["role"] != "courier":
        await query.answer(get_message(language, "wrong_role"), show_alert=True)
        return

    try:
        order_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer(get_message(language, "invalid_order"), show_alert=True)
        return

    order = get_order_by_id(order_id)
    if order is None:
        await query.answer(
            get_message(employee["language"], "order_not_found"),
            show_alert=True,
        )
        return

    if order["courier_id"] != employee["id"] or order["status"] != "in_delivery":
        await query.answer(get_message(language, "not_courier"), show_alert=True)
        return

    message_text = build_client_contact_message(
        order,
        "Курьер уже в пути и будет рядом в ближайшее время."
    )

    client_telegram_id = None
    if "client_telegram_id" in order.keys() and order["client_telegram_id"]:
        client_telegram_id = order["client_telegram_id"]

    if client_telegram_id:
        await query.answer()
        await send_message_with_retry(
            context.bot,
            chat_id=client_telegram_id,
            text=message_text,
        )
        await query.edit_message_text(
            f"🚚 Заказ № {order['order_number']}\n\n"
            "✅ Сообщение отправлено клиенту."
        )
        return

    await query.answer()
    await send_message_with_retry(
        context.bot,
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
    language = employee["language"] if employee is not None else "ru"

    if employee is None:
        await query.answer(get_message(language, "not_registered"), show_alert=True)
        return

    if employee["application_status"] != "approved":
        await query.answer(get_message(language, "not_approved"), show_alert=True)
        return

    if employee["is_active"] != 1:
        await query.answer(get_message(language, "access_disabled"), show_alert=True)
        return

    if employee["is_on_shift"] != 1:
        await query.answer(get_message(language, "start_shift"), show_alert=True)
        return

    if employee["role"] != "courier":
        await query.answer(get_message(language, "wrong_role"), show_alert=True)
        return

    try:
        order_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer(get_message(language, "invalid_order"), show_alert=True)
        return

    result = deliver_order(order_id, employee["id"])

    if not result["success"]:
        if result["reason"] == "not_found":
            message = get_message(language, "order_not_found")
        elif result["reason"] == "wrong_status":
            message = get_message(language, "wrong_delivery_status")
        elif result["reason"] == "not_courier":
            message = get_message(language, "not_courier")
        else:
            message = get_message(language, "delivery_failed")

        await query.answer(message, show_alert=True)
        return

    order_number = result["order_number"]
    await query.answer(
        get_message(employee["language"], "delivered_alert"),
        show_alert=True,
    )
    await notify_customer_status(
        context,
        order_id,
        employee["language"],
        "client_delivered",
    )

    await query.edit_message_text(
        get_message(
            employee["language"],
            "delivered_order",
            order_number=order_number,
        )
    )


async def handle_reject_order(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    telegram_id = query.from_user.id

    employee = get_employee_by_telegram_id(telegram_id)

    if employee is None:
        language = "tg" if query.from_user.language_code == "tg" else "ru"
        await query.answer(get_message(language, "not_registered"), show_alert=True)
        return

    language = employee["language"]
    if employee["application_status"] != "approved":
        await query.answer(get_message(language, "not_approved"), show_alert=True)
        return

    if employee["is_active"] != 1:
        await query.answer(get_message(language, "access_disabled"), show_alert=True)
        return

    if employee["is_on_shift"] != 1:
        await query.answer(get_message(language, "start_shift"), show_alert=True)
        return

    if employee["role"] != "courier":
        await query.answer(get_message(language, "wrong_role"), show_alert=True)
        return

    try:
        order_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer(get_message(language, "invalid_order"), show_alert=True)
        return

    reason_keyboard = [
        [
            InlineKeyboardButton(get_message(employee["language"], "reason_no_show"), callback_data=f"reject_reason:{order_id}:client_no_show"),
            InlineKeyboardButton(get_message(employee["language"], "reason_refused"), callback_data=f"reject_reason:{order_id}:refused"),
        ],
        [
            InlineKeyboardButton(get_message(employee["language"], "reason_wrong_address"), callback_data=f"reject_reason:{order_id}:wrong_address"),
            InlineKeyboardButton(get_message(employee["language"], "reason_other"), callback_data=f"reject_reason:{order_id}:other"),
        ],
    ]

    await query.answer("Выберите причину отклонения.", show_alert=True)
    await query.edit_message_text(
        get_message(employee["language"], "reject_prompt"),
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
        language = "tg" if query.from_user.language_code == "tg" else "ru"
        await query.answer(get_message(language, "not_registered"), show_alert=True)
        return

    language = employee["language"]
    if employee["application_status"] != "approved":
        await query.answer(get_message(language, "not_approved"), show_alert=True)
        return

    if employee["is_active"] != 1:
        await query.answer(get_message(language, "access_disabled"), show_alert=True)
        return

    if employee["role"] != "courier":
        await query.answer(get_message(language, "wrong_role"), show_alert=True)
        return

    try:
        _, order_id_text, reason_code = query.data.split(":", 2)
        order_id = int(order_id_text)
    except (ValueError, TypeError, IndexError):
        await query.answer(get_message(language, "invalid_order"), show_alert=True)
        return

    reason_map = {
        "client_no_show": "Клиент не вышел / не отвечает",
        "refused": "Клиент отказался от заказа",
        "wrong_address": "Неверный адрес",
        "other": "Другое (указано вручную)",
    }

    reason = reason_map.get(reason_code, "Причина не указана")
    if reason_code == "other":
        context.user_data["pending_rejection_order_id"] = order_id
        await query.answer()
        await query.edit_message_text(
            get_message(employee["language"], "custom_rejection_prompt")
        )
        return REJECTION_REASON

    result = reject_order(order_id, employee["id"], reason)

    if not result["success"]:
        if result["reason"] == "not_found":
            message = get_message(employee["language"], "order_not_found")
        elif result["reason"] == "wrong_status":
            message = get_message(employee["language"], "wrong_delivery_status")
        elif result["reason"] == "not_courier":
            message = get_message(employee["language"], "not_courier")
        else:
            message = get_message(employee["language"], "rejection_failed")

        await query.answer(message, show_alert=True)
        return

    await query.answer(
        get_message(employee["language"], "rejected_alert"),
        show_alert=True,
    )
    await notify_admin_about_rejection(
        context,
        order_id,
        result["order_number"],
        reason,
    )
    await notify_customer_status(
        context,
        order_id,
        employee["language"],
        "client_rejected",
    )

    await query.edit_message_text(
        f"🚚 Заказ № {result['order_number']}\n\n"
        "❌ Заказ отклонён.\n\n"
        f"Причина: {reason}\n\n"
        "Администратор получил уведомление для решения по возврату товара."
    )


async def save_custom_rejection_reason(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    employee = get_employee_by_telegram_id(update.effective_user.id)
    order_id = context.user_data.pop("pending_rejection_order_id", None)
    reason = update.message.text.strip()

    if employee is None or not order_id:
        await update.message.reply_text("⚠️ Не найдена активная заявка на отказ.")
        return ConversationHandler.END

    if not reason:
        await update.message.reply_text("Напишите причину отказа текстом.")
        context.user_data["pending_rejection_order_id"] = order_id
        return REJECTION_REASON

    result = reject_order(order_id, employee["id"], reason)
    if not result["success"]:
        await update.message.reply_text(
            "⚠️ Заказ уже изменился либо ваша смена завершена."
        )
        return ConversationHandler.END

    await notify_admin_about_rejection(
        context,
        order_id,
        result["order_number"],
        reason,
    )
    await notify_customer_status(
        context,
        order_id,
        employee["language"],
        "client_rejected",
    )
    await update.message.reply_text(
        f"✅ Заказ № {result['order_number']} отклонён. Причина: {reason}"
    )
    return ConversationHandler.END


async def cancel_custom_rejection_reason(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop("pending_rejection_order_id", None)
    await update.message.reply_text("Отказ от заказа отменён; заказ остался у вас в работе.")
    return ConversationHandler.END
