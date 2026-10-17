import asyncio
import uuid
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, Forbidden, InvalidToken, NetworkError, RetryAfter, TimedOut

from app.notifications import (
    BotUnavailable,
    Button,
    OutgoingMessage,
    PermanentSendError,
    RateLimited,
    RecipientBlocked,
    TransientSendError,
)
from app.notifications.telegram import classify_telegram_error, telegram_sender


@pytest.mark.parametrize(
    "error, expected",
    [
        (RetryAfter(7), RateLimited),
        (InvalidToken("Unauthorized"), BotUnavailable),
        (Forbidden("Forbidden: bot was blocked by the user"), RecipientBlocked),
        (BadRequest("Bad Request: chat not found"), PermanentSendError),
        (TimedOut(), TransientSendError),
        (NetworkError("Bad Gateway"), TransientSendError),
    ],
)
def test_classify(error, expected):
    assert isinstance(classify_telegram_error(error), expected)


def test_retry_after_seconds():
    assert classify_telegram_error(RetryAfter(7)).retry_after == 7


class FakeBot:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(message_id=42)


WS = uuid.uuid4()
MSG = OutgoingMessage(WS, 99, "<b>Task</b>", ((Button("Done", callback_data="done:1"), Button("Open", url="https://x")),))


def test_sender_builds_keyboard_and_returns_message_id():
    bot = FakeBot()

    async def get_bot(workspace_id):
        assert workspace_id == WS
        return bot

    assert asyncio.run(telegram_sender(get_bot)(MSG)) == 42
    (call,) = bot.calls
    assert call["chat_id"] == 99 and call["parse_mode"] == "HTML"
    (row,) = call["reply_markup"].inline_keyboard
    assert [b.callback_data for b in row] == ["done:1", None] and row[1].url == "https://x"


def test_sender_without_bot_raises_bot_unavailable():
    async def get_bot(workspace_id):
        return None

    with pytest.raises(BotUnavailable):
        asyncio.run(telegram_sender(get_bot)(MSG))


def test_sender_translates_telegram_errors():
    async def get_bot(workspace_id):
        return FakeBot(Forbidden("blocked"))

    with pytest.raises(RecipientBlocked):
        asyncio.run(telegram_sender(get_bot)(MSG))
