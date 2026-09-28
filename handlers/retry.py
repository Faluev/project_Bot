import asyncio

from telegram.error import NetworkError, RetryAfter, TimedOut


async def send_message_with_retry(
    bot,
    chat_id,
    text,
    reply_markup=None,
    attempts=3,
):
    """Повторяет отправку сообщения только при временных сетевых ошибках."""
    last_error = None

    for attempt in range(attempts):
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
            )
        except RetryAfter as error:
            last_error = error
            if attempt < attempts - 1:
                await asyncio.sleep(error.retry_after)
        except (NetworkError, TimedOut) as error:
            last_error = error
            if attempt < attempts - 1:
                await asyncio.sleep(1)

    raise last_error
