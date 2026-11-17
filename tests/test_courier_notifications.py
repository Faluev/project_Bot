import asyncio
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import BadRequest

os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("COURIER_BOT_TOKEN", "test-courier-bot-token")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "999999999")

import database
from handlers import courier_orders


class CourierNotificationTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database, "DATABASE_PATH", f"{directory}/test.db"))
        database.init_database()
        database.create_employee_application(
            8101, "courier", "Test", "Courier", "+8101", "courier", "bike"
        )
        database.update_application_status(1, "approved")
        self.execute("UPDATE employees SET is_on_shift = 1 WHERE id = 1")
        self.execute(
            "INSERT INTO orders (order_number, status) VALUES ('NOTIFY-1', 'awaiting_courier')"
        )

    def execute(self, sql, params=()):
        with closing(database.get_connection()) as connection, connection:
            return connection.execute(sql, params).fetchall()

    def complete_send(self, message_id):
        [(order, token)] = database.claim_courier_order_notifications(1)
        self.assertTrue(database.log_courier_order_notification(
            order["id"], 1, message_id, claim_token=token
        ))

    def test_timeout_requeue_allows_one_more_send(self):
        # Keep an existing notification from before the upgrade.
        self.assertTrue(database.log_courier_order_notification(1, 1, 100))
        self.execute(
            """UPDATE orders
               SET status = 'in_delivery', courier_id = 1, updated_at = '2000-01-01 00:00:00'
               WHERE id = 1"""
        )
        events = database.process_order_timeouts(15)
        self.assertEqual([event["action"] for event in events], ["timeout_requeue"])
        self.complete_send(101)
        self.assertEqual(database.claim_courier_order_notifications(1), [])

    def test_admin_retry_delivery_allows_one_more_send(self):
        self.complete_send(100)
        self.execute("UPDATE orders SET status = 'rejected', courier_id = 1 WHERE id = 1")
        self.assertTrue(database.resolve_rejected_order(1, "return_to_stock")["success"])
        self.assertTrue(database.retry_rejected_order(1)["success"])
        self.complete_send(101)
        self.assertEqual(database.claim_courier_order_notifications(1), [])

    def test_concurrent_connections_claim_only_one_send(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(database.claim_courier_order_notifications, [1] * 8))
        self.assertEqual(sum(map(len, results)), 1)

    def test_expired_reservation_can_be_replaced_and_old_sender_cannot_finish(self):
        [(_, old_token)] = database.claim_courier_order_notifications(1)
        self.execute("UPDATE courier_notification_claims SET expires_at = 0")
        [(_, new_token)] = database.claim_courier_order_notifications(1)
        self.assertNotEqual(old_token, new_token)
        self.assertFalse(database.log_courier_order_notification(
            1, 1, 100, claim_token=old_token
        ))
        self.assertTrue(database.log_courier_order_notification(
            1, 1, 101, claim_token=new_token
        ))

    def test_old_round_cannot_log_into_a_new_round(self):
        [(_, old_token)] = database.claim_courier_order_notifications(1)
        self.execute(
            """INSERT INTO action_log (order_id, action, old_status, new_status)
               VALUES (1, 'retry_delivery', 'rejected', 'awaiting_courier')"""
        )
        self.assertFalse(database.log_courier_order_notification(
            1, 1, 100, claim_token=old_token
        ))
        self.complete_send(101)

    def test_overlapping_polls_send_only_one_message(self):
        async def poll():
            started = asyncio.Event()
            finish = asyncio.Event()

            async def send(**kwargs):
                started.set()
                await finish.wait()
                return SimpleNamespace(message_id=201)

            bot = SimpleNamespace(
                token=courier_orders.COURIER_BOT_TOKEN,
                send_message=AsyncMock(side_effect=send),
            )
            context = SimpleNamespace(bot=bot)
            first_poll = asyncio.create_task(
                courier_orders.notify_couriers_about_waiting_orders(context)
            )
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                await asyncio.wait_for(
                    courier_orders.notify_couriers_about_waiting_orders(context), timeout=2
                )
            finally:
                finish.set()
                await first_poll
            self.assertEqual(bot.send_message.await_count, 1)

        asyncio.run(poll())

    def test_failed_send_releases_reservation_for_next_poll(self):
        bot = SimpleNamespace(
            token=courier_orders.COURIER_BOT_TOKEN,
            send_message=AsyncMock(side_effect=[BadRequest("not sent"), SimpleNamespace(message_id=201)]),
        )
        context = SimpleNamespace(bot=bot)

        async def poll():
            with self.assertLogs(courier_orders.logger, level="ERROR"):
                await courier_orders.notify_couriers_about_waiting_orders(context)
            await courier_orders.notify_couriers_about_waiting_orders(context)
            await courier_orders.notify_couriers_about_waiting_orders(context)

        asyncio.run(poll())
        self.assertEqual(bot.send_message.await_count, 2)
        messages = database.get_courier_order_notification_messages(1)
        self.assertEqual([message["message_id"] for message in messages], [201])
