from telegram import Update
from telegram.ext import ContextTypes

from database import get_employee_by_telegram_id, toggle_employee_shift
from handlers.menu import get_work_menu


async def toggle_shift(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    employee = get_employee_by_telegram_id(update.effective_user.id)

    if employee is None or employee["application_status"] != "approved":
        await update.message.reply_text("⛔ У вас нет одобренного доступа.")
        return

    if employee["is_active"] != 1:
        await update.message.reply_text("⛔ Ваш рабочий доступ отключён.")
        return

    is_on_shift = toggle_employee_shift(employee["id"])
    if is_on_shift is None:
        await update.message.reply_text("❌ Не удалось изменить статус смены.")
        return

    status_text = "🟢 Вы вышли на смену." if is_on_shift else "🔴 Вы завершили смену."
    await update.message.reply_text(
        status_text,
        reply_markup=get_work_menu(employee["role"], is_on_shift),
    )
