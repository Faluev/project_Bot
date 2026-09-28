from telegram import Update
from telegram.ext import ContextTypes

from database import get_employee_by_telegram_id, toggle_employee_shift
from handlers.menu import get_work_menu
from handlers.i18n import get_message


async def toggle_shift(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    employee = get_employee_by_telegram_id(
        update.effective_user.id,
        context.bot_data.get("role"),
    )

    if employee is None or employee["application_status"] != "approved":
        await update.message.reply_text(
            get_message(employee["language"] if employee else "ru", "access_not_approved")
        )
        return

    if employee["is_active"] != 1:
        await update.message.reply_text(get_message(employee["language"], "disabled_access"))
        return

    is_on_shift = toggle_employee_shift(employee["id"])
    if is_on_shift is None:
        await update.message.reply_text(
            get_message(employee["language"], "shift_change_failed")
        )
        return

    status_key = "shift_started" if is_on_shift else "shift_ended"
    status_text = get_message(employee["language"], status_key)
    await update.message.reply_text(
        status_text,
        reply_markup=get_work_menu(
            employee["role"],
            is_on_shift,
            employee["language"],
        ),
    )
