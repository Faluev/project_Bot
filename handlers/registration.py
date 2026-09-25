from telegram import Update, ReplyKeyboardMarkup, KeyboardButton

from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import ADMIN_TELEGRAM_ID

from database import (
    create_employee_application,
    get_employee_by_telegram_id,
)

from handlers.menu import get_work_menu

# Состояния регистрации
FIRST_LAST_NAME, PHONE, ROLE, ADMIN_CONTACT = range(4)


def build_admin_contact_message(employee, text):
    """
    Формирует текст сообщения для администратора.
    """
    username = employee["telegram_username"]
    telegram_label = f"@{username}" if username else "не указан"
    role_label = {
        "picker": "Сборщик",
        "courier": "Курьер",
    }.get(employee["role"], employee["role"])

    return (
        "📩 Сообщение от сотрудника\n\n"
        f"Имя: {employee['first_name']} {employee['last_name']}\n"
        f"Роль: {role_label}\n"
        f"Telegram: {telegram_label}\n\n"
        f"Текст сообщения:\n{text}"
    )


async def start_registration(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Обрабатывает команду /start.

    Проверяет, есть ли пользователь в базе
    и какой у него статус.
    """

    telegram_id = update.effective_user.id

    employee = get_employee_by_telegram_id(
        telegram_id
    )

    # ==========================================
    # СОТРУДНИК ОДОБРЕН
    # ==========================================

    if employee:

        if (
            employee["application_status"] == "approved"
            and employee["is_active"] == 1
        ):

            role_name = {
                "picker": "👷 Сборщик",
                "courier": "🚚 Курьер",
            }.get(
                employee["role"],
                employee["role"],
            )

            menu = get_work_menu(
                employee["role"],
                employee["is_on_shift"],
            )

            await update.message.reply_text(
                f"Здравствуйте, "
                f"{employee['first_name']}! 👋\n\n"
                f"Ваша роль: {role_name}\n\n"
                "Выберите нужное действие:",
                reply_markup=menu,
            )

            return ConversationHandler.END

        # ======================================
        # ЗАЯВКА ЕЩЁ НА РАССМОТРЕНИИ
        # ======================================

        if employee["application_status"] == "pending":

            await update.message.reply_text(
                "⏳ Ваша заявка ещё находится "
                "на рассмотрении администратора.\n\n"
                "Пожалуйста, дождитесь решения."
            )

            return ConversationHandler.END

    # ==========================================
    # НОВАЯ РЕГИСТРАЦИЯ
    # ==========================================

    await update.message.reply_text(
        "Добро пожаловать! 👋\n\n"
        "Для регистрации напишите ваше имя и фамилию."
    )

    return FIRST_LAST_NAME

async def get_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Получаем имя и фамилию сотрудника.
    """

    context.user_data["full_name"] = update.message.text

    phone_button = KeyboardButton(
        "📱 Отправить номер телефона",
        request_contact=True,
    )

    keyboard = ReplyKeyboardMarkup(
        [[phone_button]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await update.message.reply_text(
        "Отлично.\n\n"
        "Теперь отправьте ваш номер телефона "
        "нажав кнопку ниже.",
        reply_markup=keyboard,
    )

    return PHONE


async def get_phone(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Получаем номер телефона.
    """

    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text

    context.user_data["phone"] = phone

    keyboard = ReplyKeyboardMarkup(
        [
            ["👷 Сборщик"],
            ["🚚 Курьер"],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await update.message.reply_text(
        "Выберите вашу роль:",
        reply_markup=keyboard,
    )

    return ROLE


async def get_role(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Получаем выбранную роль
    и сохраняем заявку в базе данных.
    """

    role_text = update.message.text

    if "Сборщик" in role_text:
        role = "picker"

    elif "Курьер" in role_text:
        role = "courier"

    else:
        await update.message.reply_text(
            "Пожалуйста, выберите одну из кнопок."
        )
        return ROLE

    # Получаем данные пользователя Telegram
    telegram_id = update.effective_user.id
    telegram_username = update.effective_user.username

    # Получаем имя и фамилию
    full_name = context.user_data["full_name"]

    name_parts = full_name.strip().split(maxsplit=1)

    # Проверяем, что пользователь действительно указал
    # имя и фамилию
    if len(name_parts) < 2:
        await update.message.reply_text(
            "Пожалуйста, укажите имя и фамилию через пробел.\n\n"
            "Например:\n"
            "Иван Иванов"
        )

        return FIRST_LAST_NAME

    first_name = name_parts[0]
    last_name = name_parts[1]

    phone = context.user_data["phone"]

    # Сохраняем заявку в базе данных
    result = create_employee_application(
        telegram_id=telegram_id,
        telegram_username=telegram_username,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        role=role,
    )

    # Обрабатываем результат
    if result == "created":
        message = (
            "✅ Заявка на регистрацию создана.\n\n"
            "Ваши данные отправлены на проверку "
            "администратору.\n"
            "После рассмотрения вы получите сообщение "
            "с результатом."
        )

    elif result == "recreated":
        message = (
            "✅ Заявка отправлена повторно.\n\n"
            "Ожидайте решения администратора."
        )

    elif result == "already_pending":
        message = (
            "ℹ️ Ваша заявка уже находится "
            "на рассмотрении администратора."
        )

    elif result == "already_approved":
        message = (
            "✅ Вы уже зарегистрированы "
            "и ваша заявка одобрена."
        )

    else:
        message = (
            "Произошла ошибка при сохранении заявки.\n"
            "Попробуйте ещё раз позже."
        )

    await update.message.reply_text(
        message,
        reply_markup=None,
    )

    print(
        "Заявка сотрудника:",
        result,
        telegram_id,
        role,
    )

    return ConversationHandler.END


async def start_contact_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Начинает чат с администратором через пересылку текста.
    """
    telegram_id = update.effective_user.id
    employee = get_employee_by_telegram_id(telegram_id)

    if employee is None:
        await update.message.reply_text(
            "⛔ Вы не зарегистрированы как сотрудник."
        )
        return ConversationHandler.END

    if employee["application_status"] != "approved":
        await update.message.reply_text(
            "⛔ Ваша заявка ещё не одобрена администратором."
        )
        return ConversationHandler.END

    if employee["is_active"] != 1:
        await update.message.reply_text(
            "⛔ Ваш рабочий доступ отключён."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "💬 Напишите сообщение администратору.\n\n"
        "После отправки оно будет переслано в админку."
    )

    context.user_data["admin_contact_active"] = True
    return ADMIN_CONTACT


async def handle_contact_admin_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Пересылает текст сотрудника администратору.
    """
    telegram_id = update.effective_user.id
    employee = get_employee_by_telegram_id(telegram_id)

    if employee is None:
        await update.message.reply_text("⛔ Сотрудник не найден.")
        return ConversationHandler.END

    message_text = update.message.text.strip()

    if not message_text:
        await update.message.reply_text("⚠️ Сообщение пустое. Напишите текст снова.")
        return ADMIN_CONTACT

    admin_message = build_admin_contact_message(
        employee,
        message_text,
    )

    await context.bot.send_message(
        chat_id=ADMIN_TELEGRAM_ID,
        text=admin_message,
    )

    await update.message.reply_text(
        "✅ Сообщение отправлено администратору."
    )

    context.user_data.pop("admin_contact_active", None)
    return ConversationHandler.END


async def cancel_contact_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Отмена диалога с администратором.
    """
    context.user_data.pop("admin_contact_active", None)

    await update.message.reply_text(
        "❌ Диалог с администратором закрыт."
    )

    return ConversationHandler.END


async def cancel_registration(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Отмена регистрации.
    """

    await update.message.reply_text(
        "Регистрация отменена."
    )

    return ConversationHandler.END