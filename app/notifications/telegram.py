"""python-telegram-bot adapter for the worker's send seam.

    send = telegram_sender(bot_manager.get_bot)   # B1: workspace_id -> telegram.Bot | None
    worker = OutboxWorker(SessionLocal, send)
"""
import uuid
from datetime import timedelta
from typing import Awaitable, Callable, Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import (
    BadRequest,
    ChatMigrated,
    Forbidden,
    InvalidToken,
    NetworkError,
    RetryAfter,
    TelegramError,
)

from .errors import (
    BotUnavailable,
    PermanentSendError,
    RateLimited,
    RecipientBlocked,
    SendError,
    TransientSendError,
)
from .messages import OutgoingMessage, SendFn

GetBot = Callable[[uuid.UUID], Awaitable[Optional[Bot]]]


def telegram_sender(get_bot: GetBot) -> SendFn:
    async def send(message: OutgoingMessage) -> int:
        bot = await get_bot(message.workspace_id)
        if bot is None:
            raise BotUnavailable(f"no live bot for workspace {message.workspace_id}")
        markup = None
        if message.buttons:
            markup = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(b.text, callback_data=b.callback_data, url=b.url) for b in row]
                    for row in message.buttons
                ]
            )
        try:
            sent = await bot.send_message(
                chat_id=message.chat_id,
                text=message.text,
                parse_mode=message.parse_mode,
                reply_markup=markup,
            )
        except TelegramError as e:
            raise classify_telegram_error(e) from e
        return sent.message_id

    return send


def classify_telegram_error(error: TelegramError) -> SendError:
    """Map a PTB exception to the outcome the worker acts on."""
    # Order matters: BadRequest and the others subclass NetworkError.
    if isinstance(error, RetryAfter):
        retry_after = error.retry_after
        seconds = retry_after.total_seconds() if isinstance(retry_after, timedelta) else float(retry_after)
        return RateLimited(seconds)
    if isinstance(error, InvalidToken):
        return BotUnavailable(f"bot token rejected: {error.message}")
    if isinstance(error, Forbidden):
        return RecipientBlocked(error.message)
    if isinstance(error, (BadRequest, ChatMigrated)):
        return PermanentSendError(error.message)
    if isinstance(error, NetworkError):  # includes TimedOut
        return TransientSendError(error.message)
    return TransientSendError(str(error))
