from database import reset_test_employee


# ==========================================
# ВСТАВЬ СЮДА СВОЙ TELEGRAM ID
# ==========================================

TELEGRAM_ID =1489820047


deleted_count = reset_test_employee(
    TELEGRAM_ID
)


if deleted_count == 1:
    print(
        "✅ Тестовый сотрудник успешно сброшен."
    )

elif deleted_count == 0:
    print(
        "ℹ️ Сотрудник с таким Telegram ID "
        "не найден."
    )

else:
    print(
        f"⚠️ Удалено записей: {deleted_count}"
    )