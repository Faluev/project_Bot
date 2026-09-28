import asyncio
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import ReplyKeyboardRemove
from telegram.ext import ConversationHandler

import database
from handlers import registration
from handlers.courier_orders import build_client_contact_message
from handlers.registration import build_admin_contact_message
from handlers.retry import send_message_with_retry
from handlers.i18n import MESSAGES


class OrderTimeoutTests(unittest.TestCase):
    def test_translation_catalogs_have_matching_keys(self):
        self.assertEqual(set(MESSAGES["ru"]), set(MESSAGES["tg"]))

    def test_get_order_timeouts_detects_stale_orders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/test_delivery.db"
            database.DATABASE_PATH = db_path

            database.init_database()

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row

            conn.execute(
                """
                INSERT INTO employees (
                    telegram_id, first_name, last_name, phone, role, application_status, is_active
                ) VALUES (?, ?, ?, ?, ?, 'approved', 1)
                """,
                (1001, "Тест", "Сборщик", "+70000000001", "picker"),
            )

            stale_time = (
                datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=30)
            ).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, delivery_address, status, picker_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("TEST-100", "Иван", "Улица Тестовая 1", "new", 1, stale_time, stale_time),
            )
            conn.commit()
            conn.close()

            result = database.get_order_timeouts(15)

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["order_number"], "TEST-100")
            self.assertEqual(result[0]["status"], "new")

    def test_build_admin_contact_message_includes_employee_details(self):
        employee = {
            "first_name": "Иван",
            "last_name": "Иванов",
            "role": "picker",
            "telegram_username": "ivan",
        }

        message = build_admin_contact_message(employee, "Нет товара")

        self.assertIn("Иван Иванов", message)
        self.assertIn("Сборщик", message)
        self.assertIn("@ivan", message)
        self.assertIn("Нет товара", message)

    def test_build_client_contact_message_includes_order_details(self):
        order = {
            "order_number": "TEST-100",
            "client_name": "Ольга",
            "delivery_address": "Душанбе, ул. Ленина 10",
            "payment_method": "cash",
            "payment_amount": 250.0,
        }

        message = build_client_contact_message(order, "Курьер уже в пути.")

        self.assertIn("TEST-100", message)
        self.assertIn("Ольга", message)
        self.assertIn("Душанбе, ул. Ленина 10", message)
        self.assertIn("Курьер уже в пути.", message)

    def test_get_employee_stats_returns_counts_and_average_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/test_delivery.db"
            database.DATABASE_PATH = db_path
            database.init_database()

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute(
                """
                INSERT INTO employees (telegram_id, first_name, last_name, phone, role, application_status, is_active)
                VALUES (?, ?, ?, ?, ?, 'approved', 1)
                """,
                (2001, "Али", "Ибрагимов", "+70000000002", "picker"),
            )
            conn.execute(
                """
                INSERT INTO employees (telegram_id, first_name, last_name, phone, role, application_status, is_active)
                VALUES (?, ?, ?, ?, ?, 'approved', 1)
                """,
                (2002, "Мирзо", "Каримов", "+70000000003", "courier"),
            )
            conn.execute(
                """
                INSERT INTO orders (order_number, client_name, delivery_address, status, picker_id, courier_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("STAT-1", "Клиент 1", "Адрес 1", "delivered", 1, 2, "2026-09-01 09:00:00", "2026-09-01 09:30:00"),
            )
            conn.execute(
                """
                INSERT INTO action_log (order_id, employee_id, action, old_status, new_status, details)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (1, 1, "complete_assembly", "assembling", "awaiting_courier", "Собран"),
            )
            conn.execute(
                """
                INSERT INTO action_log (order_id, employee_id, action, old_status, new_status, details)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (1, 2, "pickup_order", "awaiting_courier", "in_delivery", "Забрал"),
            )
            conn.execute(
                """
                INSERT INTO action_log (order_id, employee_id, action, old_status, new_status, details)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (1, 2, "deliver_order", "in_delivery", "delivered", "Доставлен"),
            )
            conn.commit()
            conn.close()

            stats = database.get_employee_stats()

            self.assertTrue(any(item["role"] == "picker" and item["assembly_count"] >= 1 for item in stats))
            self.assertTrue(any(item["role"] == "courier" and item["delivery_count"] >= 1 for item in stats))
            self.assertTrue(any(item["avg_delivery_minutes"] is not None for item in stats))

    def test_toggle_employee_shift_changes_only_shift_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/test_delivery.db"
            database.init_database()

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                """
                INSERT INTO employees (
                    telegram_id, first_name, last_name, role,
                    application_status, is_active
                ) VALUES (?, ?, ?, ?, 'approved', 1)
                """,
                (3001, "Саид", "Юсуфов", "courier"),
            )
            connection.commit()
            connection.close()

            self.assertTrue(database.toggle_employee_shift(1))
            employee = database.get_employee_by_telegram_id(3001)
            self.assertEqual(employee["is_on_shift"], 1)
            self.assertEqual(employee["is_active"], 1)

            self.assertFalse(database.toggle_employee_shift(1))
            employee = database.get_employee_by_telegram_id(3001)
            self.assertEqual(employee["is_on_shift"], 0)
            self.assertEqual(employee["is_active"], 1)

    def test_toggle_employee_access_disables_shift_and_restores_access(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/test_delivery.db"
            database.init_database()

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                """
                INSERT INTO employees (
                    telegram_id, first_name, last_name, role,
                    application_status, is_active, is_on_shift
                ) VALUES (?, ?, ?, ?, 'approved', 1, 1)
                """,
                (3002, "Насиба", "Рахимова", "picker"),
            )
            connection.commit()
            connection.close()

            employee = database.toggle_employee_access(1)
            self.assertEqual(employee["is_active"], 0)
            self.assertEqual(employee["is_on_shift"], 0)

            employee = database.toggle_employee_access(1)
            self.assertEqual(employee["is_active"], 1)
            self.assertEqual(employee["is_on_shift"], 0)

    def test_set_employee_access_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/test_delivery.db"
            database.init_database()

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                """
                INSERT INTO employees (
                    telegram_id, first_name, last_name, role,
                    application_status, is_active, is_on_shift
                ) VALUES (?, ?, ?, ?, 'approved', 1, 1)
                """,
                (3005, "Саид", "Юсуфов", "courier"),
            )
            connection.commit()
            connection.close()

            first = database.set_employee_access(1, 0)
            second = database.set_employee_access(1, 0)
            self.assertEqual(first["is_active"], 0)
            self.assertEqual(first["is_on_shift"], 0)
            self.assertEqual(second["is_active"], 0)

            enabled = database.set_employee_access(1, 1)
            self.assertEqual(enabled["is_active"], 1)

    def test_rejected_order_can_be_resolved_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/test_delivery.db"
            database.init_database()

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                """
                INSERT INTO employees (
                    telegram_id, first_name, last_name, role,
                    application_status, is_active
                ) VALUES (?, ?, ?, ?, 'approved', 1)
                """,
                (3003, "Фарид", "Саидов", "courier"),
            )
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status, courier_id
                ) VALUES (?, ?, 'rejected', 1)
                """,
                ("REJECT-1", "Клиент"),
            )
            connection.execute(
                """
                INSERT INTO action_log (
                    order_id, employee_id, action,
                    old_status, new_status, details
                ) VALUES (1, 1, 'reject_order', 'in_delivery', 'rejected', ?)
                """,
                ("Причина: клиент не вышел",),
            )
            connection.commit()
            connection.close()

            result = database.resolve_rejected_order(1, "return_to_stock")
            self.assertTrue(result["success"])
            self.assertEqual(result["resolution"], "return_to_stock")

            second_result = database.resolve_rejected_order(1, "write_off")
            self.assertFalse(second_result["success"])
            self.assertEqual(second_result["reason"], "already_resolved")

    def test_new_order_notification_is_not_repeated_for_picker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/test_delivery.db"
            database.init_database()

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                """
                INSERT INTO employees (
                    telegram_id, first_name, last_name, role,
                    application_status, is_active, is_on_shift
                ) VALUES (?, ?, ?, 'picker', 'approved', 1, 1)
                """,
                (3004, "Малика", "Хакимова"),
            )
            connection.execute(
                """
                INSERT INTO orders (order_number, client_name, status)
                VALUES (?, ?, 'new')
                """,
                ("NEW-1", "Клиент"),
            )
            connection.commit()
            connection.close()

            self.assertEqual(len(database.get_on_shift_pickers()), 1)
            self.assertEqual(len(database.get_unnotified_new_orders(1)), 1)
            self.assertTrue(database.log_new_order_notification(1, 1))
            self.assertEqual(len(database.get_unnotified_new_orders(1)), 0)
            self.assertFalse(database.log_new_order_notification(1, 1))

    def test_send_message_with_retry_recovers_from_transient_failure(self):
        class FakeBot:
            def __init__(self):
                self.attempts = 0

            async def send_message(self, **kwargs):
                self.attempts += 1
                if self.attempts == 1:
                    raise ConnectionError("temporary failure")
                return kwargs["text"]

        bot = FakeBot()
        result = asyncio.run(
            send_message_with_retry(bot, 123, "test", attempts=2)
        )

        self.assertEqual(result, "test")
        self.assertEqual(bot.attempts, 2)

    def test_create_courier_application_saves_transport_type(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/test_delivery.db"
            database.init_database()

            result = database.create_employee_application(
                telegram_id=4001,
                telegram_username="courier_test",
                first_name="Саид",
                last_name="Каримов",
                phone="+992900000001",
                role="courier",
                transport_type="велосипед",
                language="tg",
            )

            employee = database.get_employee_by_telegram_id(4001)
            self.assertEqual(result, "created")
            self.assertEqual(employee["role"], "courier")
            self.assertEqual(employee["transport_type"], "велосипед")
            self.assertEqual(employee["language"], "tg")

    def test_full_order_lifecycle_and_access_invariants(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/lifecycle.db"
            database.init_database()

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                """
                INSERT INTO employees (
                    telegram_id, first_name, last_name, role,
                    application_status, is_active, is_on_shift
                ) VALUES (101, 'Picker', 'One', 'picker', 'approved', 1, 1)
                """
            )
            connection.execute(
                """
                INSERT INTO employees (
                    telegram_id, first_name, last_name, role,
                    application_status, is_active, is_on_shift
                ) VALUES (102, 'Courier', 'One', 'courier', 'approved', 1, 1)
                """
            )
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, delivery_address, status
                ) VALUES ('FLOW-1', 'Client', 'Address', 'new')
                """
            )
            connection.commit()
            connection.close()

            self.assertTrue(database.start_order_assembly(1, 1)["success"])
            self.assertFalse(database.start_order_assembly(1, 1)["success"])

            missing = database.mark_order_missing_item(1, 1, "Milk")
            self.assertTrue(missing["success"])
            self.assertEqual(database.get_order_by_id(1)["status"], "assembling")
            self.assertEqual(len(database.get_pending_missing_item_alerts()), 1)

            self.assertTrue(database.complete_order_assembly(1, 1)["success"])
            self.assertFalse(database.complete_order_assembly(1, 1)["success"])
            self.assertEqual(database.get_order_by_id(1)["status"], "awaiting_courier")
            self.assertEqual(len(database.get_unnotified_courier_orders(2)), 1)
            self.assertTrue(database.log_courier_order_notification(1, 2))
            self.assertEqual(len(database.get_unnotified_courier_orders(2)), 0)

            self.assertTrue(database.pickup_order(1, 2)["success"])
            self.assertFalse(database.pickup_order(1, 2)["success"])
            self.assertTrue(database.deliver_order(1, 2)["success"])
            self.assertFalse(database.deliver_order(1, 2)["success"])
            self.assertEqual(database.get_order_by_id(1)["status"], "delivered")
            feed_order = next(
                item for item in database.get_admin_orders_feed()
                if item["id"] == 1
            )
            self.assertIsNotNone(feed_order["total_delivery_minutes"])

            history = database.get_order_history(1)
            self.assertEqual(
                [event["action"] for event in history if event["employee_name"]],
                [
                    "start_assembly",
                    "missing_item",
                    "complete_assembly",
                    "courier_order_notification",
                    "pickup_order",
                    "deliver_order",
                ],
            )

            courier_stats = next(
                item for item in database.get_employee_stats()
                if item["role"] == "courier"
            )
            self.assertEqual(courier_stats["delivery_count"], 1)
            self.assertIsNotNone(courier_stats["avg_delivery_minutes"])

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, delivery_address,
                    status, courier_id
                ) VALUES ('FLOW-2', 'Client 2', 'Address 2', 'in_delivery', 2)
                """
            )
            connection.commit()
            connection.close()

            rejected = database.reject_order(2, 2, "Мизоҷ ҷавоб надод")
            self.assertTrue(rejected["success"])
            self.assertEqual(len(database.get_rejected_orders()), 1)
            resolution = database.resolve_rejected_order(
                2,
                "return_to_stock",
                admin_telegram_id=9001,
            )
            self.assertTrue(resolution["success"])
            self.assertEqual(database.get_rejected_orders(), [])
            resolved_event = database.get_order_history(2)[-1]
            self.assertIn("9001", resolved_event["details"])

    def test_database_rejects_order_actions_off_shift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/off_shift.db"
            database.init_database()
            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                """
                INSERT INTO employees (
                    telegram_id, first_name, last_name, role,
                    application_status, is_active, is_on_shift
                ) VALUES (201, 'Picker', 'Off', 'picker', 'approved', 1, 0)
                """
            )
            connection.execute(
                "INSERT INTO orders (order_number, status) VALUES ('OFF-1', 'new')"
            )
            connection.commit()
            connection.close()

            result = database.start_order_assembly(1, 1)
            self.assertFalse(result["success"])
            self.assertEqual(result["reason"], "off_shift")
            self.assertEqual(database.get_order_by_id(1)["status"], "new")

    def test_concurrent_picker_claim_assigns_order_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/concurrent.db"
            database.init_database()
            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.executemany(
                """
                INSERT INTO employees (
                    telegram_id, first_name, last_name, role,
                    application_status, is_active, is_on_shift
                ) VALUES (?, ?, ?, 'picker', 'approved', 1, 1)
                """,
                [(501, "Picker", "One"), (502, "Picker", "Two")],
            )
            connection.executemany(
                """
                INSERT INTO employees (
                    telegram_id, first_name, last_name, role,
                    application_status, is_active, is_on_shift
                ) VALUES (?, ?, ?, 'courier', 'approved', 1, 1)
                """,
                [(503, "Courier", "One"), (504, "Courier", "Two")],
            )
            connection.execute(
                "INSERT INTO orders (order_number, status) VALUES ('RACE-1', 'new')"
            )
            connection.execute(
                """
                INSERT INTO orders (order_number, status, picker_id)
                VALUES ('RACE-2', 'awaiting_courier', 1)
                """
            )
            connection.commit()
            connection.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(
                    lambda employee_id: database.start_order_assembly(1, employee_id),
                    (1, 2),
                ))

            self.assertEqual(sum(result["success"] for result in results), 1)
            history = database.get_order_history(1)
            self.assertEqual(
                sum(event["action"] == "start_assembly" for event in history),
                1,
            )

            with ThreadPoolExecutor(max_workers=2) as executor:
                pickup_results = list(executor.map(
                    lambda employee_id: database.pickup_order(2, employee_id),
                    (3, 4),
                ))

            self.assertEqual(sum(result["success"] for result in pickup_results), 1)
            self.assertEqual(database.get_order_by_id(2)["status"], "in_delivery")

    def test_existing_database_migrates_employee_and_order_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/legacy.db"
            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.executescript(
                """
                CREATE TABLE employees (
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
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE orders (
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
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            connection.commit()
            connection.close()

            database.init_database()
            connection = sqlite3.connect(database.DATABASE_PATH)
            employee_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(employees)")
            }
            order_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(orders)")
            }
            connection.close()

            self.assertTrue({"is_on_shift", "language"}.issubset(employee_columns))
            self.assertIn("client_telegram_id", order_columns)

    def test_missing_item_alert_is_pending_until_logged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/missing_alert.db"
            database.init_database()
            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                """
                INSERT INTO employees (
                    telegram_id, first_name, last_name, role,
                    application_status, is_active, is_on_shift
                ) VALUES (601, 'Picker', 'One', 'picker', 'approved', 1, 1)
                """
            )
            connection.execute(
                "INSERT INTO orders (order_number, status, picker_id) VALUES ('MISS-1', 'assembling', 1)"
            )
            connection.execute(
                """
                INSERT INTO action_log (
                    order_id, employee_id, action, old_status, new_status, details
                ) VALUES (1, 1, 'missing_item', 'assembling', 'assembling', 'Missing: Milk')
                """
            )
            connection.commit()
            connection.close()

            alerts = database.get_pending_missing_item_alerts()
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["details"], "Missing: Milk")
            self.assertTrue(database.log_missing_item_alert_sent(alerts[0]["event_id"], 1))
            self.assertEqual(database.get_pending_missing_item_alerts(), [])

    def _create_legacy_database(self, path):
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE employees (
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
                language TEXT NOT NULL DEFAULT 'ru',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                picker_id INTEGER,
                courier_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (picker_id) REFERENCES employees(id),
                FOREIGN KEY (courier_id) REFERENCES employees(id)
            );
            INSERT INTO employees (
                telegram_id, first_name, last_name, role,
                application_status, is_on_shift, language
            ) VALUES
                (100, 'Pick', 'One', 'picker', 'approved', 1, 'tg'),
                (200, 'Cour', 'Two', 'courier', 'approved', 0, 'ru');
            INSERT INTO orders (order_number, status, picker_id, courier_id)
            VALUES ('LEGACY-1', 'in_delivery', 1, 2);
            """
        )
        connection.commit()
        connection.close()

    def test_legacy_database_keeps_employees_and_allows_two_roles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/legacy_roles.db"
            self._create_legacy_database(database.DATABASE_PATH)

            database.init_database()
            database.init_database()

            picker = database.get_employee_by_telegram_id(100, "picker")
            self.assertEqual(
                (picker["id"], picker["is_on_shift"], picker["language"]),
                (1, 1, "tg"),
            )
            self.assertEqual(database.get_employee_by_telegram_id(200)["id"], 2)

            connection = sqlite3.connect(database.DATABASE_PATH)
            order = connection.execute(
                "SELECT picker_id, courier_id FROM orders"
            ).fetchone()
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            connection.close()
            self.assertEqual(order, (1, 2))
            self.assertEqual(violations, [])

            self.assertEqual(
                database.create_employee_application(
                    100, "user", "Pick", "One", "+992", "courier", "bike"
                ),
                "created",
            )

    def test_same_telegram_id_can_hold_picker_and_courier_roles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/two_roles.db"
            database.init_database()

            self.assertEqual(
                database.create_employee_application(
                    700, "user", "Ali", "Aliev", "+992", "picker"
                ),
                "created",
            )
            self.assertEqual(
                database.create_employee_application(
                    700, "user", "Ali", "Aliev", "+992", "courier", "bike"
                ),
                "created",
            )
            self.assertEqual(
                database.create_employee_application(
                    700, "user", "Ali", "Aliev", "+992", "courier", "bike"
                ),
                "already_pending",
            )

            picker = database.get_employee_by_telegram_id(700, "picker")
            courier = database.get_employee_by_telegram_id(700, "courier")
            self.assertNotEqual(picker["id"], courier["id"])
            self.assertEqual(courier["transport_type"], "bike")

            database.update_application_status(picker["id"], "approved")

            self.assertEqual(
                database.get_employee_by_telegram_id(700, "picker")["application_status"],
                "approved",
            )
            self.assertEqual(
                database.get_employee_by_telegram_id(700, "courier")["application_status"],
                "pending",
            )

    def test_rejected_application_is_resubmitted_only_for_its_own_role(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/resubmit.db"
            database.init_database()
            database.create_employee_application(
                710, "user", "Ali", "Aliev", "+992", "picker"
            )
            database.create_employee_application(
                710, "user", "Ali", "Aliev", "+992", "courier", "bike"
            )
            courier = database.get_employee_by_telegram_id(710, "courier")
            database.update_application_status(courier["id"], "rejected")

            result = database.create_employee_application(
                710, "user", "Ali", "Aliev", "+992", "courier", "car"
            )

            self.assertEqual(result, "recreated")
            courier = database.get_employee_by_telegram_id(710, "courier")
            picker = database.get_employee_by_telegram_id(710, "picker")
            self.assertEqual(
                (courier["application_status"], courier["transport_type"]),
                ("pending", "car"),
            )
            self.assertEqual(picker["application_status"], "pending")

    def test_parallel_init_on_legacy_database_migrates_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/parallel.db"
            self._create_legacy_database(database.DATABASE_PATH)

            with ThreadPoolExecutor(max_workers=6) as executor:
                futures = [executor.submit(database.init_database) for _ in range(6)]
                for future in futures:
                    future.result()

            connection = sqlite3.connect(database.DATABASE_PATH)
            count = connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
            connection.close()
            self.assertEqual(count, 2)

    def _registration_update(self, telegram_id, contact_phone=None, text=None):
        message = SimpleNamespace(
            contact=SimpleNamespace(phone_number=contact_phone)
            if contact_phone
            else None,
            text=text,
            reply_text=AsyncMock(),
        )
        return SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(
                id=telegram_id, username="tester", language_code="ru"
            ),
        )

    def test_picker_registration_removes_phone_keyboard_after_contact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/picker_registration.db"
            database.init_database()
            update = self._registration_update(800, contact_phone="+992900000001")
            context = SimpleNamespace(
                user_data={"full_name": "Ivan Ivanov", "registration_role": "picker"},
                bot_data={"role": "picker"},
            )

            result = asyncio.run(registration.get_phone(update, context))

            self.assertEqual(result, ConversationHandler.END)
            reply_markup = update.message.reply_text.await_args.kwargs["reply_markup"]
            self.assertIsInstance(reply_markup, ReplyKeyboardRemove)
            employee = database.get_employee_by_telegram_id(800, "picker")
            self.assertEqual(employee["phone"], "+992900000001")

    def test_courier_registration_removes_phone_keyboard_after_contact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/courier_registration.db"
            database.init_database()
            update = self._registration_update(801, contact_phone="+992900000002")
            context = SimpleNamespace(
                user_data={"full_name": "Ivan Ivanov", "registration_role": "courier"},
                bot_data={"role": "courier"},
            )

            result = asyncio.run(registration.get_phone(update, context))

            self.assertEqual(result, registration.COURIER_TRANSPORT)
            reply_markup = update.message.reply_text.await_args.kwargs["reply_markup"]
            self.assertIsInstance(reply_markup, ReplyKeyboardRemove)

    def test_cancel_registration_removes_leftover_keyboard(self):
        update = self._registration_update(802)
        context = SimpleNamespace(user_data={"phone": "+992"}, bot_data={})

        result = asyncio.run(registration.cancel_registration(update, context))

        self.assertEqual(result, ConversationHandler.END)
        reply_markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertIsInstance(reply_markup, ReplyKeyboardRemove)


if __name__ == "__main__":
    unittest.main()
