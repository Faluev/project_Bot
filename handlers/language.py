from telegram import Update
from handlers.employee import get_current_employee
from telegram.ext import ContextTypes

from database import toggle_employee_language
from handlers.menu import get_work_menu
from handlers.i18n import get_message


async def toggle_language(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    employee = get_current_employee(update, context)

    if employee is None or employee["application_status"] != "approved":
        await update.message.reply_text(
            get_message(employee["language"] if employee else "ru", "access_not_approved")
        )
        return

    language = toggle_employee_language(employee["id"])
    if language is None:
        await update.message.reply_text(
            get_message(employee["language"], "language_change_failed")
        )
        return

    await update.message.reply_text(
        get_message(language, "language_changed"),
        reply_markup=get_work_menu(
            employee["role"],
            employee["is_on_shift"],
            language,
        ),
    )
