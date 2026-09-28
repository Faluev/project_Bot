import os

from dotenv import load_dotenv


# Загружаем переменные из файла .env
load_dotenv()


# ==========================================
# БОТ СБОРЩИКА
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError(
        "Не найден BOT_TOKEN в файле .env"
    )


# ==========================================
# БОТ КУРЬЕРА
# ==========================================

COURIER_BOT_TOKEN = os.getenv("COURIER_BOT_TOKEN")

if not COURIER_BOT_TOKEN:
    raise ValueError(
        "Не найден COURIER_BOT_TOKEN в файле .env"
    )


# ==========================================
# АДМИНИСТРАТОР
# ==========================================

ADMIN_TELEGRAM_ID_TEXT = os.getenv("ADMIN_TELEGRAM_ID")

if not ADMIN_TELEGRAM_ID_TEXT:
    raise ValueError(
        "Не найден ADMIN_TELEGRAM_ID в файле .env"
    )

try:
    ADMIN_TELEGRAM_ID = int(ADMIN_TELEGRAM_ID_TEXT)

except ValueError:
    raise ValueError(
        "ADMIN_TELEGRAM_ID должен содержать только цифры"
    )


try:
    ORDER_TIMEOUT_MINUTES = int(os.getenv("ORDER_TIMEOUT_MINUTES", "15"))
except ValueError as error:
    raise ValueError("ORDER_TIMEOUT_MINUTES должен быть целым числом") from error

if ORDER_TIMEOUT_MINUTES <= 0:
    raise ValueError("ORDER_TIMEOUT_MINUTES должен быть больше нуля")