import asyncio
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.error import BadRequest, NetworkError
from telegram.ext import ConversationHandler

import os

os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("COURIER_BOT_TOKEN", "test-courier-bot-token")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "999999999")

import database
from handlers import registration
from handlers.courier_orders import build_client_contact_message
from handlers.registration import build_admin_contact_message
from handlers.retry import send_message_with_retry
from handlers.i18n import MESSAGES
from handlers.employee import get_current_employee, show_work_menu
from handlers.menu import get_admin_menu, get_admin_mode_menu, get_work_menu, get_work_menu_expanded


class OrderTimeoutTests(unittest.TestCase):

    def test_retry_retries_transient_network_error(self):
        bot = SimpleNamespace(
            send_message=AsyncMock(
                side_effect=[NetworkError("temporary"), "sent"]
            )
        )

        result = asyncio.run(
            send_message_with_retry(
                bot,
                chat_id=1,
                text="test",
            )
        )

        self.assertEqual(result, "sent")
        self.assertEqual(bot.send_message.await_count, 2)

    def test_retry_does_not_retry_permanent_telegram_error(self):
        bot = SimpleNamespace(
            send_message=AsyncMock(
                side_effect=BadRequest("invalid request")
            )
        )

        with self.assertRaises(BadRequest):
            asyncio.run(
                send_message_with_retry(
                    bot,
                    chat_id=1,
                    text="test",
                )
            )

        self.assertEqual(bot.send_message.await_count, 1)


    def test_current_employee_lookup_is_role_aware(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/employee_role.db"
            database.init_database()

            database.create_employee_application(
                900, "worker", "Ali", "Aliev", "+992900000009", "picker"
            )
            database.create_employee_application(
                900, "worker", "Ali", "Aliev", "+992900000009", "courier", "bike"
            )

            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=900)
            )

            picker_context = SimpleNamespace(bot_data={"role": "picker"})
            courier_context = SimpleNamespace(bot_data={"role": "courier"})

            picker = get_current_employee(update, picker_context)
            courier = get_current_employee(update, courier_context)

            self.assertEqual(picker["role"], "picker")
            self.assertEqual(courier["role"], "courier")
            self.assertNotEqual(picker["id"], courier["id"])

    def test_current_employee_lookup_rejects_unknown_role(self):
        update = SimpleNamespace(effective_user=SimpleNamespace(id=900))
        context = SimpleNamespace(bot_data={"role": "admin"})

        self.assertIsNone(get_current_employee(update, context))

    def test_approved_employee_result_contains_full_work_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/approval_result.db"
            database.init_database()

            database.create_employee_application(
                8101, "picker_ok", "Али", "Сафаров", "+9928101", "picker"
            )
            employee = database.update_application_status(1, "approved")

            self.assertEqual(employee["application_status"], "approved")
            self.assertEqual(employee["is_active"], 1)
            self.assertEqual(employee["is_on_shift"], 0)
            self.assertEqual(employee["role"], "picker")

    def test_approved_courier_result_contains_full_work_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/approval_courier_result.db"
            database.init_database()

            database.create_employee_application(
                8102, "courier_ok", "Беҳруз", "Назаров", "+9928102", "courier", "car"
            )
            employee = database.update_application_status(1, "approved")

            self.assertEqual(employee["application_status"], "approved")
            self.assertEqual(employee["is_active"], 1)
            self.assertEqual(employee["is_on_shift"], 0)
            self.assertEqual(employee["role"], "courier")

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

    def test_get_admin_stats_summary_counts_orders_and_staff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/admin_stats.db"
            database.init_database()

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                """
                INSERT INTO employees (
                    telegram_id, first_name, last_name, role,
                    application_status, is_active, is_on_shift
                ) VALUES
                    (3301, 'Pick', 'One', 'picker', 'approved', 1, 1),
                    (3302, 'Cour', 'One', 'courier', 'approved', 1, 0)
                """
            )
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status, courier_id
                ) VALUES
                    ('SUM-1', 'Client 1', 'delivered', 2),
                    ('SUM-2', 'Client 2', 'rejected', 2),
                    ('SUM-3', 'Client 3', 'new', NULL)
                """
            )
            connection.commit()
            connection.close()

            summary = database.get_admin_stats_summary()

            self.assertEqual(summary["total_orders"], 3)
            self.assertEqual(summary["delivered_orders"], 1)
            self.assertEqual(summary["rejected_orders"], 1)
            self.assertEqual(summary["active_employees"], 2)
            self.assertEqual(summary["on_shift_employees"], 1)

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

    def test_employee_access_isolated_between_roles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/two_role_access.db"
            database.init_database()

            database.create_employee_application(
                3100, "dual", "Али", "Алиев", "+992", "picker"
            )
            database.create_employee_application(
                3100, "dual", "Али", "Алиев", "+992", "courier", "bike"
            )
            picker = database.get_employee_by_telegram_id(3100, "picker")
            courier = database.get_employee_by_telegram_id(3100, "courier")
            database.update_application_status(picker["id"], "approved")
            database.update_application_status(courier["id"], "approved")

            database.set_employee_access(picker["id"], 0)

            picker = database.get_employee_by_telegram_id(3100, "picker")
            courier = database.get_employee_by_telegram_id(3100, "courier")
            self.assertEqual(picker["is_active"], 0)
            self.assertEqual(picker["is_on_shift"], 0)
            self.assertEqual(courier["is_active"], 1)

    def test_employee_stats_can_be_filtered_to_one_role_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/employee_stats_filter.db"
            database.init_database()

            database.create_employee_application(
                3200, "dual", "Мирзо", "Каримов", "+992", "picker"
            )
            database.create_employee_application(
                3200, "dual", "Мирзо", "Каримов", "+992", "courier", "car"
            )
            picker = database.get_employee_by_telegram_id(3200, "picker")
            courier = database.get_employee_by_telegram_id(3200, "courier")
            database.update_application_status(picker["id"], "approved")
            database.update_application_status(courier["id"], "approved")

            stats = database.get_employee_stats(courier["id"])
            self.assertEqual(len(stats), 1)
            self.assertEqual(stats[0]["id"], courier["id"])
            self.assertEqual(stats[0]["role"], "courier")

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

    def test_courier_registration_removes_phone_keyboard_after_contact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/courier_registration.db"
            database.init_database()
            update = self._registration_update(801, contact_phone="+992900000002")
            context = SimpleNamespace(
                user_data={"full_name": "Мухаммад Сафаров", "registration_role": "courier"},
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



    def test_admin_audit_log_returns_actor_order_action_and_details(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/audit.db"
            database.init_database()

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                """
                INSERT INTO employees (
                    telegram_id, first_name, last_name, role,
                    application_status, is_active, is_on_shift
                ) VALUES (901, 'Audit', 'User', 'picker', 'approved', 1, 1)
                """
            )
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status
                ) VALUES ('AUDIT-1', 'Client', 'assembling')
                """
            )
            connection.execute(
                """
                INSERT INTO action_log (
                    order_id, employee_id, action,
                    old_status, new_status, details
                ) VALUES (1, 1, 'start_assembly',
                          'new', 'assembling',
                          'Сборщик начал сборку заказа')
                """
            )
            connection.commit()
            connection.close()

            events = database.get_admin_audit_log(20)

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["order_number"], "AUDIT-1")
            self.assertEqual(events[0]["employee_name"], "Audit User")
            self.assertEqual(events[0]["employee_role"], "picker")
            self.assertEqual(events[0]["action"], "start_assembly")
            self.assertEqual(events[0]["old_status"], "new")
            self.assertEqual(events[0]["new_status"], "assembling")
            self.assertEqual(events[0]["details"], "Сборщик начал сборку заказа")

    def test_admin_audit_log_returns_latest_events_first_and_limits_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/audit_limit.db"
            database.init_database()

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.executemany(
                """
                INSERT INTO action_log (
                    action, details
                ) VALUES (?, ?)
                """,
                [(f"action_{index}", f"details_{index}") for index in range(3)],
            )
            connection.commit()
            connection.close()

            events = database.get_admin_audit_log(2)

            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["action"], "action_2")
            self.assertEqual(events[1]["action"], "action_1")


    def test_order_status_transition_matrix(self):
        valid = [
            ("new", "assembling"),
            ("assembling", "awaiting_courier"),
            ("awaiting_courier", "in_delivery"),
            ("in_delivery", "delivered"),
            ("in_delivery", "rejected"),
        ]
        invalid = [
            ("new", "delivered"),
            ("new", "in_delivery"),
            ("assembling", "delivered"),
            ("awaiting_courier", "delivered"),
            ("delivered", "in_delivery"),
            ("rejected", "delivered"),
        ]

        for old_status, new_status in valid:
            with self.subTest(old_status=old_status, new_status=new_status):
                self.assertTrue(
                    database.is_valid_order_transition(old_status, new_status)
                )

        for old_status, new_status in invalid:
            with self.subTest(old_status=old_status, new_status=new_status):
                self.assertFalse(
                    database.is_valid_order_transition(old_status, new_status)
                )

    def test_unknown_order_status_cannot_transition(self):
        self.assertFalse(
            database.is_valid_order_transition("unknown", "delivered")
        )


    def test_admin_picker_registration_button_starts_picker_flow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/admin_picker_registration.db"
            database.init_database()
            update = self._registration_update(registration.ADMIN_TELEGRAM_ID, text="👷 Регистрация сборщика")
            context = SimpleNamespace(user_data={}, bot_data={"role": "picker"})

        update = self._registration_update(registration.ADMIN_TELEGRAM_ID, text="👷 Регистрация сборщика")
        context = SimpleNamespace(user_data={}, bot_data={"role": "picker"})
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/admin_picker_registration.db"
            database.init_database()
            result = asyncio.run(registration.start_registration(update, context))

        self.assertEqual(result, registration.FIRST_LAST_NAME)
        self.assertEqual(context.user_data["registration_role"], "picker")
        self.assertTrue(update.message.reply_text.await_count)
        registration_prompt = update.message.reply_text.await_args.args[0].lower()
        self.assertIn("имя", registration_prompt)
        self.assertIn("фамили", registration_prompt)

    def test_admin_start_opens_mode_selection(self):
        update = self._registration_update(registration.ADMIN_TELEGRAM_ID, text="/start")
        context = SimpleNamespace(user_data={}, bot_data={"role": "picker"})

        result = asyncio.run(registration.start_registration(update, context))

        self.assertEqual(result, ConversationHandler.END)
        markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        labels = [button.text for row in markup.keyboard for button in row]
        self.assertIn("👨‍💼 Админка", labels)
        self.assertIn("👷 Сборщик", labels)

    def test_admin_menu_contains_all_core_actions(self):
        markup = get_admin_menu()
        labels = [button.text for row in markup.keyboard for button in row]
        expected = {
            "📋 Заявки",
            "👥 Сотрудники",
            "📦 Заказы",
            "📊 Статистика",
            "⏰ Таймауты",
            "📜 Аудит",
            "⚠️ Отклонённые",
            "🆔 Мой ID",
            "👷 Регистрация сборщика",
        }
        self.assertTrue(expected.issubset(set(labels)))
        self.assertEqual(len(labels), len(expected))

    def test_picker_start_menu_is_expanded(self):
        markup = get_work_menu("picker", is_on_shift=True)
        labels = [button.text for row in markup.keyboard for button in row]
        self.assertIn("📦 Текущие заказы", labels)
        self.assertIn("🟢 Завершить смену", labels)
        self.assertNotIn("☰ Меню", labels)

    def test_courier_start_menu_is_expanded(self):
        markup = get_work_menu("courier", is_on_shift=False)
        labels = [button.text for row in markup.keyboard for button in row]
        self.assertIn("🚚 Текущий заказ", labels)
        self.assertIn("🔴 Начать смену", labels)
        self.assertNotIn("☰ Меню", labels)

    def test_show_work_menu_expands_picker_actions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/expand_picker.db"
            database.init_database()
            database.create_employee_application(
                910, "picker_user", "Ivan", "Ivanov", "+992910", "picker"
            )
            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET application_status = 'approved' WHERE id = 1")
            connection.commit()
            connection.close()

            update = self._registration_update(910)
            context = SimpleNamespace(bot_data={"role": "picker"}, user_data={})

            asyncio.run(show_work_menu(update, context))

            markup = update.message.reply_text.await_args.kwargs["reply_markup"]
            labels = [button.text for row in markup.keyboard for button in row]
            self.assertIn("📦 Текущие заказы", labels)
            self.assertIn("🔴 Начать смену", labels)

    def test_show_work_menu_expands_courier_actions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/expand_courier.db"
            database.init_database()
            database.create_employee_application(
                911, "courier_user", "Ivan", "Ivanov", "+992911", "courier", "car"
            )
            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET application_status = 'approved' WHERE id = 1")
            connection.commit()
            connection.close()

            update = self._registration_update(911)
            context = SimpleNamespace(bot_data={"role": "courier"}, user_data={})

            asyncio.run(show_work_menu(update, context))

            markup = update.message.reply_text.await_args.kwargs["reply_markup"]
            labels = [button.text for row in markup.keyboard for button in row]
            self.assertIn("🚚 Текущий заказ", labels)
            self.assertIn("🔴 Начать смену", labels)

    def test_courier_queue_shows_at_most_two_available_orders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/courier_queue_limit.db"
            database.init_database()

            database.create_employee_application(
                940, "courier_a", "Рустам", "Назаров", "+992940", "courier", "car"
            )
            database.update_application_status(1, "approved")
            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id = 1")
            for number in ("C-Q1", "C-Q2", "C-Q3", "C-Q4"):
                connection.execute(
                    "INSERT INTO orders (order_number, client_name, status, created_at, updated_at) "
                    "VALUES (?, ?, 'awaiting_courier', '2026-09-29 10:00:00', '2026-09-29 10:00:00')",
                    (number, "Клиент"),
                )
            connection.commit()
            connection.close()

            orders = database.get_orders_for_courier(1)

            self.assertEqual([o["order_number"] for o in orders], ["C-Q1", "C-Q2"])

    def test_courier_queue_refreshes_after_other_courier_takes_oldest_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/courier_queue_refresh.db"
            database.init_database()

            for employee_id, telegram_id in ((1, 941), (2, 942)):
                database.create_employee_application(
                    telegram_id, f"courier_{employee_id}", "Курьер", str(employee_id),
                    f"+992{employee_id}", "courier", "car"
                )
                database.update_application_status(employee_id, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1")
            for index, number in enumerate(("C-R1", "C-R2", "C-R3")):
                stamp = f"2026-09-29 10:{index:02d}:00"
                connection.execute(
                    "INSERT INTO orders (order_number, client_name, status, created_at, updated_at) "
                    "VALUES (?, ?, 'awaiting_courier', ?, ?)",
                    (number, "Клиент", stamp, stamp),
                )
            connection.commit()
            connection.close()

            first_view = database.get_orders_for_courier(1)
            self.assertEqual([o["order_number"] for o in first_view], ["C-R1", "C-R2"])

            self.assertTrue(database.pickup_order(1, 2)["success"])

            refreshed = database.get_orders_for_courier(1)
            self.assertEqual([o["order_number"] for o in refreshed], ["C-R2", "C-R3"])
            self.assertNotIn("C-R1", [o["order_number"] for o in refreshed])

    def test_courier_shift_start_uses_expanded_menu_and_shows_orders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/courier_shift_ui.db"
            database.init_database()
            database.create_employee_application(
                943, "courier_ui", "Фарид", "Саидов", "+992943", "courier", "car"
            )
            database.update_application_status(1, "approved")
            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("INSERT INTO orders (order_number, client_name, status) VALUES ('C-UI1', 'Клиент', 'awaiting_courier')")
            connection.commit()
            connection.close()

            update = self._registration_update(943)
            context = SimpleNamespace(bot_data={"role": "courier"}, user_data={})

            asyncio.run(__import__("handlers.shift", fromlist=["toggle_shift"]).toggle_shift(update, context))

            self.assertEqual(update.message.reply_text.await_count, 2)
            first_markup = update.message.reply_text.await_args_list[0].kwargs["reply_markup"]
            first_labels = [button.text for row in first_markup.keyboard for button in row]
            self.assertIn("🚚 Текущий заказ", first_labels)
            self.assertNotIn("☰ Меню", first_labels)
            second_text = update.message.reply_text.await_args_list[1].args[0]
            self.assertIn("C-UI1", second_text)

    def test_courier_queue_never_exposes_third_order_when_two_are_active(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/courier_queue_active_limit.db"
            database.init_database()
            database.create_employee_application(
                944, "courier_active", "Нурбек", "Хакимов", "+992944", "courier", "car"
            )
            database.update_application_status(1, "approved")
            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1")
            for index, number in enumerate(("C-A1", "C-A2", "C-A3")):
                connection.execute(
                    "INSERT INTO orders (order_number, client_name, status, courier_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        number, "Клиент",
                        "in_delivery" if index < 2 else "awaiting_courier",
                        1 if index < 2 else None,
                        f"2026-09-29 11:0{index}:00",
                        f"2026-09-29 11:0{index}:00",
                    ),
                )
            connection.commit()
            connection.close()

            orders = database.get_orders_for_courier(1)
            self.assertEqual([o["order_number"] for o in orders], ["C-A1", "C-A2"])

    def test_expanded_picker_menu_changes_shift_action(self):
        off_shift = get_work_menu_expanded("picker", False, "ru")
        on_shift = get_work_menu_expanded("picker", True, "ru")

        off_labels = [button.text for row in off_shift.keyboard for button in row]
        on_labels = [button.text for row in on_shift.keyboard for button in row]

        self.assertIn("🔴 Начать смену", off_labels)
        self.assertIn("🟢 Завершить смену", on_labels)
        self.assertNotIn("🟢 Завершить смену", off_labels)
        self.assertNotIn("🔴 Начать смену", on_labels)


    def test_application_approval_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/approval_idempotent.db"
            database.init_database()

            database.create_employee_application(
                920, "worker", "Ivan", "Ivanov", "+992920", "picker"
            )

            first = database.update_application_status(1, "approved")
            second = database.update_application_status(1, "approved")

            self.assertIsNotNone(first)
            self.assertEqual(first["application_status"], "approved")
            self.assertIsNone(second)

            employee = database.get_employee_by_id(1)
            self.assertEqual(employee["application_status"], "approved")
            self.assertEqual(employee["is_active"], 1)

    def test_rejected_application_cannot_be_approved_without_resubmission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/reject_then_approve.db"
            database.init_database()

            database.create_employee_application(
                921, "worker", "Ivan", "Ivanov", "+992921", "picker"
            )

            rejected = database.update_application_status(1, "rejected")
            approved = database.update_application_status(1, "approved")

            self.assertEqual(rejected["application_status"], "rejected")
            self.assertIsNone(approved)

            employee = database.get_employee_by_id(1)
            self.assertEqual(employee["application_status"], "rejected")
            self.assertEqual(employee["is_active"], 0)

    def test_disabled_employee_cannot_start_shift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/disabled_shift.db"
            database.init_database()

            database.create_employee_application(
                922, "worker", "Ivan", "Ivanov", "+992922", "courier", "car"
            )
            database.update_application_status(1, "approved")
            database.set_employee_access(1, 0)

            result = database.toggle_employee_shift(1)

            self.assertIsNone(result)
            employee = database.get_employee_by_id(1)
            self.assertEqual(employee["is_active"], 0)
            self.assertEqual(employee["is_on_shift"], 0)

    def test_courier_cannot_deliver_another_couriers_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/wrong_courier.db"
            database.init_database()

            database.create_employee_application(
                923, "courier_a", "A", "Courier", "+992923", "courier", "car"
            )
            database.create_employee_application(
                924, "courier_b", "B", "Courier", "+992924", "courier", "car"
            )
            database.update_application_status(1, "approved")
            database.update_application_status(2, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id IN (1, 2)")
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status, courier_id
                ) VALUES ('OWNER-1', 'Client', 'in_delivery', 1)
                """
            )
            connection.commit()
            connection.close()

            result = database.deliver_order(1, 2)

            self.assertFalse(result["success"])
            self.assertEqual(result["reason"], "not_courier")

            order = database.get_order_by_id(1)
            self.assertEqual(order["status"], "in_delivery")
            self.assertEqual(order["courier_id"], 1)

    def test_reject_order_records_reason_and_blocks_second_rejection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/reject_once.db"
            database.init_database()

            database.create_employee_application(
                925, "courier", "Ivan", "Courier", "+992925", "courier", "car"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id = 1")
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status, courier_id
                ) VALUES ('REJ-ONCE', 'Client', 'in_delivery', 1)
                """
            )
            connection.commit()
            connection.close()

            first = database.reject_order(1, 1, "Клиент отказался")
            second = database.reject_order(1, 1, "Другая причина")

            self.assertTrue(first["success"])
            self.assertFalse(second["success"])
            self.assertEqual(second["reason"], "wrong_status")

            history = database.get_order_history(1)
            reject_events = [
                event for event in history if event["action"] == "reject_order"
            ]
            self.assertEqual(len(reject_events), 1)
            self.assertIn("Клиент отказался", reject_events[0]["details"])

    def test_rejected_order_rejects_invalid_admin_resolution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/invalid_resolution.db"
            database.init_database()

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status
                ) VALUES ('REJ-INVALID', 'Client', 'rejected')
                """
            )
            connection.commit()
            connection.close()

            result = database.resolve_rejected_order(1, "destroy")

            self.assertFalse(result["success"])
            self.assertEqual(result["reason"], "invalid_resolution")

            history = database.get_order_history(1)
            self.assertEqual(len(history), 0)

    def test_rejected_order_resolution_is_recorded_in_audit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/resolution_audit.db"
            database.init_database()

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status
                ) VALUES ('REJ-AUDIT', 'Client', 'rejected')
                """
            )
            connection.commit()
            connection.close()

            result = database.resolve_rejected_order(
                1,
                "write_off",
                admin_telegram_id=123456,
            )

            self.assertTrue(result["success"])

            events = database.get_admin_audit_log(10)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["action"], "write_off")
            self.assertIn("123456", events[0]["details"])

    def test_role_access_change_does_not_affect_other_role_shift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/role_shift_isolation.db"
            database.init_database()

            database.create_employee_application(
                926, "dual", "Dual", "User", "+992926", "picker"
            )
            database.create_employee_application(
                926, "dual", "Dual", "User", "+992926", "courier", "bike"
            )
            database.update_application_status(1, "approved")
            database.update_application_status(2, "approved")
            database.toggle_employee_shift(1)
            database.toggle_employee_shift(2)

            database.set_employee_access(1, 0)

            picker = database.get_employee_by_id(1)
            courier = database.get_employee_by_id(2)

            self.assertEqual((picker["is_active"], picker["is_on_shift"]), (0, 0))
            self.assertEqual((courier["is_active"], courier["is_on_shift"]), (1, 1))

    def test_timeout_alert_creates_auditable_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/timeout_audit.db"
            database.init_database()

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status
                ) VALUES ('TIMEOUT-1', 'Client', 'new')
                """
            )
            connection.commit()
            connection.close()

            result = database.log_order_timeout_alert(1, "new", 15)

            self.assertTrue(result)
            events = database.get_admin_audit_log(10)
            self.assertEqual(events[0]["action"], "timeout_alert")
            self.assertEqual(events[0]["old_status"], "new")
            self.assertEqual(events[0]["new_status"], "new")
            self.assertIn("15 минут", events[0]["details"])

    def test_order_history_contains_full_lifecycle_actor_sequence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/lifecycle_audit.db"
            database.init_database()

            database.create_employee_application(
                927, "picker", "Pick", "Worker", "+992927", "picker"
            )
            database.create_employee_application(
                928, "courier", "Cour", "Worker", "+992928", "courier", "car"
            )
            database.update_application_status(1, "approved")
            database.update_application_status(2, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id IN (1, 2)")
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status
                ) VALUES ('LIFE-1', 'Client', 'awaiting_courier')
                """
            )
            connection.commit()
            connection.close()

            picked = database.pickup_order(1, 2)
            delivered = database.deliver_order(1, 2)

            self.assertTrue(picked["success"])
            self.assertTrue(delivered["success"])

            history = database.get_order_history(1)
            actions = [event["action"] for event in history]
            actors = [event["employee_id"] for event in history]

            self.assertEqual(actions, ["pickup_order", "deliver_order"])
            self.assertEqual(actors, [2, 2])



    def test_invalid_employee_role_cannot_create_application(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/invalid_role.db"
            database.init_database()

            result = database.create_employee_application(
                930, "invalid", "Ivan", "Ivanov", "+992930", "admin"
            )

            self.assertEqual(result, "invalid_role")
            self.assertIsNone(database.get_employee_by_id(1))

    def test_invalid_application_status_cannot_change_pending_application(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/invalid_application_status.db"
            database.init_database()

            database.create_employee_application(
                931, "worker", "Ivan", "Ivanov", "+992931", "picker"
            )

            result = database.update_application_status(1, "banana")

            self.assertIsNone(result)
            employee = database.get_employee_by_id(1)
            self.assertEqual(employee["application_status"], "pending")
            self.assertEqual(employee["is_active"], 1)


    def test_invalid_timeout_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/invalid_timeout.db"
            database.init_database()

            self.assertEqual(database.get_order_timeouts(0), [])
            self.assertEqual(database.get_order_timeouts(-1), [])
            self.assertFalse(database.log_order_timeout_alert(1, "new", 0))
            self.assertFalse(database.log_order_timeout_alert(1, "invalid", 15))

    def test_reject_order_requires_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/empty_rejection_reason.db"
            database.init_database()

            database.create_employee_application(
                932, "courier", "Ivan", "Courier", "+992932", "courier", "car"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id = 1")
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status, courier_id
                ) VALUES ('REJ-EMPTY', 'Client', 'in_delivery', 1)
                """
            )
            connection.commit()
            connection.close()

            result = database.reject_order(1, 1, "   ")

            self.assertFalse(result["success"])
            self.assertEqual(result["reason"], "invalid_reason")
            self.assertEqual(database.get_order_by_id(1)["status"], "in_delivery")

    def test_missing_item_requires_item_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/empty_item.db"
            database.init_database()

            database.create_employee_application(
                933, "picker", "Ivan", "Picker", "+992933", "picker"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id = 1")
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status, picker_id
                ) VALUES ('ITEM-EMPTY', 'Client', 'assembling', 1)
                """
            )
            connection.commit()
            connection.close()

            result = database.mark_order_missing_item(1, 1, "   ")

            self.assertFalse(result["success"])
            self.assertEqual(result["reason"], "invalid_item_name")
            self.assertEqual(database.get_order_by_id(1)["status"], "assembling")


    def test_employee_application_requires_required_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/invalid_employee_fields.db"
            database.init_database()

            self.assertEqual(
                database.create_employee_application(
                    4101, "picker1", "", "Иванов", "+992", "picker"
                ),
                "invalid_first_name",
            )
            self.assertEqual(
                database.create_employee_application(
                    4102, "picker2", "Иван", "", "+992", "picker"
                ),
                "invalid_last_name",
            )
            self.assertEqual(
                database.create_employee_application(
                    4103, "picker3", "Иван", "Иванов", "   ", "picker"
                ),
                "invalid_phone",
            )
            self.assertEqual(
                database.create_employee_application(
                    4104, "courier1", "Иван", "Иванов", "+992", "courier"
                ),
                "invalid_transport",
            )
            self.assertEqual(
                database.create_employee_application(
                    4105, "picker4", "Иван", "Иванов", "+992", "picker", "car"
                ),
                "invalid_transport",
            )


    def test_picker_queue_shows_one_order_and_moves_to_next_after_claim_and_completion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/picker_queue.db"
            database.init_database()

            database.create_employee_application(
                5001, "ali", "Али", "Сборщик", "+9925001", "picker"
            )
            database.create_employee_application(
                5002, "ivan", "Иван", "Сборщик", "+9925002", "picker"
            )
            database.update_application_status(1, "approved")
            database.update_application_status(2, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1")
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status, created_at, updated_at
                ) VALUES
                    ('QUEUE-1', 'Клиент 1', 'new', '2026-09-29 10:10:00', '2026-09-29 10:10:00'),
                    ('QUEUE-2', 'Клиент 2', 'new', '2026-09-29 10:15:00', '2026-09-29 10:15:00'),
                    ('QUEUE-3', 'Клиент 3', 'new', '2026-09-29 10:20:00', '2026-09-29 10:20:00')
                """
            )
            connection.commit()
            connection.close()

            self.assertEqual(database.get_orders_for_picker(1)[0]["order_number"], "QUEUE-1")
            self.assertEqual(database.get_orders_for_picker(2)[0]["order_number"], "QUEUE-1")

            self.assertTrue(database.start_order_assembly(1, 1)["success"])

            self.assertEqual(database.get_orders_for_picker(1)[0]["order_number"], "QUEUE-1")
            self.assertEqual(database.get_orders_for_picker(2)[0]["order_number"], "QUEUE-2")

            self.assertTrue(database.complete_order_assembly(1, 1)["success"])

            self.assertEqual(database.get_orders_for_picker(1)[0]["order_number"], "QUEUE-2")
            self.assertEqual(database.get_orders_for_picker(2)[0]["order_number"], "QUEUE-2")

    def test_picker_can_have_only_one_active_order_and_gets_oldest_next(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/picker_single_active.db"
            database.init_database()

            database.create_employee_application(
                5901, "picker", "Сборщик", "Тестовый", "+9925901", "picker"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id = 1")
            connection.execute(
                """
                INSERT INTO orders (order_number, client_name, status, created_at, updated_at)
                VALUES
                    ('PICK-OLD-1', 'Клиент 1', 'new', '2026-09-29 09:00:00', '2026-09-29 09:00:00'),
                    ('PICK-OLD-2', 'Клиент 2', 'new', '2026-09-29 09:05:00', '2026-09-29 09:05:00'),
                    ('PICK-OLD-3', 'Клиент 3', 'new', '2026-09-29 09:10:00', '2026-09-29 09:10:00')
                """
            )
            connection.commit()
            connection.close()

            self.assertEqual(
                [row["order_number"] for row in database.get_orders_for_picker(1)],
                ["PICK-OLD-1"],
            )

            self.assertTrue(database.start_order_assembly(1, 1)["success"])

            second = database.start_order_assembly(2, 1)
            self.assertFalse(second["success"])
            self.assertEqual(second["reason"], "picker_capacity_reached")

            self.assertTrue(database.complete_order_assembly(1, 1)["success"])
            self.assertEqual(
                [row["order_number"] for row in database.get_orders_for_picker(1)],
                ["PICK-OLD-2"],
            )

    def test_courier_can_take_two_orders_and_must_deliver_oldest_first(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/courier_capacity.db"
            database.init_database()

            database.create_employee_application(
                6001, "courier", "Курьер", "Тестовый", "+9926001", "courier", "car"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id = 1")
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status, created_at, updated_at
                ) VALUES
                    ('COURIER-1', 'Клиент 1', 'awaiting_courier', '2026-09-29 10:10:00', '2026-09-29 10:10:00'),
                    ('COURIER-2', 'Клиент 2', 'awaiting_courier', '2026-09-29 10:15:00', '2026-09-29 10:15:00'),
                    ('COURIER-3', 'Клиент 3', 'awaiting_courier', '2026-09-29 10:20:00', '2026-09-29 10:20:00')
                """
            )
            connection.commit()
            connection.close()

            self.assertTrue(database.pickup_order(1, 1)["success"])
            self.assertTrue(database.pickup_order(2, 1)["success"])

            # Пока старые взятые заказы ещё в работе, третий заказ не выдаётся.
            self.assertEqual(database.get_unnotified_courier_orders(1), [])

            # Нельзя доставить более новый заказ раньше старого.
            second_first = database.deliver_order(2, 1)
            self.assertFalse(second_first["success"])
            self.assertEqual(second_first["reason"], "priority_order")
            self.assertEqual(second_first["priority_order_number"], "COURIER-1")

            # Сначала доставляем старый, затем новый.
            self.assertTrue(database.deliver_order(1, 1)["success"])
            self.assertEqual(database.get_unnotified_courier_orders(1), [])

            self.assertTrue(database.deliver_order(2, 1)["success"])

            # После доставки всех ранее взятых заказов следующий старый
            # ожидающий заказ становится доступен сразу.
            available = database.get_unnotified_courier_orders(1)
            self.assertEqual(
                [row["order_number"] for row in available],
                ["COURIER-3"],
            )

            self.assertTrue(database.pickup_order(3, 1)["success"])
            self.assertTrue(database.deliver_order(3, 1)["success"])

    def test_application_rejects_unknown_role_without_creating_employee(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/invalid_role.db"
            database.init_database()

            result = database.create_employee_application(
                7101, "worker_a", "Али", "Рахимов", "+9927101", "manager"
            )

            self.assertEqual(result, "invalid_role")
            self.assertIsNone(database.get_employee_by_telegram_id(7101, "picker"))
            self.assertIsNone(database.get_employee_by_telegram_id(7101, "courier"))

    def test_courier_application_requires_transport(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/transport_required.db"
            database.init_database()

            result = database.create_employee_application(
                7102, "courier_b", "Мадина", "Саидова", "+9927102", "courier"
            )

            self.assertEqual(result, "invalid_transport")
            self.assertIsNone(database.get_employee_by_telegram_id(7102, "courier"))

    def test_picker_application_rejects_unexpected_transport(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/picker_transport.db"
            database.init_database()

            result = database.create_employee_application(
                7103, "picker_c", "Фарход", "Нуров", "+9927103", "picker", "scooter"
            )

            self.assertEqual(result, "invalid_transport")

    def test_same_telegram_id_can_have_independent_picker_and_courier_statuses(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/dual_role_status.db"
            database.init_database()

            database.create_employee_application(
                7104, "dual_user", "Саида", "Хасанова", "+9927104", "picker"
            )
            database.create_employee_application(
                7104, "dual_user", "Саида", "Хасанова", "+9927104", "courier", "bike"
            )

            database.update_application_status(1, "approved")
            database.update_application_status(2, "rejected")

            picker = database.get_employee_by_telegram_id(7104, "picker")
            courier = database.get_employee_by_telegram_id(7104, "courier")

            self.assertEqual(picker["application_status"], "approved")
            self.assertEqual(courier["application_status"], "rejected")
            self.assertEqual(picker["is_active"], 1)
            self.assertEqual(courier["is_active"], 0)

    def test_disabled_picker_cannot_start_assembly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/disabled_picker.db"
            database.init_database()

            database.create_employee_application(
                7105, "picker_d", "Рустам", "Абдуллоев", "+9927105", "picker"
            )
            database.update_application_status(1, "approved")
            database.set_employee_access(1, 0)

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                "INSERT INTO orders (order_number, client_name, status) VALUES ('SAFE-1', 'Клиент', 'new')"
            )
            connection.commit()
            connection.close()

            result = database.start_order_assembly(1, 1)

            self.assertFalse(result["success"])
            self.assertEqual(result["reason"], "inactive")

    def test_off_shift_picker_cannot_start_assembly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/off_shift_picker.db"
            database.init_database()

            database.create_employee_application(
                7106, "picker_e", "Беҳруз", "Назаров", "+9927106", "picker"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                "INSERT INTO orders (order_number, client_name, status) VALUES ('SAFE-2', 'Клиент', 'new')"
            )
            connection.commit()
            connection.close()

            result = database.start_order_assembly(1, 1)

            self.assertFalse(result["success"])
            self.assertEqual(result["reason"], "off_shift")

    def test_missing_item_is_logged_without_changing_assembly_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/missing_item_log.db"
            database.init_database()

            database.create_employee_application(
                7107, "picker_f", "Шахло", "Юсуфова", "+9927107", "picker"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id = 1")
            connection.execute(
                "INSERT INTO orders (order_number, client_name, status) VALUES ('SAFE-3', 'Клиент', 'new')"
            )
            connection.commit()
            connection.close()

            self.assertTrue(database.start_order_assembly(1, 1)["success"])
            result = database.mark_order_missing_item(1, 1, "Молоко")

            self.assertTrue(result["success"])
            order = database.get_order_by_id(1)
            self.assertEqual(order["status"], "assembling")

            history = database.get_order_history(1)
            self.assertTrue(
                any(
                    row["action"] == "missing_item"
                    and "Молоко" in row["details"]
                    for row in history
                )
            )

    def test_courier_cannot_take_third_active_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/courier_limit.db"
            database.init_database()

            database.create_employee_application(
                7108, "courier_g", "Камол", "Мирзоев", "+9927108", "courier", "car"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id = 1")
            connection.execute(
                """
                INSERT INTO orders (order_number, client_name, status)
                VALUES
                    ('SAFE-4', 'Клиент 1', 'awaiting_courier'),
                    ('SAFE-5', 'Клиент 2', 'awaiting_courier'),
                    ('SAFE-6', 'Клиент 3', 'awaiting_courier')
                """
            )
            connection.commit()
            connection.close()

            self.assertTrue(database.pickup_order(1, 1)["success"])
            self.assertTrue(database.pickup_order(2, 1)["success"])

            third = database.pickup_order(3, 1)
            self.assertFalse(third["success"])
            self.assertEqual(third["reason"], "capacity_reached")

    def test_rejected_order_requires_resolution_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/rejected_resolution.db"
            database.init_database()

            database.create_employee_application(
                7109, "courier_h", "Малика", "Давлатова", "+9927109", "courier", "bike"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id = 1")
            connection.execute(
                "INSERT INTO orders (order_number, client_name, status) VALUES ('SAFE-7', 'Клиент', 'awaiting_courier')"
            )
            connection.commit()
            connection.close()

            self.assertTrue(database.pickup_order(1, 1)["success"])
            rejected = database.reject_order(1, 1, "client_no_show")
            self.assertTrue(rejected["success"])

            resolved = database.resolve_rejected_order(
                1, "return_to_stock", admin_telegram_id=999001
            )
            self.assertTrue(resolved["success"])

            duplicate = database.resolve_rejected_order(
                1, "write_off", admin_telegram_id=999001
            )
            self.assertFalse(duplicate["success"])
            self.assertEqual(duplicate["reason"], "already_resolved")

    def test_terminal_order_status_has_no_outgoing_transition(self):
        self.assertFalse(database.is_valid_order_transition("delivered", "new"))
        self.assertFalse(database.is_valid_order_transition("delivered", "assembling"))
        self.assertFalse(database.is_valid_order_transition("rejected", "awaiting_courier"))


    def test_admin_mode_menu_has_separate_admin_and_picker_buttons(self):
        menu = get_admin_mode_menu()
        rows = [[button.text for button in row] for row in menu.keyboard]

        self.assertEqual(rows, [["👨‍💼 Админка"], ["👷 Сборщик"]])

    def test_one_assembled_order_is_sent_without_waiting_for_second(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/one_courier_order.db"
            database.init_database()

            database.create_employee_application(
                7401, "courier_one", "Рустам", "Каримов", "+9927401", "courier", "car"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id = 1")
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status, created_at, updated_at
                ) VALUES (
                    'ONE-READY-1', 'Клиент 1', 'awaiting_courier',
                    '2026-09-29 10:00:00', '2026-09-29 10:00:00'
                )
                """
            )
            connection.commit()
            connection.close()

            available = database.get_unnotified_courier_orders(1)

            self.assertEqual(len(available), 1)
            self.assertEqual(available[0]["order_number"], "ONE-READY-1")

    def test_second_order_is_independent_and_does_not_gate_first_notification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/courier_order_gate.db"
            database.init_database()

            database.create_employee_application(
                7402, "courier_two", "Фарход", "Нуров", "+9927402", "courier", "car"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id = 1")
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status, created_at, updated_at
                ) VALUES
                    ('READY-1', 'Клиент 1', 'awaiting_courier', '2026-09-29 10:00:00', '2026-09-29 10:00:00'),
                    ('READY-2', 'Клиент 2', 'new', '2026-09-29 10:01:00', '2026-09-29 10:01:00')
                """
            )
            connection.commit()
            connection.close()

            first = database.get_unnotified_courier_orders(1)
            self.assertEqual([row["order_number"] for row in first], ["READY-1"])

            self.assertTrue(database.log_courier_order_notification(1, 1, message_id=501))

            second = database.get_unnotified_courier_orders(1)
            self.assertEqual(second, [])

    def test_picker_cannot_skip_older_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/picker_fifo.db"
            database.init_database()

            database.create_employee_application(
                8001, "picker_fifo", "Али", "Сафаров", "+9928001", "picker"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                "UPDATE employees SET is_on_shift = 1 WHERE id = 1"
            )
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status, created_at, updated_at
                ) VALUES
                    ('FIFO-1', 'Клиент 1', 'new', '2026-09-29 10:00:00', '2026-09-29 10:00:00'),
                    ('FIFO-2', 'Клиент 2', 'new', '2026-09-29 10:01:00', '2026-09-29 10:01:00'),
                    ('FIFO-3', 'Клиент 3', 'new', '2026-09-29 10:02:00', '2026-09-29 10:02:00')
                """
            )
            connection.commit()
            connection.close()

            # В интерфейсе первым доступен самый старый.
            visible = database.get_orders_for_picker(1)
            self.assertEqual(visible[0]["order_number"], "FIFO-1")

            # Прямой вызов более нового заказа тоже обязан быть заблокирован.
            skipped = database.start_order_assembly(2, 1)
            self.assertFalse(skipped["success"])
            self.assertEqual(skipped["reason"], "not_oldest")

            # Старый заказ можно взять.
            first = database.start_order_assembly(1, 1)
            self.assertTrue(first["success"])

            # Пока первый не завершён, второй не становится доступным.
            visible = database.get_orders_for_picker(1)
            self.assertEqual([row["order_number"] for row in visible], ["FIFO-1"])

            # После завершения первым становится следующий старый.
            completed = database.complete_order_assembly(1, 1)
            self.assertTrue(completed["success"])
            visible = database.get_orders_for_picker(1)
            self.assertEqual([row["order_number"] for row in visible], ["FIFO-2"])

    
    def test_courier_notification_bot_is_cached_between_calls(self):
        from handlers import courier_orders

        courier_orders._courier_notification_bot = None

        class FakeBot:
            def __init__(self, token):
                self.token = token
            async def initialize(self):
                return None

        original_bot = courier_orders.Bot
        try:
            courier_orders.Bot = FakeBot

            first = asyncio.run(courier_orders._get_courier_notification_bot())
            second = asyncio.run(courier_orders._get_courier_notification_bot())

            self.assertIs(first, second)
            self.assertEqual(first.token, courier_orders.COURIER_BOT_TOKEN)
        finally:
            courier_orders.Bot = original_bot
            courier_orders._courier_notification_bot = None

    def test_courier_notification_helper_uses_courier_bot_token(self):
        from handlers import courier_orders

        self.assertEqual(courier_orders.COURIER_BOT_TOKEN, database.COURIER_BOT_TOKEN if hasattr(database, "COURIER_BOT_TOKEN") else courier_orders.COURIER_BOT_TOKEN)

    def test_courier_notification_query_returns_one_order_immediately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/courier_immediate.db"
            database.init_database()

            database.create_employee_application(
                7501, "courier_fast", "Али", "Сафаров", "+9927501", "courier", "car"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id = 1")
            connection.execute(
                "INSERT INTO orders (order_number, client_name, status, created_at, updated_at) "
                "VALUES ('FAST-1', 'Клиент', 'awaiting_courier', '2026-09-29 10:00:00', '2026-09-29 10:00:00')"
            )
            connection.commit()
            connection.close()

            orders = database.get_unnotified_courier_orders(1)
            self.assertEqual([row["order_number"] for row in orders], ["FAST-1"])

    def test_picker_menu_has_no_courier_pickup_action(self):
        picker_menu = get_work_menu_expanded("picker", True, "ru")
        picker_buttons = {
            button.text
            for row in picker_menu.keyboard
            for button in row
        }

        self.assertIn("📦 Текущие заказы", picker_buttons)
        self.assertNotIn("🚚 Текущий заказ", picker_buttons)
        self.assertNotIn("Забрал заказ", picker_buttons)

    def test_courier_menu_has_courier_order_entry(self):
        courier_menu = get_work_menu_expanded("courier", True, "ru")
        courier_buttons = {
            button.text
            for row in courier_menu.keyboard
            for button in row
        }

        self.assertIn("🚚 Текущий заказ", courier_buttons)
        self.assertNotIn("📦 Текущие заказы", courier_buttons)

    def test_picker_and_admin_menus_are_separate(self):
        admin_menu = get_admin_menu()
        picker_menu = get_work_menu_expanded("picker", False, "ru")

        admin_buttons = {
            button.text
            for row in admin_menu.keyboard
            for button in row
        }
        picker_buttons = {
            button.text
            for row in picker_menu.keyboard
            for button in row
        }

        self.assertIn("📋 Заявки", admin_buttons)
        self.assertIn("👥 Сотрудники", admin_buttons)
        self.assertIn("📦 Текущие заказы", picker_buttons)
        self.assertIn("🔴 Начать смену", picker_buttons)
        self.assertNotIn("📋 Заявки", picker_buttons)
        self.assertNotIn("📦 Текущие заказы", admin_buttons)

    def test_picker_shift_start_shows_current_order_immediately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/picker_shift_start.db"
            database.init_database()

            database.create_employee_application(
                7201, "picker_shift", "Али", "Сафаров", "+9927201", "picker"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                "INSERT INTO orders (order_number, client_name, status, created_at, updated_at) VALUES "
                "('PICKER-LIVE-1', 'Клиент 1', 'new', '2026-09-29 10:00:00', '2026-09-29 10:00:00')"
            )
            connection.commit()
            connection.close()

            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=7201, language_code="ru"),
                message=SimpleNamespace(
                    reply_text=AsyncMock()
                ),
            )
            context = SimpleNamespace(
                bot_data={"role": "picker"},
                user_data={},
            )

            asyncio.run(
                __import__("handlers.shift", fromlist=["toggle_shift"]).toggle_shift(
                    update,
                    context,
                )
            )

            replies = update.message.reply_text.await_args_list
            self.assertEqual(len(replies), 2)
            self.assertIn("PICKER-LIVE-1", replies[1].args[0])


    def test_picker_receives_next_order_automatically_after_completion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/picker_auto_next.db"
            database.init_database()

            database.create_employee_application(
                7401, "picker_auto", "Фарход", "Нуров", "+9927401", "picker"
            )
            database.update_application_status(1, "approved")

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute("UPDATE employees SET is_on_shift = 1 WHERE id = 1")
            connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, status, created_at, updated_at
                ) VALUES
                    ('AUTO-1001', 'Клиент 1', 'new', '2026-09-29 12:00:00', '2026-09-29 12:00:00'),
                    ('AUTO-1002', 'Клиент 2', 'new', '2026-09-29 12:01:00', '2026-09-29 12:01:00')
                """
            )
            connection.commit()
            connection.close()

            started = database.start_order_assembly(1, 1)
            self.assertTrue(started["success"])

            completed = database.complete_order_assembly(1, 1)
            self.assertTrue(completed["success"])

            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=7401, language_code="ru"),
                message=SimpleNamespace(reply_text=AsyncMock()),
            )
            context = SimpleNamespace(
                bot=SimpleNamespace(
                    send_message=AsyncMock(
                        return_value=SimpleNamespace(message_id=555)
                    )
                ),
                bot_data={"role": "picker"},
                user_data={},
            )

            asyncio.run(
                __import__("handlers.orders", fromlist=["notify_next_order_to_picker"])
                .notify_next_order_to_picker(
                    context,
                    database.get_employee_by_telegram_id(7401, "picker"),
                )
            )

            self.assertEqual(context.bot.send_message.await_count, 1)
            sent_text = context.bot.send_message.await_args.kwargs["text"]
            self.assertIn("AUTO-1002", sent_text)

    def test_picker_notification_message_ids_are_tracked_for_stale_cleanup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/picker_notifications.db"
            database.init_database()

            database.create_employee_application(
                7301, "picker_a", "Малика", "Сафарова", "+9927301", "picker"
            )
            database.create_employee_application(
                7302, "picker_b", "Фарход", "Нуров", "+9927302", "picker"
            )

            connection = sqlite3.connect(database.DATABASE_PATH)
            connection.execute(
                "INSERT INTO orders (order_number, client_name, status) VALUES ('PICK-NOTIFY-1', 'Клиент', 'new')"
            )
            connection.commit()
            connection.close()

            self.assertTrue(database.log_new_order_notification(1, 1, message_id=101))
            self.assertTrue(database.log_new_order_notification(1, 2, message_id=202))

            other = database.get_picker_order_notification_messages(
                1,
                exclude_employee_id=1,
            )
            self.assertEqual(
                other,
                [{"employee_id": 2, "telegram_id": 7302, "message_id": 202}],
            )

if __name__ == "__main__":
    unittest.main()
