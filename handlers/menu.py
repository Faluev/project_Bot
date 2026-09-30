from telegram import ReplyKeyboardMarkup


def get_picker_menu(is_on_shift=False, language="ru"):
    """
    Рабочее меню сборщика.
    """

    if language == "tg":
        keyboard = [
            ["📦 Фармоишҳои ҷорӣ"],
            ["📋 Таърихи навбат"],
            ["🟢 Анҷоми навбат" if is_on_shift else "🔴 Оғози навбат"],
            ["👨‍💼 Тамос бо администратор"],
            ["🌐 Забон / Язык"],
        ]
    else:
        keyboard = [
            ["📦 Текущие заказы"],
            ["📋 История смены"],
            ["🟢 Завершить смену" if is_on_shift else "🔴 Начать смену"],
            ["👨‍💼 Связаться с администратором"],
            ["🌐 Язык / Забон"],
        ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )


def get_courier_menu(is_on_shift=False, language="ru"):
    """
    Рабочее меню курьера.
    """

    if language == "tg":
        keyboard = [
            ["🚚 Фармоиши ҷорӣ"],
            ["📋 Доставкаҳои навбат"],
            ["🟢 Анҷоми навбат" if is_on_shift else "🔴 Оғози навбат"],
            ["👨‍💼 Тамос бо администратор"],
            ["🌐 Забон / Язык"],
        ]
    else:
        keyboard = [
            ["🚚 Текущий заказ"],
            ["📋 Доставки за смену"],
            ["🟢 Завершить смену" if is_on_shift else "🔴 Начать смену"],
            ["👨‍💼 Связаться с администратором"],
            ["🌐 Язык / Забон"],
        ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )


def get_work_menu_expanded(role, is_on_shift=False, language="ru"):
    """Полное рабочее меню сотрудника."""
    if role == "picker":
        return get_picker_menu(is_on_shift, language)
    if role == "courier":
        return get_courier_menu(is_on_shift, language)
    return None


def get_work_menu_compact(role, is_on_shift=False, language="ru"):
    """Компактное рабочее меню: одна кнопка, остальные действия раскрываются отдельно."""
    return ReplyKeyboardMarkup(
        [["\u2630 Меню"]],
        resize_keyboard=True,
    )


def get_work_menu(role, is_on_shift=False, language="ru"):
    """Полное рабочее меню для внутреннего использования и совместимости."""
    return get_work_menu_expanded(role, is_on_shift, language)

def get_admin_mode_menu():
    """Меню выбора режима для администратора в боте сборщика."""
    return ReplyKeyboardMarkup(
        [["👨‍💼 Админка"], ["👷 Сборщик"]],
        resize_keyboard=True,
    )


def get_registration_menu():
    """Стартовое меню для нового пользователя."""
    return ReplyKeyboardMarkup(
        [["🚀 Начать"]],
        resize_keyboard=True,
    )


def get_admin_menu():
    """Постоянное меню администратора."""
    keyboard = [
        ["📋 Заявки", "👥 Сотрудники"],
        ["📦 Заказы", "📊 Статистика"],
        ["⏰ Таймауты", "📜 Аудит"],
        ["⚠️ Отклонённые", "🆔 Мой ID"],
        ["👷 Регистрация сборщика"],
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )
