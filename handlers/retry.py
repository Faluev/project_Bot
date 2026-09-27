import asyncio


async def send_message_with_retry(
    bot,
    chat_id,
    text,
    reply_markup=None,
    attempts=3,
):
    """Повторяет отправку сообщения при временной ошибке Telegram/сети."""
    last_error = None

    for attempt in range(attempts):
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
            )
        except Exception as error:
            last_error = error
            if attempt < attempts - 1:
                await asyncio.sleep(1)

    raise last_error
