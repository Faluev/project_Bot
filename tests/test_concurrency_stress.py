import gc
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed

os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("COURIER_BOT_TOKEN", "test-courier-bot-token")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "999999999")

import database


class ConcurrentOrderStressTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE_PATH = f"{self.temp_dir.name}/stress.db"
        database.init_database()

    def tearDown(self):
        # На Windows sqlite-файл нельзя удалить, пока соединение ещё держится
        # до финализации объекта. Принудительно собираем такие объекты перед
        # удалением временного каталога.
        gc.collect()
        self.temp_dir.cleanup()

    def _employee(self, telegram_id, role):
        transport = "car" if role == "courier" else None
        database.create_employee_application(
            telegram_id,
            f"user_{telegram_id}",
            "Тест",
            str(telegram_id),
            f"+{telegram_id}",
            role,
            transport,
        )
        database.update_application_status(
            self._last_employee_id(),
            "approved",
        )
        with sqlite3.connect(database.DATABASE_PATH) as connection:
            connection.execute(
                "UPDATE employees SET is_on_shift = 1 WHERE telegram_id = ?",
                (telegram_id,),
            )
        return self._employee_id(telegram_id, role)

    def _last_employee_id(self):
        with sqlite3.connect(database.DATABASE_PATH) as connection:
            row = connection.execute("SELECT MAX(id) FROM employees").fetchone()
            return row[0]

    def _employee_id(self, telegram_id, role):
        employee = database.get_employee_by_telegram_id(telegram_id, role)
        return employee["id"]

    def _insert_orders(self, count):
        with sqlite3.connect(database.DATABASE_PATH) as connection:
            for index in range(count):
                minute = index
                connection.execute(
                    """
                    INSERT INTO orders (
                        order_number, client_name, status, created_at, updated_at
                    ) VALUES (?, ?, 'new',
                              datetime('2026-09-29 10:00:00', ?),
                              datetime('2026-09-29 10:00:00', ?))
                    """,
                    (
                        f"STRESS-{index + 1:03d}",
                        f"Клиент {index + 1}",
                        f"+{minute} minutes",
                        f"+{minute} minutes",
                    ),
                )

    def test_many_pickers_racing_for_one_order_only_one_wins(self):
        picker_ids = [self._employee(9100 + i, "picker") for i in range(20)]
        self._insert_orders(1)

        def attempt(employee_id):
            return database.start_order_assembly(1, employee_id)

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(attempt, picker_ids))

        winners = [result for result in results if result["success"]]
        self.assertEqual(len(winners), 1)
        self.assertEqual(database.get_order_by_id(1)["status"], "assembling")

    def test_many_couriers_racing_for_one_order_only_one_wins(self):
        courier_ids = [self._employee(9200 + i, "courier") for i in range(20)]
        self._insert_orders(1)

        with sqlite3.connect(database.DATABASE_PATH) as connection:
            connection.execute(
                "UPDATE orders SET status = 'awaiting_courier' WHERE id = 1"
            )

        def attempt(employee_id):
            return database.pickup_order(1, employee_id)

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(attempt, courier_ids))

        winners = [result for result in results if result["success"]]
        self.assertEqual(len(winners), 1)
        self.assertEqual(database.get_order_by_id(1)["status"], "in_delivery")

    def test_ten_pickers_can_take_ten_distinct_oldest_orders_concurrently(self):
        picker_ids = [self._employee(9300 + i, "picker") for i in range(10)]
        self._insert_orders(20)

        def attempt(employee_id):
            return database.start_order_assembly(1, employee_id)

        # Each picker attempts the current oldest order. The DB transaction
        # must serialize the claim so that a successful claim advances FIFO.
        # Retry each worker a few times because a transient loser may observe
        # a newer oldest order after another transaction commits.
        def claim(employee_id):
            for _ in range(5):
                for order_id in range(1, 21):
                    result = database.start_order_assembly(order_id, employee_id)
                    if result["success"]:
                        return order_id
            return None

        with ThreadPoolExecutor(max_workers=10) as pool:
            claimed = list(pool.map(claim, picker_ids))

        claimed = [order_id for order_id in claimed if order_id is not None]
        self.assertEqual(len(claimed), 10)
        self.assertEqual(len(set(claimed)), 10)

        with sqlite3.connect(database.DATABASE_PATH) as connection:
            rows = connection.execute(
                "SELECT id, status FROM orders ORDER BY id"
            ).fetchall()

        assembling = [order_id for order_id, status in rows if status == "assembling"]
        new = [order_id for order_id, status in rows if status == "new"]

        self.assertEqual(assembling, list(range(1, 11)))
        self.assertEqual(new, list(range(11, 21)))

    def _prepare_two_active_courier_orders(self):
        courier_id = self._employee(9401, "courier")
        self._insert_orders(2)

        with sqlite3.connect(database.DATABASE_PATH) as connection:
            connection.execute(
                """
                UPDATE orders
                SET status = 'awaiting_courier'
                WHERE id IN (1, 2)
                """
            )

        self.assertTrue(database.pickup_order(1, courier_id)["success"])
        self.assertTrue(database.pickup_order(2, courier_id)["success"])
        return courier_id

    def test_courier_cannot_deliver_newer_order_when_older_is_also_active(self):
        courier_id = self._prepare_two_active_courier_orders()

        newer = database.deliver_order(2, courier_id)
        self.assertFalse(newer["success"])
        self.assertEqual(newer["reason"], "not_oldest")
        self.assertEqual(newer["priority_order_id"], 1)
        self.assertEqual(newer["priority_order_number"], "STRESS-001")

        older = database.deliver_order(1, courier_id)
        self.assertTrue(older["success"])

        newer_after_older = database.deliver_order(2, courier_id)
        self.assertTrue(newer_after_older["success"])

        statuses = {
            order_id: database.get_order_by_id(order_id)["status"]
            for order_id in (1, 2)
        }
        self.assertEqual(statuses, {1: "delivered", 2: "delivered"})

    def test_concurrent_delivery_preserves_oldest_first(self):
        courier_id = self._prepare_two_active_courier_orders()

        def deliver(order_id):
            return database.deliver_order(order_id, courier_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                order_id: pool.submit(deliver, order_id)
                for order_id in (1, 2)
            }
            results = {
                order_id: future.result()
                for order_id, future in futures.items()
            }

        statuses = {
            order_id: database.get_order_by_id(order_id)["status"]
            for order_id in (1, 2)
        }

        # Допускаются оба корректных исхода гонки:
        # 1) старый заказ захватывает транзакцию первым -> оба доставлены;
        # 2) новый пытается первым -> он получает not_oldest, старый доставляется.
        if results[2]["success"]:
            self.assertTrue(results[1]["success"])
            self.assertEqual(statuses, {1: "delivered", 2: "delivered"})

            with sqlite3.connect(database.DATABASE_PATH) as connection:
                rows = connection.execute(
                    """
                    SELECT order_id
                    FROM action_log
                    WHERE action = 'deliver_order'
                    ORDER BY id ASC
                    """
                ).fetchall()

            self.assertEqual([row[0] for row in rows], [1, 2])
        else:
            self.assertEqual(results[2]["reason"], "not_oldest")
            self.assertTrue(results[1]["success"])
            self.assertEqual(statuses, {1: "delivered", 2: "in_delivery"})


if __name__ == "__main__":
    unittest.main()
