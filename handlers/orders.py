from telegram import (
from handlers.employee import get_current_employee
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes, ConversationHandler

from database import (
    get_new_orders,
    get_order_items,
    get_order_by_id,
    start_order_assembly,
    complete_order_assembly,
    mark_order_missing_item,
    get_employee_shift_report,
    get_on_shift_pickers,
    get_unnotified_new_orders,
    log_new_order_notification,
)
from handlers.i18n import get_message
from handlers.retry import send_message_with_retry

MISSING_ITEM = 5


def _employee_language(employee, user):
    if employee is not None:
        return employee["language"]
    return "tg" if user and user.language_code == "tg" else "ru"


def _order_action_error(language, reason):
    message_keys = {
        "not_found": "order_not_found",
        "already_taken": "order_taken",
        "not_picker": "not_assigned_picker",
        "wrong_status": "wrong_assembly_status",
        "not_approved": "not_approved",
        "inactive": "access_disabled",
        "off_shift": "start_shift",
        "wrong_role": "wrong_role",
    }
    return get_message(language, message_keys.get(reason, "action_failed"))


# ==========================================
# ПОКАЗ ТЕКУЩИХ НОВЫХ ЗАКАЗОВ
# ==========================================

async def show_current_orders(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_id = update.effective_user.id

    # Получаем сотрудника
    employee = get_current_employee(update, context)
    language = _employee_language(employee, update.effective_user)

    if employee is None:
        await update.message.reply_text(get_message(language, "not_registered"))
        return

    # Проверяем одобрение заявки
    if employee["application_status"] != "approved":
        await update.message.reply_text(get_message(language, "not_approved"))
        return

    # Проверяем активность
    if employee["is_active"] != 1:
        await update.message.reply_text(get_message(language, "access_disabled"))
        return

    if employee["is_on_shift"] != 1:
        await update.message.reply_text(get_message(language, "start_shift"))
        return

    # Проверяем роль
    if employee["role"] != "picker":
        await update.message.reply_text(get_message(language, "picker_only"))
        return

    # Получаем новые заказы
    orders = get_new_orders()

    if not orders:
        await update.message.reply_text(get_message(language, "no_picker_orders"))
        return

    # Показываем каждый заказ
    for order in orders:

        items = get_order_items(order["id"])

        item_text = "\n".join(
            f"• {item['product_name']} — {item['quantity']} шт."
            for item in items
        ) or "—"
        comment = (
            f"\n💬 Комментарий клиента:\n{order['client_comment']}\n"
            if order["client_comment"]
            else ""
        )

        # Способ оплаты
        if order["payment_method"] == "cash":
            payment_text = "Наличные"
        else:
            payment_text = order["payment_method"]

        message = get_message(
            employee["language"],
            "picker_order",
            order_number=order["order_number"],
            client=order["client_name"],
            phone=order["client_phone"],
            address=order["delivery_address"],
            items=item_text,
            comment=comment,
            payment=payment_text,
            amount=order["payment_amount"],
        )

        # Кнопка "Начать сборку"
        keyboard = [
            [
                InlineKeyboardButton(
                    get_message(employee["language"], "start_assembly_button"),
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


async def notify_pickers_about_new_orders(
    context: ContextTypes.DEFAULT_TYPE,
):
    """Отправляет новые заказы сборщикам на смене без дублей."""
    for employee in get_on_shift_pickers():
        for order in get_unnotified_new_orders(employee["id"]):
            items = get_order_items(order["id"])
            item_text = "\n".join(
                f"• {item['product_name']} — {item['quantity']} шт."
                for item in items
            ) or "—"
            message = get_message(
                employee["language"],
                "new_order",
                order_number=order["order_number"],
                client_name=order["client_name"],
                items=item_text,
                created_at=order["created_at"],
            )
            if order["client_comment"]:
                message += get_message(
                    employee["language"],
                    "comment",
                    comment=order["client_comment"],
                )

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "▶️ Начать сборку",
                    callback_data=f"start_assembly:{order['id']}",
                )]
            ])

            try:
                await send_message_with_retry(
                    context.bot,
                    chat_id=employee["telegram_id"],
                    text=message,
                    reply_markup=keyboard,
                )
            except Exception:
                continue

            log_new_order_notification(order["id"], employee["id"])


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
    employee = get_current_employee(update, context)
    language = _employee_language(employee, query.from_user)

    if employee is None:
        await query.answer(get_message(language, "not_registered"), show_alert=True)
        return

    # Проверяем заявку
    if employee["application_status"] != "approved":
        await query.answer(get_message(language, "not_approved"), show_alert=True)
        return

    # Проверяем активность
    if employee["is_active"] != 1:
        await query.answer(get_message(language, "access_disabled"), show_alert=True)
        return

    if employee["is_on_shift"] != 1:
        await query.answer(get_message(language, "start_shift"), show_alert=True)
        return

    # Проверяем роль
    if employee["role"] != "picker":
        await query.answer(get_message(language, "picker_only"), show_alert=True)
        return

    # Получаем ID заказа
    try:
        order_id = int(
            query.data.split(":")[1]
        )

    except (IndexError, ValueError):
        await query.answer(get_message(language, "invalid_order"), show_alert=True)
        return

    # Пытаемся назначить заказ сборщику
    result = start_order_assembly(
        order_id,
        employee["id"],
    )

    # Если назначить не получилось
    if not result["success"]:

        await query.answer(
            _order_action_error(language, result["reason"]),
            show_alert=True,
        )
        return

    # Заказ успешно принят
    order_number = result["order_number"]

    # Кнопка "Заказ собран" и "Товара нет"
    keyboard = [
        [
            InlineKeyboardButton(
                get_message(employee["language"], "complete_button"),
                callback_data=(
                    f"complete_assembly:{order_id}"
                ),
            ),
            InlineKeyboardButton(
                get_message(employee["language"], "missing_button"),
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
        get_message(
            employee["language"],
            "assembly_started",
            order_number=order_number,
        ),
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
    employee = get_current_employee(update, context)
    language = _employee_language(employee, query.from_user)

    if employee is None:
        await query.answer(get_message(language, "not_registered"), show_alert=True)
        return

    # Проверяем заявку
    if employee["application_status"] != "approved":
        await query.answer(get_message(language, "not_approved"), show_alert=True)
        return

    # Проверяем активность
    if employee["is_active"] != 1:
        await query.answer(get_message(language, "access_disabled"), show_alert=True)
        return

    # Проверяем роль
    if employee["role"] != "picker":
        await query.answer(get_message(language, "picker_only"), show_alert=True)
        return

    # Получаем ID заказа
    try:
        order_id = int(
            query.data.split(":")[1]
        )

    except (IndexError, ValueError):
        await query.answer(get_message(language, "invalid_order"), show_alert=True)
        return

    # Завершаем сборку
    result = complete_order_assembly(
        order_id,
        employee["id"],
    )

    # Если завершить не получилось
    if not result["success"]:

        await query.answer(
            _order_action_error(language, result["reason"]),
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
        get_message(
            employee["language"],
            "assembly_completed",
            order_number=order_number,
        )
    )


async def handle_missing_item(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    telegram_id = query.from_user.id

    employee = get_current_employee(update, context)
    language = _employee_language(employee, query.from_user)

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

    if employee["role"] != "picker":
        await query.answer(get_message(language, "picker_only"), show_alert=True)
        return

    try:
        order_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer(get_message(language, "invalid_order"), show_alert=True)
        return

    order = get_order_by_id(order_id)
    if order is None or order["status"] != "assembling" or order["picker_id"] != employee["id"]:
        await query.answer(
            get_message(language, "wrong_assembly_status"),
            show_alert=True,
        )
        return None

    context.user_data["missing_item_order_id"] = order_id
    await query.answer()
    await query.edit_message_text(
        get_message(
            employee["language"],
            "missing_item_prompt",
            order_number=order["order_number"],
        )
    )
    return MISSING_ITEM


async def save_missing_item(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    employee = get_current_employee(update, context)
    order_id = context.user_data.get("missing_item_order_id")
    item_name = update.message.text.strip()

    if employee is None or not order_id:
        await update.message.reply_text(
            get_message(_employee_language(employee, update.effective_user), "missing_order_input_lost")
        )
        return ConversationHandler.END

    if not item_name:
        await update.message.reply_text(
            get_message(employee["language"], "missing_item_name_prompt")
        )
        return MISSING_ITEM

    result = mark_order_missing_item(order_id, employee["id"], item_name)
    if not result["success"]:
        context.user_data.pop("missing_item_order_id", None)
        await update.message.reply_text(
            get_message(employee["language"], "stale_missing_item")
        )
        return ConversationHandler.END

    context.user_data.pop("missing_item_order_id", None)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            get_message(employee["language"], "complete_button"),
            callback_data=f"complete_assembly:{order_id}",
        )],
        [InlineKeyboardButton(
            get_message(employee["language"], "missing_button"),
            callback_data=f"missing_item:{order_id}",
        )],
    ])
    await update.message.reply_text(
        get_message(
            employee["language"],
            "missing_item_recorded",
            order_number=result["order_number"],
            item_name=item_name,
        ),
        reply_markup=keyboard,
    )
    return ConversationHandler.END


async def cancel_missing_item(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop("missing_item_order_id", None)
    employee = get_current_employee(update, context)
    language = _employee_language(employee, update.effective_user)
    await update.message.reply_text(get_message(language, "missing_cancelled"))
    return ConversationHandler.END


async def show_picker_shift_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    employee = get_current_employee(update, context)

    if (
        employee is None
        or employee["role"] != "picker"
        or employee["application_status"] != "approved"
        or employee["is_active"] != 1
    ):
        await update.message.reply_text(
            get_message(_employee_language(employee, update.effective_user), "picker_only")
        )
        return

    report = get_employee_shift_report(employee["id"])
    await update.message.reply_text(
        get_message(
            employee["language"],
            "picker_shift",
            assemblies=report["assembly_count"],
            missing=report["rejection_count"],
            actions=len(report["actions"]),
        )
    )