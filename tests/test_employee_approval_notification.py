import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("COURIER_BOT_TOKEN", "test-courier-bot-token")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "999999999")

from handlers.admin import notify_employee_application_result


class EmployeeApprovalNotificationTests(unittest.TestCase):

    def test_approved_picker_receives_notification_with_work_menu(self):
        context = SimpleNamespace(
            bot=SimpleNamespace()
        )
        employee = {
            "role": "picker",
            "application_status": "approved",
            "is_on_shift": 0,
            "language": "ru",
            "telegram_id": 123456,
        }

        with patch(
            "handlers.admin.send_message_with_retry",
            new=AsyncMock(),
        ) as send_message:
            asyncio.run(
                notify_employee_application_result(
                    context,
                    employee,
                    "Заявка одобрена",
                )
            )

        send_message.assert_awaited_once()
        kwargs = send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 123456)
        self.assertEqual(kwargs["text"], "Заявка одобрена")
        self.assertIsNotNone(kwargs["reply_markup"])


if __name__ == "__main__":
    unittest.main()
