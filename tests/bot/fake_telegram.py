"""A fake Telegram Bot API behind python-telegram-bot's HTTP layer, so tests run
the real PTB stack. Each token is a bot; calls are recorded per bot."""
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
    sent: list[dict] = field(default_factory=list)  # sendMessage params


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
            result = {"url": bot.webhook_url, "has_custom_certificate": False, "pending_update_count": 0}
        elif method == "setMyCommands":
            bot.commands = params["commands"]
        elif method == "sendMessage":
            bot.sent.append(params)
            self._message_id += 1
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


def private_message(text: str, user_id: int, update_id: int = 1) -> dict:
    """A Telegram Update JSON for a private-chat command."""
    entities = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else []
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": int(time.time()),
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": f"U{user_id}"},
            "text": text,
            "entities": entities,
        },
    }
