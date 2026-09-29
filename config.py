import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent


def _required(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Не найден {name} в файле .env")
    return value


def _positive_int(name, default=None):
    raw = os.getenv(name, str(default) if default is not None else "").strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} должен быть целым числом") from error
    if value <= 0:
        raise ValueError(f"{name} должен быть больше нуля")
    return value


def _path_from_env(name, default):
    raw = os.getenv(name, default).strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def _log_level():
    value = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if value not in allowed:
        raise ValueError(
            f"LOG_LEVEL должен быть одним из: {', '.join(sorted(allowed))}"
        )
    return value


# Загружаем переменные из файла .env.
load_dotenv()


# ==========================================
# Telegram / администратор
# ==========================================

BOT_TOKEN = _required("BOT_TOKEN")
COURIER_BOT_TOKEN = _required("COURIER_BOT_TOKEN")

ADMIN_TELEGRAM_ID_TEXT = _required("ADMIN_TELEGRAM_ID")
try:
    ADMIN_TELEGRAM_ID = int(ADMIN_TELEGRAM_ID_TEXT)
except ValueError as error:
    raise ValueError("ADMIN_TELEGRAM_ID должен содержать только цифры") from error

if ADMIN_TELEGRAM_ID <= 0:
    raise ValueError("ADMIN_TELEGRAM_ID должен быть больше нуля")


# ==========================================
# Production-конфигурация
# ==========================================

DATABASE_PATH = _path_from_env("DATABASE_PATH", "delivery.db")
BACKUP_PATH = _path_from_env("BACKUP_PATH", "backups")
LOG_LEVEL = _log_level()
TIMEZONE = os.getenv("TIMEZONE", "Asia/Dushanbe").strip()

try:
    ZoneInfo(TIMEZONE)
except ZoneInfoNotFoundError as error:
    raise ValueError(f"TIMEZONE содержит неизвестную таймзону: {TIMEZONE}") from error

ORDER_TIMEOUT_MINUTES = _positive_int("ORDER_TIMEOUT_MINUTES", 15)
