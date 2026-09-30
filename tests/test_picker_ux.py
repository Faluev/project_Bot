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
        labels = [button.text for row in menu.keyboard for button in row]
        self.assertEqual(labels, ["☰ Меню"])

    def test_expanded_work_menu_keeps_actions(self):
        menu = get_work_menu_expanded("picker", True, "ru")
        labels = [button.text for row in menu.keyboard for button in row]
        self.assertIn("📦 Текущие заказы", labels)
        self.assertIn("🟢 Завершить смену", labels)

    def test_real_e2e_orders_have_chronological_creation_times(self):
        from scripts.seed_e2e_orders import seed

        original_path = database.DATABASE_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            database.DATABASE_PATH = f"{temp_dir}/seed_orders.db"
            try:
                seed(reset=True)

                connection = sqlite3.connect(database.DATABASE_PATH)
                rows = connection.execute(
                    "SELECT order_number, created_at FROM orders WHERE order_number LIKE 'E2E-REAL-%' ORDER BY created_at ASC"
                ).fetchall()
                connection.close()

                self.assertEqual(len(rows), 10)
                self.assertEqual(rows[0][0], "E2E-REAL-001")
                self.assertEqual(rows[-1][0], "E2E-REAL-010")

                timestamps = [row[1] for row in rows]
                self.assertEqual(timestamps, sorted(timestamps))
                self.assertNotEqual(timestamps[0], timestamps[-1])

                from handlers.i18n import get_message
                rendered = get_message(
                    "ru", "picker_order", order_number=rows[0][0], created_at=rows[0][1],
                    client="Мадина Саидова", phone="+992 90 100 1001",
                    address="Душанбе, ул. Рудаки, 105, кв. 24",
                    items="• Молоко 3.2%, 1 л — 2 шт.", comment="",
                    payment="Наличные", amount=186.50,
                )
                self.assertIn(f"🕒 Оформлен: {rows[0][1]}", rendered)
            finally:
                database.DATABASE_PATH = original_path

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
            self.assertIn("missing_item_qty:", query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data)

            quantity_query = SimpleNamespace(
                data=f"missing_item_qty:{order_id}:{item_id}:1",
                from_user=SimpleNamespace(id=7001, language_code="ru"),
                answer=AsyncMock(),
                edit_message_text=AsyncMock(),
            )
            quantity_update = SimpleNamespace(
                callback_query=quantity_query,
                effective_user=quantity_query.from_user,
            )

            asyncio.run(orders.confirm_missing_item_quantity(quantity_update, context))

            connection = sqlite3.connect(database.DATABASE_PATH)
            row = connection.execute(
                "SELECT quantity, missing_quantity FROM order_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            event = connection.execute(
                "SELECT details FROM action_log WHERE order_id = ? AND action = 'missing_item' ORDER BY id DESC LIMIT 1",
                (order_id,),
            ).fetchone()
            connection.close()

            self.assertEqual(row, (2, 1))
            self.assertIsNotNone(event)
            self.assertIn("Молоко 3.2%, 1 л", event[0])
            self.assertIn("отсутствует: 1 шт.", event[0])

            # После частичного отсутствия в выборе остаётся только остаток.
            menu_query = SimpleNamespace(
                data=f"missing_item:{order_id}",
                from_user=SimpleNamespace(id=7001, language_code="ru"),
                answer=AsyncMock(),
                edit_message_text=AsyncMock(),
            )
            menu_update = SimpleNamespace(
                callback_query=menu_query,
                effective_user=menu_query.from_user,
            )
            asyncio.run(orders.handle_missing_item(menu_update, context))
            rendered = menu_query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
            self.assertIn("Молоко 3.2%, 1 л", rendered[0][0].text)
            self.assertIn("1 шт.", rendered[0][0].text)

            # Полностью отсутствующую позицию в следующем выборе уже не показываем.
            full_item_id = sqlite3.connect(database.DATABASE_PATH).execute(
                "SELECT id FROM order_items WHERE order_id = ? AND product_name = ?",
                (order_id, "Хлеб Бородинский"),
            ).fetchone()[0]
            full_query = SimpleNamespace(
                data=f"missing_item_select:{order_id}:{full_item_id}",
                from_user=SimpleNamespace(id=7001, language_code="ru"),
                answer=AsyncMock(),
                edit_message_text=AsyncMock(),
            )
            full_update = SimpleNamespace(
                callback_query=full_query,
                effective_user=full_query.from_user,
            )
            asyncio.run(orders.select_missing_item(full_update, context))
            quantity_buttons = full_query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
            self.assertTrue(quantity_buttons)

            # «Назад к заказу» реально восстанавливает карточку.
            back_query = SimpleNamespace(
                data=f"missing_item_back:{order_id}",
                from_user=SimpleNamespace(id=7001, language_code="ru"),
                answer=AsyncMock(),
                edit_message_text=AsyncMock(),
            )
            back_update = SimpleNamespace(
                callback_query=back_query,
                effective_user=back_query.from_user,
            )
            asyncio.run(orders.back_from_missing_item_selection(back_update, context))
            back_query.answer.assert_awaited_once()
            back_query.edit_message_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
