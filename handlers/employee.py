from database import get_employee_by_telegram_id
from handlers.menu import get_work_menu_expanded


SUPPORTED_EMPLOYEE_ROLES = {"picker", "courier"}


def get_current_role(context):
    """
    Возвращает роль бота из application.bot_data.

    Каждый бот при запуске устанавливает:
    - picker для бота сборщика;
    - courier для бота курьера.
    """
    role = context.bot_data.get("role")
    if role not in SUPPORTED_EMPLOYEE_ROLES:
        return None
    return role


def get_current_employee(update, context):
    """
    Возвращает сотрудника текущей роли по Telegram ID.

    Это единая точка доступа к сотруднику для обработчиков.
    Она не позволяет случайно взять запись другой роли,
    если один Telegram-аккаунт зарегистрирован и сборщиком, и курьером.
    """
    user = update.effective_user
    if user is None:
        return None

    role = get_current_role(context)
    if role is None:
        return None

    return get_employee_by_telegram_id(user.id, role)


async def show_work_menu(update, context):
    """Раскрывает полное рабочее меню сотрудника."""
    employee = get_current_employee(update, context)
    if employee is None:
        await update.message.reply_text("⛔ Сотрудник не найден.")
        return

    await update.message.reply_text(
        "📋 Выберите действие:",
        reply_markup=get_work_menu_expanded(
            employee["role"],
            employee["is_on_shift"],
            employee["language"],
        ),
    )
