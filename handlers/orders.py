from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes, ConversationHandler

from handlers.employee import get_current_employee

from database import (
    get_new_orders,
    get_orders_for_picker,
    get_order_items,
    get_order_by_id,
    start_order_assembly,
    complete_order_assembly,
    mark_order_missing_item,
    get_employee_shift_report,
    get_on_shift_pickers,
    get_unnotified_new_orders,
    claim_new_order_notification,
    finalize_new_order_notification,
    release_new_order_notification_claim,
    log_new_order_notification,
    get_picker_order_notification_messages,
)
from handlers.i18n import get_message
from handlers.retry import send_message_with_retry
from handlers.courier_orders import notify_couriers_about_waiting_orders

MISSING_ITEM = 5


async def clear_stale_picker_order_messages(
    context: ContextTypes.DEFAULT_TYPE,
    order_id,
    employee_id,
):
    """Удаляет устаревшие уведомления этого заказа у других сборщиков."""
    for item in get_picker_order_notification_messages(
        order_id,
        exclude_employee_id=employee_id,
    ):
        try:
            await context.bot.delete_message(
                chat_id=item["telegram_id"],
                message_id=item["message_id"],
            )
        except Exception:
            # Уведомление могло уже быть удалено или стать недоступным.
            continue


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
        "not_oldest": "not_oldest",
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

    # Показываем только один актуальный заказ:
    # текущий заказ сборщика либо самый старый новый заказ из очереди.
    orders = get_orders_for_picker(employee["id"])

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
            created_at=order["created_at"],
        )

        if order["status"] == "assembling":
            # Заказ остаётся полностью видимым у сборщика после принятия:
            # товары, адрес, комментарий и оплата нужны во время сборки.
            message += "\n" + get_message(
                employee["language"],
                "assembly_started_suffix",
            )
            keyboard = [
                [
                    InlineKeyboardButton(
                        get_message(employee["language"], "complete_button"),
                        callback_data=f"complete_assembly:{order['id']}",
                    ),
                    InlineKeyboardButton(
                        get_message(employee["language"], "missing_button"),
                        callback_data=f"missing_item:{order['id']}",
                    ),
                ]
            ]
        else:
            # Вне сборки показываем только самый старый новый заказ.
            keyboard = [
                [
                    InlineKeyboardButton(
                        get_message(employee["language"], "start_assembly_button"),
                        callback_data=f"start_assembly:{order['id']}",
                    )
                ]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        existing_messages = [
            item
            for item in get_picker_order_notification_messages(
                order["id"],
            )
            if item["employee_id"] == employee["id"]
        ]

        if existing_messages:
            message_id = existing_messages[-1]["message_id"]
            for stale_message in existing_messages[:-1]:
                try:
                    await context.bot.delete_message(
                        chat_id=employee["telegram_id"],
                        message_id=stale_message["message_id"],
                    )
                except Exception:
                    pass
            try:
                await context.bot.edit_message_text(
                    chat_id=employee["telegram_id"],
                    message_id=message_id,
                    text=message,
                    reply_markup=reply_markup,
                )
                continue
            except Exception:
                pass

        sent_message = await update.message.reply_text(
            message,
            reply_markup=reply_markup,
        )

        # Ручной просмотр тоже фиксируем как показанное уведомление,
        # чтобы фоновые задачи не прислали эту же карточку повторно.
        log_new_order_notification(
            order["id"],
            employee["id"],
            getattr(sent_message, "message_id", None),
        )


async def notify_next_order_to_picker(
    context: ContextTypes.DEFAULT_TYPE,
    employee,
):
    """Сразу выдаёт освободившемуся сборщику следующий самый старый заказ."""
    orders = get_unnotified_new_orders(employee["id"])
    if not orders:
        return False

    order = orders[0]
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

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "▶️ Начать сборку",
            callback_data=f"start_assembly:{order['id']}",
        )
    ]])

    if not claim_new_order_notification(order["id"], employee["id"]):
        return False

    try:
        sent_message = await send_message_with_retry(
            context.bot,
            chat_id=employee["telegram_id"],
            text=message,
            reply_markup=keyboard,
        )
    except Exception:
        release_new_order_notification_claim(order["id"], employee["id"])
        return False

    message_id = getattr(sent_message, "message_id", None)
    if not finalize_new_order_notification(order["id"], employee["id"], message_id):
        release_new_order_notification_claim(order["id"], employee["id"])
        return False
    return True


async def notify_pickers_about_new_orders(
    context: ContextTypes.DEFAULT_TYPE,
):
    """Отправляет новые заказы сборщикам на смене без дублей."""
    for employee in get_on_shift_pickers():
        await notify_next_order_to_picker(context, employee)
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

        if result["reason"] == "picker_capacity_reached":
            current_orders = get_orders_for_picker(employee["id"])
            current_order = current_orders[0] if current_orders else None

            if current_order is not None:
                items = get_order_items(current_order["id"])
                item_text = "\n".join(
                    f"• {item['product_name']} — {item['quantity']} шт."
                    for item in items
                ) or "—"
                comment = (
                    f"\n💬 Комментарий клиента:\n{current_order['client_comment']}\n"
                    if current_order["client_comment"]
                    else ""
                )
                payment_text = (
                    "Наличные"
                    if current_order["payment_method"] == "cash"
                    else current_order["payment_method"]
                )
                current_message = get_message(
                    employee["language"],
                    "picker_order",
                    order_number=current_order["order_number"],
                    client=current_order["client_name"],
                    phone=current_order["client_phone"],
                    address=current_order["delivery_address"],
                    items=item_text,
                    comment=comment,
                    payment=payment_text,
                    amount=current_order["payment_amount"],
                    created_at=current_order["created_at"],
                )
                current_message += "\n" + get_message(
                    employee["language"],
                    "assembly_started_suffix",
                )
                current_keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        get_message(employee["language"], "complete_button"),
                        callback_data=f"complete_assembly:{current_order['id']}",
                    ),
                    InlineKeyboardButton(
                        get_message(employee["language"], "missing_button"),
                        callback_data=f"missing_item:{current_order['id']}",
                    ),
                ]])
                await query.answer(
                    "ℹ️ У вас уже есть заказ в сборке. Показываю его.",
                )
                try:
                    await query.edit_message_text(
                        current_message,
                        reply_markup=current_keyboard,
                    )
                except Exception:
                    pass
            else:
                await query.answer(
                    "ℹ️ У вас уже есть активный заказ.",
                    show_alert=True,
                )
            return

        error_message = _order_action_error(language, result["reason"])
        await query.answer(
            error_message,
            show_alert=True,
        )

        # Если заказ уже взял другой сборщик, убираем кнопку
        # из этого уведомления после первого неуспешного клика.
        if result["reason"] == "already_taken":
            try:
                await query.edit_message_text(error_message)
            except Exception:
                pass
        return

    # Убираем устаревшие карточки этого заказа у остальных сборщиков,
    # затем сразу выдаём им следующий самый старый доступный заказ.
    await clear_stale_picker_order_messages(context, order_id, employee["id"])
    await notify_pickers_about_new_orders(context)

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

    # Оставляем полную карточку заказа на экране после принятия.
    order = get_order_by_id(order_id)
    items = get_order_items(order_id)
    item_text = "\n".join(
        f"• {item['product_name']} — {item['quantity']} шт."
        for item in items
    ) or "—"
    comment = (
        f"\n💬 Комментарий клиента:\n{order['client_comment']}\n"
        if order["client_comment"]
        else ""
    )
    payment_text = "Наличные" if order["payment_method"] == "cash" else order["payment_method"]
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
    message += "\n" + get_message(
        employee["language"],
        "assembly_started_suffix",
    )
    await query.edit_message_text(
        message,
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

    # Курьер получает заказ сразу после завершения сборки.
    # Не ждём второго заказа и не прячем уведомление в фоновой задаче.
    await notify_couriers_about_waiting_orders(context)

    # После передачи курьеру сразу выдаём следующий самый старый заказ
    # освободившемуся сборщику — без ручного нажатия «Текущие заказы».
    await notify_next_order_to_picker(context, employee)
    # Остальным сборщикам также обновляем очередь.
    await notify_pickers_about_new_orders(context)

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


async def _render_picker_order_message(order, employee, suffix=True):
    """Собирает актуальную карточку заказа сборщика."""
    items = get_order_items(order["id"])
    item_lines = []
    for item in items:
        remaining = item["quantity"] - item["missing_quantity"]
        if remaining <= 0:
            continue
        if item["missing_quantity"]:
            item_lines.append(
                f"• {item['product_name']} — {remaining} шт. "
                f"(из {item['quantity']}; нет {item['missing_quantity']})"
            )
        else:
            item_lines.append(
                f"• {item['product_name']} — {item['quantity']} шт."
            )
    item_text = "\n".join(item_lines) or "—"
    payment_text = (
        "Наличные"
        if order["payment_method"] == "cash"
        else order["payment_method"]
    )
    message = get_message(
        employee["language"],
        "picker_order",
        order_number=order["order_number"],
        client=order["client_name"],
        phone=order["client_phone"],
        address=order["delivery_address"],
        items=item_text,
        comment=(
            f"\n💬 Комментарий клиента:\n{order['client_comment']}\n"
            if order["client_comment"]
            else ""
        ),
        payment=payment_text,
        amount=order["payment_amount"],
        created_at=order["created_at"],
    )
    if suffix:
        message += "\n" + get_message(
            employee["language"],
            "assembly_started_suffix",
        )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            get_message(employee["language"], "complete_button"),
            callback_data=f"complete_assembly:{order['id']}",
        ),
        InlineKeyboardButton(
            get_message(employee["language"], "missing_button"),
            callback_data=f"missing_item:{order['id']}",
        ),
    ]])
    return message, keyboard


def _missing_item_keyboard(order_id, item_id, remaining_quantity):
    buttons = []
    if remaining_quantity <= 20:
        for quantity in range(1, remaining_quantity + 1):
            buttons.append(
                InlineKeyboardButton(
                    f"{quantity} шт.",
                    callback_data=f"missing_item_qty:{order_id}:{item_id}:{quantity}",
                )
            )
    else:
        for quantity in range(1, 11):
            buttons.append(
                InlineKeyboardButton(
                    f"{quantity} шт.",
                    callback_data=f"missing_item_qty:{order_id}:{item_id}:{quantity}",
                )
            )
        buttons.append(
            InlineKeyboardButton(
                f"Все {remaining_quantity} шт.",
                callback_data=(
                    f"missing_item_qty:{order_id}:{item_id}:{remaining_quantity}"
                ),
            )
        )

    rows = [buttons[index:index + 5] for index in range(0, len(buttons), 5)]
    rows.append([
        InlineKeyboardButton(
            "↩️ Назад к товарам",
            callback_data=f"missing_item_back_to_items:{order_id}",
        )
    ])
    return InlineKeyboardMarkup(rows)


async def handle_missing_item(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Показывает только ещё не обработанные позиции заказа."""
    query = update.callback_query
    employee = get_current_employee(update, context)
    language = _employee_language(employee, query.from_user)
    await query.answer()

    if employee is None:
        await query.edit_message_text(get_message(language, "not_registered"))
        return

    if employee["application_status"] != "approved":
        await query.edit_message_text(get_message(language, "not_approved"))
        return

    if employee["is_active"] != 1:
        await query.edit_message_text(get_message(language, "access_disabled"))
        return

    if employee["is_on_shift"] != 1:
        await query.edit_message_text(get_message(language, "start_shift"))
        return

    if employee["role"] != "picker":
        await query.edit_message_text(get_message(language, "picker_only"))
        return

    try:
        order_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.edit_message_text(get_message(language, "invalid_order"))
        return

    order = get_order_by_id(order_id)
    if (
        order is None
        or order["status"] != "assembling"
        or order["picker_id"] != employee["id"]
    ):
        await query.edit_message_text(get_message(language, "wrong_assembly_status"))
        return

    items = [
        item
        for item in get_order_items(order_id)
        if item["quantity"] - item["missing_quantity"] > 0
    ]

    if not items:
        await query.edit_message_text(
            get_message(
                employee["language"],
                "missing_all_items_recorded",
                order_number=order["order_number"],
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_message(employee["language"], "complete_button"),
                    callback_data=f"complete_assembly:{order_id}",
                )
            ]]),
        )
        return

    keyboard = []
    for item in items:
        remaining = item["quantity"] - item["missing_quantity"]
        label = f"❌ {item['product_name']} — {remaining} шт."
        keyboard.append([InlineKeyboardButton(
            label,
            callback_data=f"missing_item_select:{order_id}:{item['id']}",
        )])
    keyboard.append([InlineKeyboardButton(
        "↩️ Назад к заказу",
        callback_data=f"missing_item_back:{order_id}",
    )])

    await query.edit_message_text(
        get_message(
            employee["language"],
            "missing_item_choose",
            order_number=order["order_number"],
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def select_missing_item(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Показывает выбор количества отсутствующих единиц конкретной позиции."""
    query = update.callback_query
    employee = get_current_employee(update, context)
    language = _employee_language(employee, query.from_user)
    await query.answer()

    if employee is None or employee["application_status"] != "approved":
        await query.edit_message_text(get_message(language, "not_approved"))
        return
    if employee["is_active"] != 1:
        await query.edit_message_text(get_message(language, "access_disabled"))
        return
    if employee["is_on_shift"] != 1:
        await query.edit_message_text(get_message(language, "start_shift"))
        return
    if employee["role"] != "picker":
        await query.edit_message_text(get_message(language, "picker_only"))
        return

    try:
        _, order_id_text, item_id_text = query.data.split(":", 2)
        order_id = int(order_id_text)
        item_id = int(item_id_text)
    except (ValueError, IndexError):
        await query.edit_message_text(get_message(language, "invalid_order"))
        return

    order = get_order_by_id(order_id)
    if (
        order is None
        or order["status"] != "assembling"
        or order["picker_id"] != employee["id"]
    ):
        await query.edit_message_text(get_message(language, "wrong_assembly_status"))
        return

    item = next(
        (row for row in get_order_items(order_id) if row["id"] == item_id),
        None,
    )
    if item is None:
        await query.edit_message_text(get_message(language, "missing_item_not_found"))
        return

    remaining = item["quantity"] - item["missing_quantity"]
    if remaining <= 0:
        await query.edit_message_text(
            get_message(employee["language"], "missing_item_not_found")
        )
        return

    await query.edit_message_text(
        get_message(
            employee["language"],
            "missing_item_quantity",
            item_name=item["product_name"],
            remaining_quantity=remaining,
        ),
        reply_markup=_missing_item_keyboard(
            order_id,
            item_id,
            remaining,
        ),
    )


async def confirm_missing_item_quantity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Сохраняет выбранное количество отсутствующих единиц."""
    query = update.callback_query
    employee = get_current_employee(update, context)
    language = _employee_language(employee, query.from_user)
    await query.answer()

    if employee is None or employee["application_status"] != "approved":
        await query.edit_message_text(get_message(language, "not_approved"))
        return
    if employee["is_active"] != 1:
        await query.edit_message_text(get_message(language, "access_disabled"))
        return
    if employee["is_on_shift"] != 1 or employee["role"] != "picker":
        await query.edit_message_text(get_message(language, "wrong_role"))
        return

    try:
        _, order_id_text, item_id_text, quantity_text = query.data.split(":", 3)
        order_id = int(order_id_text)
        item_id = int(item_id_text)
        missing_quantity = int(quantity_text)
    except (ValueError, IndexError):
        await query.edit_message_text(get_message(language, "invalid_order"))
        return

    order = get_order_by_id(order_id)
    if (
        order is None
        or order["status"] != "assembling"
        or order["picker_id"] != employee["id"]
    ):
        await query.edit_message_text(get_message(language, "wrong_assembly_status"))
        return

    item = next(
        (row for row in get_order_items(order_id) if row["id"] == item_id),
        None,
    )
    if item is None:
        await query.edit_message_text(get_message(language, "missing_item_not_found"))
        return

    result = mark_order_missing_item(
        order_id,
        employee["id"],
        item["product_name"],
        item_id=item_id,
        missing_quantity=missing_quantity,
    )
    if not result["success"]:
        await query.edit_message_text(
            get_message(employee["language"], "stale_missing_item")
        )
        return

    await query.edit_message_text(
        get_message(
            employee["language"],
            "missing_item_recorded",
            order_number=result["order_number"],
            item_name=item["product_name"],
            missing_quantity=result["missing_quantity"],
            remaining_quantity=result["remaining_quantity"],
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                get_message(employee["language"], "complete_button"),
                callback_data=f"complete_assembly:{order_id}",
            )],
            [InlineKeyboardButton(
                get_message(employee["language"], "missing_button"),
                callback_data=f"missing_item:{order_id}",
            )],
        ]),
    )


async def back_to_missing_items(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Возвращает список ещё не обработанных позиций."""
    query = update.callback_query
    employee = get_current_employee(update, context)
    language = _employee_language(employee, query.from_user)
    await query.answer()

    if employee is None or employee["application_status"] != "approved":
        await query.edit_message_text(get_message(language, "not_approved"))
        return

    try:
        order_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.edit_message_text(get_message(language, "invalid_order"))
        return

    order = get_order_by_id(order_id)
    if (
        order is None
        or order["status"] != "assembling"
        or order["picker_id"] != employee["id"]
    ):
        await query.edit_message_text(get_message(language, "wrong_assembly_status"))
        return

    items = [
        item
        for item in get_order_items(order_id)
        if item["quantity"] - item["missing_quantity"] > 0
    ]
    if not items:
        await query.edit_message_text(
            get_message(
                employee["language"],
                "missing_all_items_recorded",
                order_number=order["order_number"],
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    get_message(employee["language"], "complete_button"),
                    callback_data=f"complete_assembly:{order_id}",
                )
            ]]),
        )
        return

    keyboard = [
        [InlineKeyboardButton(
            f"❌ {item['product_name']} — "
            f"{item['quantity'] - item['missing_quantity']} шт.",
            callback_data=f"missing_item_select:{order_id}:{item['id']}",
        )]
        for item in items
    ]
    keyboard.append([InlineKeyboardButton(
        "↩️ Назад к заказу",
        callback_data=f"missing_item_back:{order_id}",
    )])

    await query.edit_message_text(
        get_message(
            employee["language"],
            "missing_item_choose",
            order_number=order["order_number"],
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def back_from_missing_item_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Возвращает полноценную карточку текущего заказа."""
    query = update.callback_query
    employee = get_current_employee(update, context)
    language = _employee_language(employee, query.from_user)
    await query.answer()

    try:
        order_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.edit_message_text(get_message(language, "invalid_order"))
        return

    if employee is None:
        await query.edit_message_text(get_message(language, "not_registered"))
        return

    order = get_order_by_id(order_id)
    if (
        order is None
        or order["picker_id"] != employee["id"]
        or order["status"] != "assembling"
    ):
        await query.edit_message_text(get_message(language, "wrong_assembly_status"))
        return

    message, keyboard = await _render_picker_order_message(order, employee)
    await query.edit_message_text(message, reply_markup=keyboard)



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