"""A fake Telegram Bot API behind python-telegram-bot's HTTP layer, so tests run
the real PTB stack. Each token is a bot; calls are recorded per bot."""
import html
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from telegram.request import BaseRequest

URL = re.compile(r"/bot(?P<token>[^/]+)/(?P<method>\w+)$")


@dataclass
class FakeBotState:
    id: int
    username: str
    revoked: bool = False
    webhook_url: str = ""
    webhook_secret: Optional[str] = None
    commands: list = field(default_factory=list)
    pending_update_count: int = 0
    last_error_date: Optional[int] = None
    last_error_message: Optional[str] = None
    sent: list[dict] = field(default_factory=list)  # sendMessage params, plus the assigned message_id
    edits: list[dict] = field(default_factory=list)  # editMessageText params
    answers: list[dict] = field(default_factory=list)  # answerCallbackQuery params


class FakeTelegram:
    def __init__(self):
        self.bots: dict[str, FakeBotState] = {}
        self.down = False  # simulate Telegram being unreachable
        self._message_id = 100

    def add_bot(self, token: str, bot_id: int, username: str) -> FakeBotState:
        self.bots[token] = FakeBotState(bot_id, username)
        return self.bots[token]

    def by_id(self, bot_id: int) -> FakeBotState:
        return next(b for b in self.bots.values() if b.id == bot_id)

    def request(self) -> "FakeRequest":
        return FakeRequest(self)

    def handle(self, token: str, method: str, params: dict[str, Any]) -> tuple[int, dict]:
        bot = self.bots.get(token)
        if bot is None or bot.revoked:
            return 401, {"ok": False, "error_code": 401, "description": "Unauthorized"}
        result: Any = True
        if method == "getMe":
            result = {"id": bot.id, "is_bot": True, "first_name": bot.username, "username": bot.username}
        elif method == "setWebhook":
            bot.webhook_url, bot.webhook_secret = params["url"], params.get("secret_token")
        elif method == "deleteWebhook":
            bot.webhook_url, bot.webhook_secret = "", None
        elif method == "getWebhookInfo":
            result = {"url": bot.webhook_url, "has_custom_certificate": False, "pending_update_count": bot.pending_update_count}
            if bot.last_error_date:
                result |= {"last_error_date": bot.last_error_date, "last_error_message": bot.last_error_message}
        elif method == "setMyCommands":
            bot.commands = params["commands"]
        elif method == "answerCallbackQuery":
            bot.answers.append(params)
        elif method == "editMessageText":
            bot.edits.append(params)
            result = {
                "message_id": int(params["message_id"]),
                "date": int(time.time()),
                "chat": {"id": int(params["chat_id"]), "type": "private"},
                "text": params["text"],
            }
        elif method == "sendMessage":
            self._message_id += 1
            bot.sent.append({**params, "message_id": self._message_id})
            chat_id = int(params["chat_id"])
            result = {
                "message_id": self._message_id,
                "date": int(time.time()),
                "chat": {"id": chat_id, "type": "private"},
                "text": params["text"],
            }
        return 200, {"ok": True, "result": result}


class FakeRequest(BaseRequest):
    def __init__(self, telegram: FakeTelegram):
        self.telegram = telegram

    @property
    def read_timeout(self):
        return 5

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **_):
        if self.telegram.down:
            from telegram.error import NetworkError

            raise NetworkError("Network is unreachable")
        m = URL.search(url)
        params = {}
        if request_data is not None:
            params = dict(request_data.parameters)
        code, body = self.telegram.handle(m["token"], m["method"], params)
        return code, json.dumps(body).encode()


def private_message(text: str, user_id: int, update_id: int = 1, username: Optional[str] = None) -> dict:
    """A Telegram Update JSON for a private-chat command."""
    entities = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else []
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": int(time.time()),
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": f"U{user_id}", **({"username": username} if username else {})},
            "text": text,
            "entities": entities,
        },
    }


def button_press(data: str, user_id: int, message: dict, update_id: int = 50) -> dict:
    """A callback_query Update for pressing an inline button on a bot message
    (`message` is an entry from FakeBotState.sent)."""
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cbq{update_id}",
            "chat_instance": "ci",
            "from": {"id": user_id, "is_bot": False, "first_name": f"U{user_id}"},
            "data": data,
            "message": {
                "message_id": message["message_id"],
                "date": int(time.time()),
                "chat": {"id": int(message["chat_id"]), "type": "private"},
                # Telegram hands back plain text (formatting lives in entities).
                "text": html.unescape(re.sub(r"<[^>]+>", "", message["text"])),
            },
        },
    }


def buttons(message: dict) -> list[str]:
    """callback_data of every inline button on a sent message."""
    markup = message.get("reply_markup") or {}
    return [b.get("callback_data") for row in markup.get("inline_keyboard", []) for b in row]
