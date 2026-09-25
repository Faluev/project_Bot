import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

import database
from handlers.courier_orders import build_client_contact_message
from handlers.registration import build_admin_contact_message


class OrderTimeoutTests(unittest.TestCase):
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

            stale_time = (datetime.utcnow() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
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


if __name__ == "__main__":
    unittest.main()
