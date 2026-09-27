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
from handlers.retry import send_message_with_retry
from handlers.i18n import get_message

# Состояния регистрации
FIRST_LAST_NAME, PHONE, ROLE, ADMIN_CONTACT = range(4)
COURIER_TRANSPORT = 4


def get_preferred_language(update, context):
    language = context.user_data.get("registration_language")
    if language in ("ru", "tg"):
        return language

    telegram_language = (update.effective_user.language_code or "ru").split("-")[0]
    language = "tg" if telegram_language == "tg" else "ru"
    context.user_data["registration_language"] = language
    return language


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

    language = (
        employee["language"]
        if employee is not None
        else get_preferred_language(update, context)
    )

    if employee is not None and employee["role"] != "picker":
        await update.message.reply_text(
            "⛔ Этот бот предназначен для сборщиков. "
            "Для работы курьером зарегистрируйтесь в боте курьера."
        )
        return ConversationHandler.END

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
                employee["language"],
            )

            await update.message.reply_text(
                get_message(
                    employee["language"],
                    "picker_login",
                    name=employee["first_name"],
                ),
                reply_markup=menu,
            )

            return ConversationHandler.END

        # ======================================
        # ЗАЯВКА ЕЩЁ НА РАССМОТРЕНИИ
        # ======================================

        if employee["application_status"] == "pending":

            await update.message.reply_text(
                get_message(language, "registration_pending")
            )

            return ConversationHandler.END

    # ==========================================
    # НОВАЯ РЕГИСТРАЦИЯ
    # ==========================================

    context.user_data["registration_role"] = "picker"
    await update.message.reply_text(
        get_message(language, "picker_welcome")
    )

    return FIRST_LAST_NAME


async def start_courier_registration(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_id = update.effective_user.id
    employee = get_employee_by_telegram_id(telegram_id)

    if employee is not None and employee["role"] != "courier":
        role_name = "сборщик" if employee["role"] == "picker" else employee["role"]
        await update.message.reply_text(
            f"⛔ Этот Telegram-аккаунт уже зарегистрирован как {role_name}. "
            "Для другой роли нужен отдельный Telegram-аккаунт."
        )
        return ConversationHandler.END

    language = (
        employee["language"]
        if employee is not None
        else get_preferred_language(update, context)
    )
    context.user_data["registration_language"] = language

    if employee is not None and employee["application_status"] == "approved":
        if employee["is_active"] != 1:
            await update.message.reply_text("⛔ Ваш рабочий доступ отключён.")
            return ConversationHandler.END

        await update.message.reply_text(
            get_message(language, "registration_approved"),
            reply_markup=get_work_menu(
                "courier",
                employee["is_on_shift"],
                employee["language"],
            ),
        )
        return ConversationHandler.END

    if employee is not None and employee["application_status"] == "pending":
        await update.message.reply_text(get_message(language, "registration_pending"))
        return ConversationHandler.END

    context.user_data["registration_role"] = "courier"
    await update.message.reply_text(get_message(language, "courier_welcome"))
    return FIRST_LAST_NAME


async def get_courier_transport(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    transport_type = update.message.text.strip()
    if not transport_type:
        await update.message.reply_text(
            get_message(
                get_preferred_language(update, context),
                "registration_transport_prompt",
            )
        )
        return COURIER_TRANSPORT

    full_name = context.user_data.get("full_name", "").strip().split(maxsplit=1)
    if len(full_name) < 2:
        await update.message.reply_text(
            "Укажите имя и фамилию через пробел, затем начните регистрацию командой /start."
        )
        return ConversationHandler.END

    result = create_employee_application(
        telegram_id=update.effective_user.id,
        telegram_username=update.effective_user.username,
        first_name=full_name[0],
        last_name=full_name[1],
        phone=context.user_data["phone"],
        role="courier",
        transport_type=transport_type,
        language=get_preferred_language(update, context),
    )

    message_key = {
        "created": "registration_created",
        "recreated": "registration_resubmitted",
        "already_pending": "registration_pending",
        "already_approved": "registration_approved",
    }.get(result, "registration_failed")
    await update.message.reply_text(
        get_message(get_preferred_language(update, context), message_key)
    )
    context.user_data.pop("registration_role", None)
    context.user_data.pop("registration_language", None)
    return ConversationHandler.END

async def get_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Получаем имя и фамилию сотрудника.
    """

    language = get_preferred_language(update, context)
    full_name = update.message.text.strip()
    if len(full_name.split(maxsplit=1)) < 2:
        await update.message.reply_text(
            get_message(language, "registration_invalid_name")
        )
        return FIRST_LAST_NAME

    context.user_data["full_name"] = full_name

    phone_button = KeyboardButton(
        get_message(language, "registration_phone_button"),
        request_contact=True,
    )

    keyboard = ReplyKeyboardMarkup(
        [[phone_button]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await update.message.reply_text(
        get_message(language, "registration_phone_prompt"),
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
        phone = (update.message.text or "").strip()

    if not phone:
        await update.message.reply_text(
            get_message(
                get_preferred_language(update, context),
                "registration_phone_prompt",
            )
        )
        return PHONE

    context.user_data["phone"] = phone
    language = get_preferred_language(update, context)

    if context.user_data.get("registration_role") == "courier":
        await update.message.reply_text(
            get_message(
                get_preferred_language(update, context),
                "registration_transport_prompt",
            ),
            reply_markup=None,
        )
        return COURIER_TRANSPORT

    keyboard = ReplyKeyboardMarkup(
        [[get_message(language, "registration_picker_role")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await update.message.reply_text(
        get_message(
            get_preferred_language(update, context),
            "registration_role_prompt",
        ),
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
    role = context.user_data.get("registration_role")
    language = get_preferred_language(update, context)

    if role != "picker" or role_text != get_message(language, "registration_picker_role"):
        await update.message.reply_text(
            get_message(language, "registration_invalid_role")
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
            get_message(language, "registration_invalid_name")
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
        language=language,
    )

    message_key = {
        "created": "registration_created",
        "recreated": "registration_resubmitted",
        "already_pending": "registration_pending",
        "already_approved": "registration_approved",
    }.get(result, "registration_failed")

    await update.message.reply_text(
        get_message(language, message_key),
        reply_markup=None,
    )
    context.user_data.pop("registration_role", None)

    print(
        "Заявка сотрудника:",
        result,
        telegram_id,
        role,
    )
    context.user_data.pop("registration_role", None)
    context.user_data.pop("registration_language", None)

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

    await send_message_with_retry(
        context.bot,
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

    for key in (
        "registration_role",
        "registration_language",
        "full_name",
        "phone",
    ):
        context.user_data.pop(key, None)

    return ConversationHandler.END