import asyncio
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("COURIER_BOT_TOKEN", "test-courier-bot-token")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "999999999")

import database
from handlers import orders
from handlers.menu import get_work_menu_compact, get_work_menu_expanded


class PickerUxTests(unittest.TestCase):
    def test_work_menu_is_compact(self):
        menu = get_work_menu_compact("picker", True, "ru")
        self.assertEqual(menu.keyboard, [["☰ Меню"]])

    def test_expanded_work_menu_keeps_actions(self):
        menu = get_work_menu_expanded("picker", True, "ru")
        labels = [button.text for row in menu.keyboard for button in row]
        self.assertIn("📦 Текущие заказы", labels)
        self.assertIn("🟢 Завершить смену", labels)

    def test_missing_item_selection_logs_exact_order_item(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/picker_ux.db"
            database.init_database()

            database.create_employee_application(
                7001, "picker_ux", "Иван", "Тестов", "+992900000001", "picker"
            )
            employee = database.update_application_status(1, "approved")
            database.toggle_employee_shift(employee["id"])

            connection = sqlite3.connect(database.DATABASE_PATH)
            cursor = connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, client_phone, delivery_address,
                    client_comment, payment_method, payment_amount, status, picker_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'assembling', ?)
                """,
                ("E2E-ITEM-001", "Клиент Тестов", "+992900000002", "Душанбе, ул. Тестовая 1", "", "cash", 120, employee["id"]),
            )
            order_id = cursor.lastrowid
            connection.execute(
                "INSERT INTO order_items (order_id, product_name, quantity) VALUES (?, ?, ?)",
                (order_id, "Молоко 3.2%, 1 л", 2),
            )
            connection.execute(
                "INSERT INTO order_items (order_id, product_name, quantity) VALUES (?, ?, ?)",
                (order_id, "Хлеб Бородинский", 1),
            )
            connection.commit()
            item_id = connection.execute(
                "SELECT id FROM order_items WHERE order_id = ? ORDER BY id ASC LIMIT 1",
                (order_id,),
            ).fetchone()[0]
            connection.close()

            query = SimpleNamespace(
                data=f"missing_item_select:{order_id}:{item_id}",
                from_user=SimpleNamespace(id=7001, language_code="ru"),
                answer=AsyncMock(),
                edit_message_text=AsyncMock(),
            )
            update = SimpleNamespace(
                callback_query=query,
                effective_user=query.from_user,
            )
            context = SimpleNamespace(bot_data={"role": "picker"}, user_data={})

            asyncio.run(orders.select_missing_item(update, context))

            query.answer.assert_awaited_once()
            query.edit_message_text.assert_awaited_once()
            connection = sqlite3.connect(database.DATABASE_PATH)
            event = connection.execute(
                "SELECT details FROM action_log WHERE order_id = ? AND action = 'missing_item' ORDER BY id DESC LIMIT 1",
                (order_id,),
            ).fetchone()
            connection.close()
            self.assertIsNotNone(event)
            self.assertIn("Молоко 3.2%, 1 л", event[0])


if __name__ == "__main__":
    unittest.main()
