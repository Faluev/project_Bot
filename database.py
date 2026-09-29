import sqlite3
from pathlib import Path


# Путь к файлу базы данных
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "delivery.db"


# Разрешённые переходы статусов заказа.
# Любое бизнес-действие, меняющее статус, должно проходить через эту схему.
ORDER_STATUSES = {
    "new",
    "assembling",
    "awaiting_courier",
    "in_delivery",
    "delivered",
    "rejected",
}

EMPLOYEE_ROLES = {"picker", "courier"}
APPLICATION_STATUSES = {"pending", "approved", "rejected"}

ORDER_STATUS_TRANSITIONS = {
    "new": {"assembling"},
    "assembling": {"awaiting_courier"},
    "awaiting_courier": {"in_delivery"},
    "in_delivery": {"delivered", "rejected"},
    "delivered": set(),
    "rejected": set(),
}


def is_valid_order_transition(old_status, new_status):
    """Возвращает True, если переход статуса разрешён бизнес-логикой."""
    return new_status in ORDER_STATUS_TRANSITIONS.get(old_status, set())


def get_connection():
    """
    Создаёт соединение с базой данных.
    """
    connection = sqlite3.connect(DATABASE_PATH)

    # Позволяет обращаться к столбцам по имени,
    # а не только по номеру.
    connection.row_factory = sqlite3.Row

    # Включаем поддержку внешних ключей SQLite.
    connection.execute("PRAGMA foreign_keys = ON")

    return connection

# Один Telegram-аккаунт может иметь по одной записи на каждую роль
# (сборщик и курьер), поэтому уникальна пара (telegram_id, role).
EMPLOYEES_TABLE_COLUMNS = """
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            telegram_id INTEGER NOT NULL,
            telegram_username TEXT,

            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            phone TEXT,

            role TEXT NOT NULL,

            transport_type TEXT,

            application_status TEXT NOT NULL DEFAULT 'pending',

            is_active INTEGER NOT NULL DEFAULT 1,

            is_on_shift INTEGER NOT NULL DEFAULT 0,

            language TEXT NOT NULL DEFAULT 'ru',

            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            UNIQUE (telegram_id, role)
"""

EMPLOYEES_COPY_COLUMNS = (
    "id, telegram_id, telegram_username, first_name, last_name, phone, "
    "role, transport_type, application_status, is_active, is_on_shift, "
    "language, created_at"
)


def _employees_has_unique_telegram_id(cursor):
    """
    Проверяет, осталось ли в старой базе ограничение
    "один Telegram ID - одна запись".
    """
    cursor.execute("PRAGMA index_list(employees)")

    for index in cursor.fetchall():
        if not index["unique"]:
            continue

        cursor.execute(f'PRAGMA index_info("{index["name"]}")')

        if [row["name"] for row in cursor.fetchall()] == ["telegram_id"]:
            return True

    return False


def _migrate_employees_unique_telegram_role(cursor):
    """
    Переносит сотрудников из старой таблицы (UNIQUE telegram_id)
    в таблицу с UNIQUE (telegram_id, role). Данные и id сохраняются.
    """
    if not _employees_has_unique_telegram_id(cursor):
        return

    cursor.execute(f"CREATE TABLE employees_new ({EMPLOYEES_TABLE_COLUMNS})")
    cursor.execute(
        f"""
        INSERT INTO employees_new ({EMPLOYEES_COPY_COLUMNS})
        SELECT {EMPLOYEES_COPY_COLUMNS} FROM employees
        """
    )
    cursor.execute("DROP TABLE employees")
    cursor.execute("ALTER TABLE employees_new RENAME TO employees")


def init_database():
    """
    Создаёт таблицы базы данных,
    если они ещё не существуют.
    """

    connection = get_connection()

    cursor = connection.cursor()

    # Оба бота вызывают init_database при старте одновременно.
    # Вся схема создаётся и мигрирует одной транзакцией: второй процесс
    # дожидается первого и видит уже готовую базу.
    # PRAGMA foreign_keys нельзя менять внутри транзакции.
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("BEGIN IMMEDIATE")

    # ==========================================
    # 1. СОТРУДНИКИ
    # ==========================================

    cursor.execute(
        f"CREATE TABLE IF NOT EXISTS employees ({EMPLOYEES_TABLE_COLUMNS})"
    )

    # Проверяем структуру уже существующей базы.
    # Это нужно, потому что база могла быть создана
    # до появления telegram_username.
    cursor.execute("PRAGMA table_info(employees)")

    columns = [row["name"] for row in cursor.fetchall()]

    if "telegram_username" not in columns:
        cursor.execute(
            "ALTER TABLE employees ADD COLUMN telegram_username TEXT"
        )

    if "is_on_shift" not in columns:
        cursor.execute(
            "ALTER TABLE employees ADD COLUMN is_on_shift INTEGER NOT NULL DEFAULT 0"
        )

    if "language" not in columns:
        cursor.execute(
            "ALTER TABLE employees ADD COLUMN language TEXT NOT NULL DEFAULT 'ru'"
        )

    _migrate_employees_unique_telegram_role(cursor)

    # ==========================================
    # 2. ЗАКАЗЫ
    # ==========================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            order_number TEXT UNIQUE NOT NULL,

            client_name TEXT,
            client_phone TEXT,

            client_telegram_id INTEGER,

            delivery_address TEXT,

            client_comment TEXT,

            payment_method TEXT,
            payment_amount REAL,

            status TEXT NOT NULL DEFAULT 'new',

            picker_id INTEGER,
            courier_id INTEGER,

            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (picker_id)
                REFERENCES employees(id),

            FOREIGN KEY (courier_id)
                REFERENCES employees(id)
        )
    """)

    cursor.execute("PRAGMA table_info(orders)")
    order_columns = [row["name"] for row in cursor.fetchall()]

    if "client_telegram_id" not in order_columns:
        cursor.execute(
            "ALTER TABLE orders ADD COLUMN client_telegram_id INTEGER"
        )

    # ==========================================
    # 3. ТОВАРЫ В ЗАКАЗАХ
    # ==========================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            order_id INTEGER NOT NULL,

            product_name TEXT NOT NULL,

            quantity INTEGER NOT NULL,

            FOREIGN KEY (order_id)
                REFERENCES orders(id)
                ON DELETE CASCADE
        )
    """)

    # ==========================================
    # 4. ИСТОРИЯ ДЕЙСТВИЙ
    # ==========================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS action_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            order_id INTEGER,

            employee_id INTEGER,

            action TEXT NOT NULL,

            old_status TEXT,
            new_status TEXT,

            details TEXT,

            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (order_id)
                REFERENCES orders(id),

            FOREIGN KEY (employee_id)
                REFERENCES employees(id)
        )
    """)

    connection.commit()
    connection.close()

def get_employee_by_telegram_id(telegram_id, role=None):
    """
    Находит сотрудника по его Telegram ID.

    У одного Telegram ID может быть по записи на каждую роль,
    поэтому боты передают свою роль ("picker" или "courier").
    """

    connection = get_connection()

    try:
        cursor = connection.cursor()

        query = """
            SELECT
                id,
                telegram_id,
                telegram_username,
                first_name,
                last_name,
                phone,
                role,
                transport_type,
                application_status,
                is_active,
                is_on_shift,
                language,
                created_at
            FROM employees
            WHERE telegram_id = ?
        """
        params = [telegram_id]

        if role is not None:
            query += " AND role = ?"
            params.append(role)

        query += " ORDER BY id"

        cursor.execute(query, params)

        return cursor.fetchone()

    finally:
        connection.close()

def create_employee_application(
    telegram_id,
    telegram_username,
    first_name,
    last_name,
    phone,
    role,
    transport_type=None,
    language="ru",
):
    """
    Создаёт или обновляет заявку сотрудника.
    """

    if role not in EMPLOYEE_ROLES:
        return "invalid_role"

    if not isinstance(first_name, str) or not first_name.strip():
        return "invalid_first_name"
    if not isinstance(last_name, str) or not last_name.strip():
        return "invalid_last_name"
    if not isinstance(phone, str) or not phone.strip():
        return "invalid_phone"

    if role == "courier":
        if not isinstance(transport_type, str) or not transport_type.strip():
            return "invalid_transport"
    elif transport_type not in (None, ""):
        return "invalid_transport"

    connection = get_connection()
    language = language if language in ("ru", "tg") else "ru"

    try:
        cursor = connection.cursor()

        # Проверяем, есть ли уже такой сотрудник
        cursor.execute(
            """
            SELECT id, application_status
            FROM employees
            WHERE telegram_id = ?
              AND role = ?
            """,
            (telegram_id, role),
        )

        existing_employee = cursor.fetchone()

        # Если сотрудник уже существует
        if existing_employee:

            status = existing_employee["application_status"]

            # Заявка уже ожидает рассмотрения
            if status == "pending":
                return "already_pending"

            # Сотрудник уже одобрен
            if status == "approved":
                return "already_approved"

            # Если раньше заявку отклонили,
            # разрешаем подать её заново
            cursor.execute(
                """
                UPDATE employees
                SET
                    telegram_username = ?,
                    first_name = ?,
                    last_name = ?,
                    phone = ?,
                    role = ?,
                    transport_type = ?,
                    language = ?,
                    application_status = 'pending',
                    is_active = 1
                WHERE id = ?
                """,
                (
                    telegram_username,
                    first_name,
                    last_name,
                    phone,
                    role,
                    transport_type,
                    language,
                    existing_employee["id"],
                ),
            )

            connection.commit()

            return "recreated"

        # Если сотрудника ещё нет — создаём новую заявку
        cursor.execute(
            """
            INSERT INTO employees (
                telegram_id,
                telegram_username,
                first_name,
                last_name,
                phone,
                role,
                transport_type,
                language,
                application_status,
                is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 1)
            """,
            (
                telegram_id,
                telegram_username,
                first_name,
                last_name,
                phone,
                role,
                transport_type,
                language,
            ),
        )

        connection.commit()

        return "created"

    finally:
        connection.close()

def get_pending_applications():
    """
    Возвращает все заявки сотрудников,
    которые ожидают решения администратора.
    """

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                id,
                telegram_id,
                telegram_username,
                first_name,
                last_name,
                phone,
                role,
                transport_type,
                application_status,
                created_at
            FROM employees
            WHERE application_status = 'pending'
            ORDER BY created_at ASC
        """)

        return cursor.fetchall()

    finally:
        connection.close()


def update_application_status(employee_id, new_status):
    """
    Изменяет статус заявки сотрудника.

    Возвращает данные сотрудника после изменения.
    """

    if new_status not in {"approved", "rejected"}:
        return None

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute("""
            UPDATE employees
            SET
                application_status = ?,
                is_active = ?
            WHERE id = ?
              AND application_status = 'pending'
        """, (
            new_status,
            1 if new_status == "approved" else 0,
            employee_id,
        ))

        # rowcount показывает, сколько строк реально изменилось.
        if cursor.rowcount == 0:
            return None

        connection.commit()

        cursor.execute("""
            SELECT
                id,
                telegram_id,
                telegram_username,
                first_name,
                last_name,
                phone,
                role,
                transport_type,
                application_status,
                language
            FROM employees
            WHERE id = ?
        """, (employee_id,))

        return cursor.fetchone()

    finally:
        connection.close()


def get_employee_by_id(employee_id):
    """Возвращает сотрудника по внутреннему ID."""
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, telegram_id, telegram_username, first_name, last_name,
                   phone, role, transport_type, application_status,
                   is_active, is_on_shift, language, created_at
            FROM employees
            WHERE id = ?
            """,
            (employee_id,),
        )
        return cursor.fetchone()
    finally:
        connection.close()


def get_approved_employees():
    """Возвращает одобренных сотрудников для управления доступом."""
    connection = get_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, telegram_id, first_name, last_name, role,
                   is_active, is_on_shift
            FROM employees
            WHERE application_status = 'approved'
            ORDER BY role, last_name, first_name
            """
        )
        return cursor.fetchall()
    finally:
        connection.close()


def toggle_employee_access(employee_id):
    """Включает или отключает доступ сотрудника к рабочим функциям."""
    connection = get_connection()

    try:
        cursor = connection.cursor()
        connection.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """
            UPDATE employees
            SET
                is_active = CASE is_active WHEN 1 THEN 0 ELSE 1 END,
                is_on_shift = CASE is_active WHEN 1 THEN 0 ELSE is_on_shift END
            WHERE id = ?
              AND application_status = 'approved'
            """,
            (employee_id,),
        )

        if cursor.rowcount != 1:
            connection.rollback()
            return None

        cursor.execute(
            """
            SELECT id, first_name, last_name, role, is_active, is_on_shift
            FROM employees
            WHERE id = ?
            """,
            (employee_id,),
        )
        employee = cursor.fetchone()
        connection.commit()
        return employee
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def set_employee_access(employee_id, is_active):
    """Sets employee access to the requested state without toggling it."""
    if is_active not in (0, 1):
        return None

    connection = get_connection()

    try:
        cursor = connection.cursor()
        connection.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """
            UPDATE employees
            SET
                is_on_shift = CASE WHEN ? = 0 THEN 0 ELSE is_on_shift END,
                is_active = ?
            WHERE id = ?
              AND application_status = 'approved'
            """,
            (is_active, is_active, employee_id),
        )

        if cursor.rowcount != 1:
            connection.rollback()
            return None

        cursor.execute(
            """
            SELECT id, first_name, last_name, role, is_active, is_on_shift
            FROM employees
            WHERE id = ?
            """,
            (employee_id,),
        )
        employee = cursor.fetchone()
        connection.commit()
        return employee
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_new_orders():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            order_number,
            client_name,
            client_phone,
            client_telegram_id,
            delivery_address,
            client_comment,
            payment_method,
            payment_amount,
            status,
            created_at
        FROM orders
        WHERE status = 'new'
        ORDER BY created_at ASC
        """
    )

    orders = cursor.fetchall()
    connection.close()

    return orders


def get_on_shift_pickers():
    """Возвращает Telegram ID сборщиков, которым можно слать заказы."""
    connection = get_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, telegram_id, language
            FROM employees
            WHERE role = 'picker'
              AND application_status = 'approved'
              AND is_active = 1
              AND is_on_shift = 1
            """
        )
        return cursor.fetchall()
    finally:
        connection.close()


def get_unnotified_new_orders(employee_id):
    """Возвращает новые заказы, ещё не отправленные сборщику."""
    connection = get_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                o.id,
                o.order_number,
                o.client_name,
                o.client_phone,
                o.delivery_address,
                o.client_comment,
                o.payment_method,
                o.payment_amount,
                o.created_at
            FROM orders o
            WHERE o.status = 'new'
              AND NOT EXISTS (
                  SELECT 1
                  FROM action_log al
                  WHERE al.order_id = o.id
                    AND al.employee_id = ?
                    AND al.action = 'new_order_notification'
              )
            ORDER BY o.created_at ASC
            """,
            (employee_id,),
        )
        return cursor.fetchall()
    finally:
        connection.close()


def log_new_order_notification(order_id, employee_id):
    """Фиксирует отправку нового заказа конкретному сборщику."""
    connection = get_connection()

    try:
        cursor = connection.cursor()
        connection.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """
            INSERT INTO action_log (
                order_id, employee_id, action,
                old_status, new_status, details
            )
            SELECT ?, ?, 'new_order_notification', 'new', 'new',
                   'Новый заказ отправлен сборщику'
            WHERE NOT EXISTS (
                SELECT 1
                FROM action_log
                WHERE order_id = ?
                  AND employee_id = ?
                  AND action = 'new_order_notification'
            )
            """,
            (order_id, employee_id, order_id, employee_id),
        )
        connection.commit()
        return cursor.rowcount == 1
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_order_by_id(order_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            order_number,
            client_name,
            client_phone,
            client_telegram_id,
            delivery_address,
            client_comment,
            payment_method,
            payment_amount,
            status,
            picker_id,
            courier_id,
            created_at,
            updated_at
        FROM orders
        WHERE id = ?
        """,
        (order_id,),
    )

    order = cursor.fetchone()
    connection.close()

    return order


def get_order_items(order_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            product_name,
            quantity
        FROM order_items
        WHERE order_id = ?
        ORDER BY id ASC
        """,
        (order_id,),
    )

    items = cursor.fetchall()
    connection.close()

    return items


def _validate_employee_for_order_action(cursor, employee_id, required_role):
    cursor.execute(
        """
        SELECT role, application_status, is_active, is_on_shift
        FROM employees
        WHERE id = ?
        """,
        (employee_id,),
    )
    employee = cursor.fetchone()

    if employee is None or employee["application_status"] != "approved":
        return "not_approved"
    if employee["is_active"] != 1:
        return "inactive"
    if employee["is_on_shift"] != 1:
        return "off_shift"
    if employee["role"] != required_role:
        return "wrong_role"
    return None


def start_order_assembly(order_id, employee_id):
    connection = get_connection()

    try:
        cursor = connection.cursor()

        # Начинаем транзакцию.
        # Транзакция — это группа операций,
        # которая выполняется как одно целое.
        connection.execute("BEGIN IMMEDIATE")

        authorization_error = _validate_employee_for_order_action(
            cursor,
            employee_id,
            "picker",
        )
        if authorization_error:
            connection.rollback()
            return {"success": False, "reason": authorization_error}

        # Проверяем, что заказ всё ещё новый
        cursor.execute(
            """
            SELECT id, order_number, status
            FROM orders
            WHERE id = ?
            """,
            (order_id,),
        )

        order = cursor.fetchone()

        if order is None:
            connection.rollback()
            return {
                "success": False,
                "reason": "not_found",
            }

        # Заказ уже кто-то взял
        if not is_valid_order_transition(order["status"], "assembling"):
            connection.rollback()
            return {
                "success": False,
                "reason": "already_taken",
                "status": order["status"],
            }

        # Назначаем сборщика и меняем статус
        cursor.execute(
            """
            UPDATE orders
            SET
                status = 'assembling',
                picker_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status = 'new'
            """,
            (
                employee_id,
                order_id,
            ),
        )

        # Проверяем, действительно ли UPDATE изменил строку
        if cursor.rowcount != 1:
            connection.rollback()
            return {
                "success": False,
                "reason": "already_taken",
            }

        # Записываем действие в историю
        cursor.execute(
            """
            INSERT INTO action_log (
                order_id,
                employee_id,
                action,
                old_status,
                new_status,
                details
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                employee_id,
                "start_assembly",
                "new",
                "assembling",
                "Сборщик начал сборку заказа",
            ),
        )

        connection.commit()

        return {
            "success": True,
            "order_number": order["order_number"],
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

def complete_order_assembly(order_id, employee_id):
    connection = get_connection()

    try:
        cursor = connection.cursor()

        connection.execute("BEGIN IMMEDIATE")

        authorization_error = _validate_employee_for_order_action(
            cursor,
            employee_id,
            "picker",
        )
        if authorization_error:
            connection.rollback()
            return {"success": False, "reason": authorization_error}

        # Получаем заказ
        cursor.execute(
            """
            SELECT
                id,
                order_number,
                status,
                picker_id
            FROM orders
            WHERE id = ?
            """,
            (order_id,),
        )

        order = cursor.fetchone()

        if order is None:
            connection.rollback()

            return {
                "success": False,
                "reason": "not_found",
            }

        # Проверяем статус
        if not is_valid_order_transition(order["status"], "awaiting_courier"):
            connection.rollback()

            return {
                "success": False,
                "reason": "wrong_status",
                "status": order["status"],
            }

        # Проверяем, что этот заказ действительно
        # собирает именно этот сотрудник
        if order["picker_id"] != employee_id:
            connection.rollback()

            return {
                "success": False,
                "reason": "not_picker",
            }

        # Меняем статус
        cursor.execute(
            """
            UPDATE orders
            SET
                status = 'awaiting_courier',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status = 'assembling'
              AND picker_id = ?
            """,
            (
                order_id,
                employee_id,
            ),
        )

        if cursor.rowcount != 1:
            connection.rollback()

            return {
                "success": False,
                "reason": "update_failed",
            }

        # Записываем действие в историю
        cursor.execute(
            """
            INSERT INTO action_log (
                order_id,
                employee_id,
                action,
                old_status,
                new_status,
                details
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                employee_id,
                "complete_assembly",
                "assembling",
                "awaiting_courier",
                "Сборщик завершил сборку заказа",
            ),
        )

        connection.commit()

        return {
            "success": True,
            "order_number": order["order_number"],
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def mark_order_missing_item(order_id, employee_id, item_name):
    """
    Сборщик отмечает, что товара нет в наличии.
    """
    if not isinstance(item_name, str) or not item_name.strip():
        return {"success": False, "reason": "invalid_item_name"}

    connection = get_connection()

    try:
        cursor = connection.cursor()
        connection.execute("BEGIN IMMEDIATE")

        authorization_error = _validate_employee_for_order_action(
            cursor,
            employee_id,
            "picker",
        )
        if authorization_error:
            connection.rollback()
            return {"success": False, "reason": authorization_error}

        cursor.execute(
            """
            SELECT id, order_number, status, picker_id
            FROM orders
            WHERE id = ?
            """,
            (order_id,),
        )

        order = cursor.fetchone()

        if order is None:
            connection.rollback()
            return {"success": False, "reason": "not_found"}

        if order["status"] != "assembling":
            connection.rollback()
            return {
                "success": False,
                "reason": "wrong_status",
                "status": order["status"],
            }

        if order["picker_id"] != employee_id:
            connection.rollback()
            return {"success": False, "reason": "not_picker"}

        cursor.execute(
            """
            UPDATE orders
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status = 'assembling'
              AND picker_id = ?
            """,
            (order_id, employee_id),
        )

        if cursor.rowcount != 1:
            connection.rollback()
            return {"success": False, "reason": "update_failed"}

        cursor.execute(
            """
            INSERT INTO action_log (
                order_id,
                employee_id,
                action,
                old_status,
                new_status,
                details
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                employee_id,
                "missing_item",
                "assembling",
                "assembling",
                f"Сборщик отметил отсутствие позиции: {item_name}",
            ),
        )

        connection.commit()
        return {"success": True, "order_number": order["order_number"]}

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def reset_test_employee(telegram_id):
    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            DELETE FROM employees
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )

        deleted_count = cursor.rowcount

        connection.commit()

        return deleted_count

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

def get_order_timeouts(timeout_minutes=15):
    """
    Возвращает заказы, которые давно не были взяты в работу.
    """
    if not isinstance(timeout_minutes, int) or timeout_minutes <= 0:
        return []

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                o.id,
                o.order_number,
                o.client_name,
                o.delivery_address,
                o.status,
                o.updated_at,
                CASE
                    WHEN o.status = 'new' THEN 'Новый'
                    WHEN o.status = 'assembling' THEN 'Собирается'
                    WHEN o.status = 'awaiting_courier' THEN 'Ожидает курьера'
                    ELSE o.status
                END AS status_label
            FROM orders o
            WHERE o.status IN ('new', 'assembling', 'awaiting_courier')
              AND o.updated_at <= datetime('now', '-' || ? || ' minutes')
              AND NOT EXISTS (
                  SELECT 1
                  FROM action_log al
                  WHERE al.order_id = o.id
                    AND al.action = 'timeout_alert'
                    AND al.new_status = o.status
              )
            ORDER BY o.updated_at ASC
            """,
            (str(timeout_minutes),),
        )
        return cursor.fetchall()
    finally:
        connection.close()


def get_employee_stats(employee_id=None):
    """
    Возвращает краткую статистику по сотрудникам:
    число сборок, число доставок и среднее время доставки.
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                id,
                first_name,
                last_name,
                role,
                application_status,
                is_active
            FROM employees
            WHERE application_status = 'approved'
              AND (? IS NULL OR id = ?)
            ORDER BY role, last_name, first_name
            """,
            (employee_id, employee_id),
        )

        employees = cursor.fetchall()
        stats = []

        for employee in employees:
            employee_id = employee["id"]

            assembly_count = cursor.execute(
                """
                SELECT COUNT(DISTINCT order_id)
                FROM action_log
                WHERE employee_id = ?
                  AND action = 'complete_assembly'
                """,
                (employee_id,),
            ).fetchone()[0] or 0

            delivery_count = cursor.execute(
                """
                SELECT COUNT(*)
                FROM orders
                WHERE courier_id = ?
                  AND status = 'delivered'
                """,
                (employee_id,),
            ).fetchone()[0] or 0

            avg_delivery_minutes = cursor.execute(
                """
                SELECT AVG(
                                        (julianday(delivered.created_at) - julianday(picked.created_at))
                                        * 24 * 60
                )
                                FROM orders o
                                JOIN action_log picked
                                    ON picked.order_id = o.id
                                 AND picked.employee_id = o.courier_id
                                 AND picked.action = 'pickup_order'
                                JOIN action_log delivered
                                    ON delivered.order_id = o.id
                                 AND delivered.employee_id = o.courier_id
                                 AND delivered.action = 'deliver_order'
                                WHERE o.courier_id = ?
                                    AND o.status = 'delivered'
                """,
                (employee_id,),
            ).fetchone()[0]

            stats.append({
                "id": employee_id,
                "name": f"{employee['first_name']} {employee['last_name']}",
                "role": employee["role"],
                "assembly_count": assembly_count,
                "delivery_count": delivery_count,
                "avg_delivery_minutes": (
                    round(float(avg_delivery_minutes), 1)
                    if avg_delivery_minutes is not None
                    else None
                ),
            })

        return stats

    finally:
        connection.close()


def get_admin_stats_summary():
    """Возвращает общую статистику для административной панели."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        total_orders = cursor.execute(
            "SELECT COUNT(*) FROM orders"
        ).fetchone()[0] or 0
        delivered_orders = cursor.execute(
            "SELECT COUNT(*) FROM orders WHERE status = 'delivered'"
        ).fetchone()[0] or 0
        rejected_orders = cursor.execute(
            "SELECT COUNT(*) FROM orders WHERE status = 'rejected'"
        ).fetchone()[0] or 0
        active_employees = cursor.execute(
            """
            SELECT COUNT(*) FROM employees
            WHERE application_status = 'approved' AND is_active = 1
            """
        ).fetchone()[0] or 0
        on_shift_employees = cursor.execute(
            """
            SELECT COUNT(*) FROM employees
            WHERE application_status = 'approved'
              AND is_active = 1
              AND is_on_shift = 1
            """
        ).fetchone()[0] or 0
        avg_delivery_minutes = cursor.execute(
            """
            SELECT AVG(
                (julianday(delivered.created_at) - julianday(picked.created_at)) * 24 * 60
            )
            FROM orders o
            JOIN action_log picked
                ON picked.order_id = o.id
               AND picked.employee_id = o.courier_id
               AND picked.action = 'pickup_order'
            JOIN action_log delivered
                ON delivered.order_id = o.id
               AND delivered.employee_id = o.courier_id
               AND delivered.action = 'deliver_order'
            WHERE o.status = 'delivered'
            """
        ).fetchone()[0]

        return {
            "total_orders": total_orders,
            "delivered_orders": delivered_orders,
            "rejected_orders": rejected_orders,
            "active_employees": active_employees,
            "on_shift_employees": on_shift_employees,
            "avg_delivery_minutes": (
                round(float(avg_delivery_minutes), 1)
                if avg_delivery_minutes is not None
                else None
            ),
        }
    finally:
        connection.close()


def get_employee_shift_report(employee_id, report_date=None):
    """
    Возвращает сводку действий сотрудника за одну смену.
    """
    connection = get_connection()
    cursor = connection.cursor()
    report_date = report_date or "now"

    try:
        cursor.execute(
            """
            SELECT
                COUNT(CASE WHEN action = 'complete_assembly' THEN 1 END) AS assembly_count,
                COUNT(CASE WHEN action = 'deliver_order' THEN 1 END) AS delivery_count,
                COUNT(CASE WHEN action = 'reject_order' THEN 1 END) AS rejection_count
            FROM action_log
            WHERE employee_id = ?
              AND date(created_at) = date(?, 'localtime')
            """,
            (employee_id, report_date),
        )
        summary = cursor.fetchone()

        cursor.execute(
            """
            SELECT
                al.action,
                al.details,
                al.created_at,
                o.order_number
            FROM action_log al
            LEFT JOIN orders o ON o.id = al.order_id
            WHERE al.employee_id = ?
              AND date(al.created_at) = date(?, 'localtime')
            ORDER BY al.created_at DESC
            """,
            (employee_id, report_date),
        )

        return {
            "assembly_count": summary["assembly_count"] or 0,
            "delivery_count": summary["delivery_count"] or 0,
            "rejection_count": summary["rejection_count"] or 0,
            "actions": cursor.fetchall(),
        }
    finally:
        connection.close()


def toggle_employee_shift(employee_id):
    """
    Переключает статус смены сотрудника и возвращает новое значение.
    """
    connection = get_connection()

    try:
        cursor = connection.cursor()
        connection.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """
            UPDATE employees
            SET is_on_shift = CASE is_on_shift WHEN 1 THEN 0 ELSE 1 END
            WHERE id = ?
              AND application_status = 'approved'
              AND is_active = 1
            """,
            (employee_id,),
        )

        if cursor.rowcount != 1:
            connection.rollback()
            return None

        cursor.execute(
            "SELECT is_on_shift FROM employees WHERE id = ?",
            (employee_id,),
        )
        is_on_shift = cursor.fetchone()["is_on_shift"]
        connection.commit()
        return bool(is_on_shift)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def toggle_employee_language(employee_id):
    """Переключает язык интерфейса сотрудника между русским и таджикским."""
    connection = get_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE employees
            SET language = CASE language WHEN 'ru' THEN 'tg' ELSE 'ru' END
            WHERE id = ?
              AND application_status = 'approved'
              AND is_active = 1
            """,
            (employee_id,),
        )
        if cursor.rowcount != 1:
            return None

        cursor.execute(
            "SELECT language FROM employees WHERE id = ?",
            (employee_id,),
        )
        language = cursor.fetchone()["language"]
        connection.commit()
        return language
    finally:
        connection.close()


def log_order_timeout_alert(order_id, status, timeout_minutes):
    """
    Записывает уведомление администратору о том,
    что заказ слишком долго не взят в работу.
    """
    if status not in ORDER_STATUSES:
        return False
    if not isinstance(timeout_minutes, int) or timeout_minutes <= 0:
        return False

    connection = get_connection()

    try:
        cursor = connection.cursor()
        connection.execute("BEGIN IMMEDIATE")

        cursor.execute(
            """
            INSERT INTO action_log (
                order_id,
                employee_id,
                action,
                old_status,
                new_status,
                details
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                None,
                "timeout_alert",
                status,
                status,
                (
                    "Автоуведомление: заказ не был взят в работу "
                    f"в течение {timeout_minutes} минут."
                ),
            ),
        )

        connection.commit()
        return True

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def get_orders_for_courier(employee_id):
    """
    Возвращает заказы, которые курьер может видеть:
    - awaiting_courier — доступны для получения;
    - in_delivery — только если уже закреплены за этим курьером.
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                id,
                order_number,
                client_name,
                client_phone,
                delivery_address,
                client_comment,
                payment_method,
                payment_amount,
                status,
                courier_id,
                created_at
            FROM orders
            WHERE status = 'awaiting_courier'
               OR (status = 'in_delivery' AND courier_id = ?)
            ORDER BY
                CASE WHEN status = 'in_delivery' THEN 0 ELSE 1 END,
                created_at ASC
            """,
            (employee_id,),
        )
        return cursor.fetchall()
    finally:
        connection.close()


def get_orders_waiting_for_courier():
    """Совместимость со старым кодом: только заказы, ожидающие курьера."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                id,
                order_number,
                client_name,
                client_phone,
                delivery_address,
                client_comment,
                payment_method,
                payment_amount,
                status,
                created_at
            FROM orders
            WHERE status = 'awaiting_courier'
            ORDER BY created_at ASC
            """
        )
        return cursor.fetchall()
    finally:
        connection.close()


def get_on_shift_couriers():
    connection = get_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, telegram_id, language
            FROM employees
            WHERE role = 'courier'
              AND application_status = 'approved'
              AND is_active = 1
              AND is_on_shift = 1
            """
        )
        return cursor.fetchall()
    finally:
        connection.close()


def get_unnotified_courier_orders(employee_id):
    connection = get_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                o.id,
                o.order_number,
                o.client_name,
                o.client_phone,
                o.delivery_address,
                o.client_comment,
                o.payment_method,
                o.payment_amount
            FROM orders o
            WHERE o.status = 'awaiting_courier'
              AND NOT EXISTS (
                  SELECT 1
                  FROM action_log al
                  WHERE al.order_id = o.id
                    AND al.employee_id = ?
                    AND al.action = 'courier_order_notification'
              )
            ORDER BY o.updated_at ASC
            """,
            (employee_id,),
        )
        return cursor.fetchall()
    finally:
        connection.close()


def log_courier_order_notification(order_id, employee_id):
    connection = get_connection()

    try:
        cursor = connection.cursor()
        connection.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """
            INSERT INTO action_log (
                order_id, employee_id, action,
                old_status, new_status, details
            )
            SELECT ?, ?, 'courier_order_notification',
                   'awaiting_courier', 'awaiting_courier',
                   'Заказ отправлен курьеру'
            WHERE EXISTS (
                SELECT 1 FROM orders
                WHERE id = ? AND status = 'awaiting_courier'
            )
              AND NOT EXISTS (
                  SELECT 1 FROM action_log
                  WHERE order_id = ?
                    AND employee_id = ?
                    AND action = 'courier_order_notification'
              )
            """,
            (order_id, employee_id, order_id, order_id, employee_id),
        )
        connection.commit()
        return cursor.rowcount == 1
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_pending_missing_item_alerts():
    connection = get_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                source.id AS event_id,
                source.order_id,
                source.details,
                source.created_at,
                o.order_number,
                employee.first_name || ' ' || employee.last_name AS picker_name
            FROM action_log source
            JOIN orders o ON o.id = source.order_id
            LEFT JOIN employees employee ON employee.id = source.employee_id
            WHERE source.action = 'missing_item'
              AND NOT EXISTS (
                  SELECT 1
                  FROM action_log sent
                  WHERE sent.action = 'missing_item_admin_notification'
                    AND sent.details = 'source_action_id:' || source.id
              )
            ORDER BY source.created_at ASC
            """
        )
        return cursor.fetchall()
    finally:
        connection.close()


def log_missing_item_alert_sent(event_id, order_id):
    connection = get_connection()

    try:
        cursor = connection.cursor()
        connection.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """
            INSERT INTO action_log (
                order_id, employee_id, action,
                old_status, new_status, details
            )
            SELECT ?, NULL, 'missing_item_admin_notification',
                   'assembling', 'assembling', ?
            WHERE NOT EXISTS (
                SELECT 1 FROM action_log
                WHERE action = 'missing_item_admin_notification'
                  AND details = ?
            )
            """,
            (
                order_id,
                f"source_action_id:{event_id}",
                f"source_action_id:{event_id}",
            ),
        )
        connection.commit()
        return cursor.rowcount == 1
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_admin_orders_feed():
    """
    Возвращает ленту заказов для админки: статус, клиент, адрес, ответственные.
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                o.id,
                o.order_number,
                o.client_name,
                o.client_phone,
                o.delivery_address,
                o.payment_method,
                o.payment_amount,
                o.status,
                o.created_at,
                o.updated_at,
                CASE
                    WHEN o.status IN ('delivered', 'rejected') THEN ROUND(
                        (julianday(o.updated_at) - julianday(o.created_at)) * 24 * 60,
                        1
                    )
                    ELSE NULL
                END AS total_delivery_minutes,
                picker.first_name || ' ' || picker.last_name AS picker_name,
                courier.first_name || ' ' || courier.last_name AS courier_name
            FROM orders o
            LEFT JOIN employees picker
                ON o.picker_id = picker.id
            LEFT JOIN employees courier
                ON o.courier_id = courier.id
            ORDER BY o.updated_at DESC
            """
        )
        return cursor.fetchall()
    finally:
        connection.close()



def get_admin_audit_log(limit=20):
    """Возвращает последние события action_log для административного аудита."""
    if not isinstance(limit, int) or limit <= 0:
        limit = 20
    limit = min(limit, 100)

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                al.id,
                al.employee_id,
                al.action,
                al.old_status,
                al.new_status,
                al.details,
                al.created_at,
                al.order_id,
                o.order_number,
                al.employee_id,
                employee.first_name || ' ' || employee.last_name AS employee_name,
                employee.role AS employee_role
            FROM action_log al
            LEFT JOIN orders o
                ON o.id = al.order_id
            LEFT JOIN employees employee
                ON employee.id = al.employee_id
            ORDER BY al.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return cursor.fetchall()
    finally:
        connection.close()


def get_order_history(order_id):
    """
    Возвращает историю статусов заказа для админки.
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                al.id,
                al.employee_id,
                al.action,
                al.old_status,
                al.new_status,
                al.details,
                al.created_at,
                employee.first_name || ' ' || employee.last_name AS employee_name
            FROM action_log al
            LEFT JOIN employees employee
                ON al.employee_id = employee.id
            WHERE al.order_id = ?
            ORDER BY al.created_at ASC
            """,
            (order_id,),
        )
        return cursor.fetchall()
    finally:
        connection.close()


def pickup_order(order_id, employee_id):
    """
    Курьер забирает собранный заказ.
    Статус меняется с 'awaiting_courier' на 'in_delivery'.
    """
    connection = get_connection()

    try:
        cursor = connection.cursor()

        connection.execute("BEGIN IMMEDIATE")

        authorization_error = _validate_employee_for_order_action(
            cursor,
            employee_id,
            "courier",
        )
        if authorization_error:
            connection.rollback()
            return {"success": False, "reason": authorization_error}

        # Получаем заказ
        cursor.execute(
            """
            SELECT id, order_number, status
            FROM orders
            WHERE id = ?
            """,
            (order_id,),
        )

        order = cursor.fetchone()

        if order is None:
            connection.rollback()
            return {
                "success": False,
                "reason": "not_found",
            }

        # Заказ должен ждать курьера
        if not is_valid_order_transition(order["status"], "in_delivery"):
            connection.rollback()
            return {
                "success": False,
                "reason": "already_taken",
                "status": order["status"],
            }

        # Меняем статус и закрепляем курьера
        cursor.execute(
            """
            UPDATE orders
            SET
                status = 'in_delivery',
                courier_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status = 'awaiting_courier'
            """,
            (
                employee_id,
                order_id,
            ),
        )

        if cursor.rowcount != 1:
            connection.rollback()
            return {
                "success": False,
                "reason": "already_taken",
            }

        # Записываем действие в историю
        cursor.execute(
            """
            INSERT INTO action_log (
                order_id,
                employee_id,
                action,
                old_status,
                new_status,
                details
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                employee_id,
                "pickup_order",
                "awaiting_courier",
                "in_delivery",
                "Курьер забрал заказ",
            ),
        )

        connection.commit()

        return {
            "success": True,
            "order_number": order["order_number"],
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

def deliver_order(order_id, employee_id):
    """
    Курьер отмечает, что заказ доставлен клиенту.
    Статус меняется с 'in_delivery' на 'delivered'.
    """
    connection = get_connection()

    try:
        cursor = connection.cursor()

        connection.execute("BEGIN IMMEDIATE")

        authorization_error = _validate_employee_for_order_action(
            cursor,
            employee_id,
            "courier",
        )
        if authorization_error:
            connection.rollback()
            return {"success": False, "reason": authorization_error}

        cursor.execute(
            """
            SELECT id, order_number, status, courier_id
            FROM orders
            WHERE id = ?
            """,
            (order_id,),
        )

        order = cursor.fetchone()

        if order is None:
            connection.rollback()
            return {"success": False, "reason": "not_found"}

        if not is_valid_order_transition(order["status"], "delivered"):
            connection.rollback()
            return {
                "success": False,
                "reason": "wrong_status",
                "status": order["status"],
            }

        if order["courier_id"] != employee_id:
            connection.rollback()
            return {"success": False, "reason": "not_courier"}

        cursor.execute(
            """
            UPDATE orders
            SET
                status = 'delivered',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status = 'in_delivery'
              AND courier_id = ?
            """,
            (order_id, employee_id),
        )

        if cursor.rowcount != 1:
            connection.rollback()
            return {"success": False, "reason": "update_failed"}

        cursor.execute(
            """
            INSERT INTO action_log (
                order_id, employee_id, action,
                old_status, new_status, details
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                employee_id,
                "deliver_order",
                "in_delivery",
                "delivered",
                "Курьер доставил заказ клиенту",
            ),
        )

        connection.commit()

        return {
            "success": True,
            "order_number": order["order_number"],
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def reject_order(order_id, employee_id, reason):
    """
    Курьер отклоняет заказ и указывает причину.
    """
    if not isinstance(reason, str) or not reason.strip():
        return {"success": False, "reason": "invalid_reason"}

    connection = get_connection()

    try:
        cursor = connection.cursor()
        connection.execute("BEGIN IMMEDIATE")

        authorization_error = _validate_employee_for_order_action(
            cursor,
            employee_id,
            "courier",
        )
        if authorization_error:
            connection.rollback()
            return {"success": False, "reason": authorization_error}

        cursor.execute(
            """
            SELECT id, order_number, status, courier_id
            FROM orders
            WHERE id = ?
            """,
            (order_id,),
        )

        order = cursor.fetchone()

        if order is None:
            connection.rollback()
            return {"success": False, "reason": "not_found"}

        if not is_valid_order_transition(order["status"], "rejected"):
            connection.rollback()
            return {
                "success": False,
                "reason": "wrong_status",
                "status": order["status"],
            }

        if order["courier_id"] != employee_id:
            connection.rollback()
            return {"success": False, "reason": "not_courier"}

        cursor.execute(
            """
            UPDATE orders
            SET
                status = 'rejected',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status = 'in_delivery'
              AND courier_id = ?
            """,
            (order_id, employee_id),
        )

        if cursor.rowcount != 1:
            connection.rollback()
            return {"success": False, "reason": "update_failed"}

        cursor.execute(
            """
            INSERT INTO action_log (
                order_id,
                employee_id,
                action,
                old_status,
                new_status,
                details
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                employee_id,
                "reject_order",
                "in_delivery",
                "rejected",
                f"Курьер отклонил заказ. Причина: {reason}",
            ),
        )

        connection.commit()
        return {
            "success": True,
            "order_number": order["order_number"],
            "reason": reason,
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def get_rejected_orders():
    """Возвращает отклонённые заказы без решения по товару."""
    connection = get_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                o.id,
                o.order_number,
                o.client_name,
                o.updated_at,
                (
                    SELECT al.details
                    FROM action_log al
                    WHERE al.order_id = o.id
                      AND al.action = 'reject_order'
                    ORDER BY al.created_at DESC
                    LIMIT 1
                ) AS rejection_details
            FROM orders o
            WHERE o.status = 'rejected'
              AND NOT EXISTS (
                  SELECT 1
                  FROM action_log al
                  WHERE al.order_id = o.id
                    AND al.action IN ('return_to_stock', 'write_off')
              )
            ORDER BY o.updated_at ASC
            """
        )
        return cursor.fetchall()
    finally:
        connection.close()


def resolve_rejected_order(order_id, resolution, admin_telegram_id=None):
    """Фиксирует решение админа по товарам отклонённого заказа."""
    if resolution not in ("return_to_stock", "write_off"):
        return {"success": False, "reason": "invalid_resolution"}

    connection = get_connection()

    try:
        cursor = connection.cursor()
        connection.execute("BEGIN IMMEDIATE")
        cursor.execute(
            "SELECT id, order_number, status FROM orders WHERE id = ?",
            (order_id,),
        )
        order = cursor.fetchone()

        if order is None:
            connection.rollback()
            return {"success": False, "reason": "not_found"}

        if order["status"] != "rejected":
            connection.rollback()
            return {"success": False, "reason": "wrong_status"}

        already_resolved = cursor.execute(
            """
            SELECT 1
            FROM action_log
            WHERE order_id = ?
              AND action IN ('return_to_stock', 'write_off')
            """,
            (order_id,),
        ).fetchone()
        if already_resolved:
            connection.rollback()
            return {"success": False, "reason": "already_resolved"}

        details = (
            "Администратор решил вернуть товар на склад"
            if resolution == "return_to_stock"
            else "Администратор решил списать товар"
        )
        if admin_telegram_id is not None:
            details += f" (Telegram ID администратора: {admin_telegram_id})"
        cursor.execute(
            """
            INSERT INTO action_log (
                order_id, employee_id, action,
                old_status, new_status, details
            )
            VALUES (?, NULL, ?, 'rejected', 'rejected', ?)
            """,
            (order_id, resolution, details),
        )
        connection.commit()
        return {
            "success": True,
            "order_number": order["order_number"],
            "resolution": resolution,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    init_database()

    print("База данных успешно создана.")
    print(f"Файл базы данных: {DATABASE_PATH}")