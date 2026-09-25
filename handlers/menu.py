from telegram import ReplyKeyboardMarkup


def get_picker_menu(is_on_shift=False):
    """
    Рабочее меню сборщика.
    """

    keyboard = [
        ["📦 Текущие заказы"],
        ["📋 История смены"],
        ["🟢 Завершить смену" if is_on_shift else "🔴 Начать смену"],
        ["👨‍💼 Связаться с администратором"],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )


def get_courier_menu(is_on_shift=False):
    """
    Рабочее меню курьера.
    """

    keyboard = [
        ["🚚 Текущий заказ"],
        ["📋 Доставки за смену"],
        ["🟢 Завершить смену" if is_on_shift else "🔴 Начать смену"],
        ["👨‍💼 Связаться с администратором"],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )


def get_work_menu(role, is_on_shift=False):
    """
    Возвращает меню в зависимости от роли сотрудника.
    """

    if role == "picker":
        return get_picker_menu(is_on_shift)

    if role == "courier":
        return get_courier_menu(is_on_shift)

    return None