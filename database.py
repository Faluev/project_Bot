import sqlite3
from pathlib import Path


# Путь к файлу базы данных
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "delivery.db"


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

def init_database():
    """
    Создаёт таблицы базы данных,
    если они ещё не существуют.
    """

    connection = get_connection()

    cursor = connection.cursor()

    # ==========================================
    # 1. СОТРУДНИКИ
    # ==========================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            telegram_id INTEGER UNIQUE NOT NULL,
            telegram_username TEXT,

            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            phone TEXT,

            role TEXT NOT NULL,

            transport_type TEXT,

            application_status TEXT NOT NULL DEFAULT 'pending',

            is_active INTEGER NOT NULL DEFAULT 1,

            is_on_shift INTEGER NOT NULL DEFAULT 0,

            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

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

    # ==========================================
    # 2. ЗАКАЗЫ
    # ==========================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            order_number TEXT UNIQUE NOT NULL,

            client_name TEXT,
            client_phone TEXT,

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

def get_employee_by_telegram_id(telegram_id):
    """
    Находит сотрудника по его Telegram ID.
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
                is_active,
                is_on_shift,
                created_at
            FROM employees
            WHERE telegram_id = ?
        """, (telegram_id,))

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
):
    """
    Создаёт или обновляет заявку сотрудника.
    """

    connection = get_connection()

    try:
        cursor = connection.cursor()

        # Проверяем, есть ли уже такой сотрудник
        cursor.execute(
            """
            SELECT id, application_status
            FROM employees
            WHERE telegram_id = ?
            """,
            (telegram_id,),
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
                    application_status = 'pending',
                    is_active = 1
                WHERE telegram_id = ?
                """,
                (
                    telegram_username,
                    first_name,
                    last_name,
                    phone,
                    role,
                    telegram_id,
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
                application_status,
                is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending', 1)
            """,
            (
                telegram_id,
                telegram_username,
                first_name,
                last_name,
                phone,
                role,
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
                application_status
            FROM employees
            WHERE id = ?
        """, (employee_id,))

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

def start_order_assembly(order_id, employee_id):
    connection = get_connection()

    try:
        cursor = connection.cursor()

        # Начинаем транзакцию.
        # Транзакция — это группа операций,
        # которая выполняется как одно целое.
        connection.execute("BEGIN IMMEDIATE")

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
        if order["status"] != "new":
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
        if order["status"] != "assembling":
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


def mark_order_missing_item(order_id, employee_id):
    """
    Сборщик отмечает, что товара нет в наличии.
    """
    connection = get_connection()

    try:
        cursor = connection.cursor()
        connection.execute("BEGIN IMMEDIATE")

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
            SET
                status = 'missing_item',
                updated_at = CURRENT_TIMESTAMP
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
                "missing_item",
                "Сборщик отметил отсутствие товара в заказе",
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


def get_employee_stats():
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
            ORDER BY role, last_name, first_name
            """
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
                    (strftime('%s', updated_at) - strftime('%s', created_at)) / 60.0
                )
                FROM orders
                WHERE courier_id = ?
                  AND status = 'delivered'
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


def log_order_timeout_alert(order_id, status, timeout_minutes):
    """
    Записывает уведомление администратору о том,
    что заказ слишком долго не взят в работу.
    """
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


def get_orders_waiting_for_courier():
    connection = get_connection()
    cursor = connection.cursor()

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

    orders = cursor.fetchall()

    connection.close()

    return orders


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
        if order["status"] != "awaiting_courier":
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

        if order["status"] != "in_delivery":
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
    connection = get_connection()

    try:
        cursor = connection.cursor()
        connection.execute("BEGIN IMMEDIATE")

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

        if order["status"] != "in_delivery":
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


if __name__ == "__main__":
    init_database()

    print("База данных успешно создана.")
    print(f"Файл базы данных: {DATABASE_PATH}")