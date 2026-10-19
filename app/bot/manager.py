"""BotManager: one python-telegram-bot Application per connected workspace bot.

Telegram posts every bot's updates to POST /telegram/{telegram_bot_id} with that
bot's secret header. The manager checks the secret and hands the update to the
bot's Application, whose bot_data["workspace_id"] scopes every handler to one
tenant. Outgoing DMs go through send(), the B4 outbox worker's SendFn.

Applications are only initialized, not started: updates arrive by webhook and
are processed inline with Application.process_update().
"""
import asyncio
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from telegram import Bot, BotCommand, Update
from telegram.error import Forbidden, InvalidToken, NetworkError, TelegramError
from telegram.ext import Application
from telegram.request import BaseRequest

from app import db
from app.auth import telegram_secret_ok
from app.models import BotStatus, WorkspaceBot
from app.notifications import BotUnavailable, OutgoingMessage, wake_workspace
from app.notifications.telegram import telegram_sender

from . import handlers
from .crypto import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

ALLOWED_UPDATES = ["message", "callback_query"]


class BotError(Exception):
    pass


class InvalidBotToken(BotError):
    """Telegram rejected the token (getMe 401)."""


class BotInUse(BotError):
    """The bot is live in another workspace."""


class TelegramUnavailable(BotError):
    """Telegram didn't answer (network error, timeout, 5xx)."""


class UnknownBot(BotError):
    pass


class BadSecret(BotError):
    pass


@dataclass
class _LiveBot:
    workspace_id: uuid.UUID
    telegram_bot_id: int
    username: str
    webhook_secret: str
    application: Application


@dataclass(frozen=True)
class BotInfo:
    """What GET /workspaces/{id}/bot reports. `webhook` is live from getWebhookInfo."""

    status: str  # not_connected | active | invalid | disconnected
    username: Optional[str] = None
    telegram_bot_id: Optional[int] = None
    connected_at: Optional[datetime] = None
    last_error: Optional[str] = None
    webhook: Optional[dict[str, Any]] = None


RequestFactory = Callable[[], BaseRequest]


class BotManager:
    def __init__(
        self,
        *,
        public_base_url: str = "",
        session_factory: Optional[sessionmaker[Session]] = None,
        request_factory: Optional[RequestFactory] = None,
        on_status_change: Optional[Callable[..., Any]] = None,
    ):
        self.public_base_url = public_base_url.rstrip("/")
        self._session_factory = session_factory or db.SessionLocal
        # Tests pass a fake HTTP layer; production uses PTB's default httpx client.
        self._request_factory = request_factory
        # Awaited with (workspace_id, BotInfo, actor Member | None) after a bot is
        # connected, disconnected or marked invalid; main.py emits realtime bot.updated.
        self._on_status_change = on_status_change
        self._bots: dict[int, _LiveBot] = {}
        self._by_workspace: dict[uuid.UUID, int] = {}
        self._lock = asyncio.Lock()

    # --- lifecycle ---

    async def start(self) -> None:
        """Load every active bot. A revoked token marks that bot invalid."""
        rows = await asyncio.to_thread(self._active_rows)
        for row in rows:
            try:
                token = decrypt_token(row.token_encrypted)
                live = await self._launch(row.workspace_id, row.telegram_bot_id, row.username, token, row.webhook_secret)
            except InvalidToken:
                await self._mark_invalid(row.workspace_id, "bot token was revoked")
                continue
            except Exception:
                logger.exception("Bot %s failed to start", row.telegram_bot_id, extra={"workspace_id": str(row.workspace_id)})
                continue
            await self._ensure_webhook(live)
        logger.info("BotManager started with %s bot(s)", len(self._bots))

    async def stop(self) -> None:
        for live in list(self._bots.values()):
            await self._shutdown(live)
        self._bots.clear()
        self._by_workspace.clear()

    # --- connect / disconnect / status (behind PUT/DELETE/GET /workspaces/{id}/bot) ---

    async def connect(self, workspace_id: uuid.UUID, token: str, *, actor=None) -> BotInfo:
        """Validate, encrypt and store the token, then register the webhook and
        commands. Replaces the workspace's current bot."""
        token = token.strip()
        token_encrypted = encrypt_token(token)  # fails fast (TokenKeyMissing) before touching Telegram
        probe = self._bot(token)
        try:
            async with probe:
                me = await probe.get_me()
                in_use = await asyncio.to_thread(self._live_elsewhere, me.id, workspace_id)
                if in_use:
                    raise BotInUse(f"@{me.username} is connected to another workspace")
                secret = secrets.token_urlsafe(32)
                await self._set_webhook(probe, me.id, secret)
                await probe.set_my_commands([BotCommand(c, d) for c, d in handlers.COMMANDS])
        except InvalidToken as e:
            raise InvalidBotToken("Telegram rejected the bot token") from e
        except NetworkError as e:  # includes TimedOut; BadRequest here means a bad webhook URL
            raise TelegramUnavailable(str(e)) from e

        async with self._lock:
            previous = self._by_workspace.get(workspace_id)
            try:
                await asyncio.to_thread(self._store, workspace_id, me.id, me.username, token_encrypted, secret)
            except IntegrityError as e:  # raced another workspace for the same bot
                async with self._bot(token) as bot:
                    await self._delete_webhook(bot)
                raise BotInUse(f"@{me.username} is connected to another workspace") from e
            if previous is not None and previous in self._bots:
                old = self._bots[previous]
                if previous != me.id:
                    await self._delete_webhook(old.application.bot)
                await self._shutdown(old)
            await self._launch(workspace_id, me.id, me.username, token, secret)
        info = await self.status(workspace_id, live_webhook=False)
        await self._changed(workspace_id, info, actor)
        return info

    async def disconnect(self, workspace_id: uuid.UUID, *, actor=None) -> None:
        async with self._lock:
            bot_id = self._by_workspace.get(workspace_id)
            live = self._bots.get(bot_id) if bot_id is not None else None
            if live is not None:
                await self._delete_webhook(live.application.bot)
                await self._shutdown(live)
            changed = await asyncio.to_thread(self._set_status, workspace_id, BotStatus.DISCONNECTED, None)
        if changed:
            await self._changed(workspace_id, await self.status(workspace_id, live_webhook=False), actor)

    async def status(self, workspace_id: uuid.UUID, *, live_webhook: bool = True) -> BotInfo:
        row = await asyncio.to_thread(self._row, workspace_id)
        if row is None:
            return BotInfo(status="not_connected")
        info = dict(
            status=row.status.value,
            username=row.username,
            telegram_bot_id=row.telegram_bot_id,
            connected_at=row.connected_at,
            last_error=row.last_error,
        )
        live = self._live(workspace_id)
        if live_webhook and live is not None and row.status == BotStatus.ACTIVE:
            info["webhook"] = await self._webhook_health(live)
        return BotInfo(**info)

    # --- runtime ---

    async def process_update(self, telegram_bot_id: int, secret: Optional[str], data: dict) -> None:
        live = self._bots.get(telegram_bot_id)
        if live is None:
            raise UnknownBot(str(telegram_bot_id))
        if not telegram_secret_ok(secret, live.webhook_secret):
            raise BadSecret(str(telegram_bot_id))
        update_ = Update.de_json(data, live.application.bot)
        await live.application.process_update(update_)

    async def get_bot(self, workspace_id: uuid.UUID) -> Optional[Bot]:
        live = self._live(workspace_id)
        return live.application.bot if live else None

    async def send(self, message: OutgoingMessage) -> int:
        """SendFn for the outbox worker. A revoked token marks the bot invalid."""
        try:
            return await self._sender(message)
        except BotUnavailable as e:
            if isinstance(e.__cause__, InvalidToken):
                await self._mark_invalid(message.workspace_id, "bot token was revoked")
            raise

    @property
    def _sender(self):
        return telegram_sender(self.get_bot)

    def workspace_for(self, telegram_bot_id: int) -> Optional[uuid.UUID]:
        live = self._bots.get(telegram_bot_id)
        return live.workspace_id if live else None

    # --- internals ---

    def _bot(self, token: str) -> Bot:
        if self._request_factory is None:
            return Bot(token)
        return Bot(token, request=self._request_factory(), get_updates_request=self._request_factory())

    def _live(self, workspace_id: uuid.UUID) -> Optional[_LiveBot]:
        bot_id = self._by_workspace.get(workspace_id)
        return self._bots.get(bot_id) if bot_id is not None else None

    async def _launch(self, workspace_id, telegram_bot_id, username, token, secret) -> _LiveBot:
        builder = Application.builder().token(token).updater(None)
        if self._request_factory is not None:
            builder = builder.request(self._request_factory()).get_updates_request(self._request_factory())
        application = builder.build()
        application.bot_data["workspace_id"] = workspace_id
        handlers.register(application)
        application.add_error_handler(_log_handler_error)
        await application.initialize()  # getMe: raises InvalidToken for a revoked token
        live = _LiveBot(workspace_id, telegram_bot_id, username, secret, application)
        self._bots[telegram_bot_id] = live
        self._by_workspace[workspace_id] = telegram_bot_id
        return live

    async def _shutdown(self, live: _LiveBot) -> None:
        self._bots.pop(live.telegram_bot_id, None)
        if self._by_workspace.get(live.workspace_id) == live.telegram_bot_id:
            del self._by_workspace[live.workspace_id]
        try:
            await live.application.shutdown()
        except Exception:
            logger.exception("Shutting down bot %s failed", live.telegram_bot_id)

    def _webhook_url(self, telegram_bot_id: int) -> str:
        return f"{self.public_base_url}/telegram/{telegram_bot_id}"

    async def _set_webhook(self, bot: Bot, telegram_bot_id: int, secret: str) -> None:
        if not self.public_base_url:
            logger.warning("PUBLIC_BASE_URL is not set; bot %s won't receive updates", telegram_bot_id)
            return
        await bot.set_webhook(
            url=self._webhook_url(telegram_bot_id), secret_token=secret, allowed_updates=ALLOWED_UPDATES
        )

    async def _ensure_webhook(self, live: _LiveBot) -> None:
        """Re-register after PUBLIC_BASE_URL changed (e.g. a new ngrok URL)."""
        if not self.public_base_url:
            return
        try:
            info = await live.application.bot.get_webhook_info()
            if info.url != self._webhook_url(live.telegram_bot_id):
                await self._set_webhook(live.application.bot, live.telegram_bot_id, live.webhook_secret)
                logger.info("Re-registered webhook for bot %s", live.telegram_bot_id)
        except TelegramError:
            logger.exception("Checking webhook for bot %s failed", live.telegram_bot_id)

    async def _delete_webhook(self, bot: Bot) -> None:
        try:
            await bot.delete_webhook()
        except TelegramError:
            logger.warning("deleteWebhook failed; the old bot may keep posting until its secret stops matching")

    async def _webhook_health(self, live: _LiveBot) -> Optional[dict[str, Any]]:
        try:
            info = await live.application.bot.get_webhook_info()
        except TelegramError:
            return None
        return {
            "url_ok": bool(self.public_base_url) and info.url == self._webhook_url(live.telegram_bot_id),
            "pending_update_count": info.pending_update_count,
            "last_error_at": info.last_error_date,
            "last_error_message": info.last_error_message,
        }

    async def _mark_invalid(self, workspace_id: uuid.UUID, reason: str) -> None:
        live = self._live(workspace_id)
        if live is not None:
            await self._shutdown(live)
        if await asyncio.to_thread(self._set_status, workspace_id, BotStatus.INVALID, reason):
            logger.warning("Bot marked invalid: %s", reason, extra={"workspace_id": str(workspace_id)})
            await self._changed(workspace_id, await self.status(workspace_id, live_webhook=False), None)

    async def _changed(self, workspace_id: uuid.UUID, info: BotInfo, actor) -> None:
        if self._on_status_change is None:
            return
        try:
            await self._on_status_change(workspace_id, info, actor)
        except Exception:
            logger.exception("bot status callback failed")

    # --- DB (sync, run in a thread) ---

    def _active_rows(self) -> list[WorkspaceBot]:
        with self._session_factory() as s:
            return list(s.scalars(select(WorkspaceBot).where(WorkspaceBot.status == BotStatus.ACTIVE)))

    def _row(self, workspace_id: uuid.UUID) -> Optional[WorkspaceBot]:
        with self._session_factory() as s:
            return s.get(WorkspaceBot, workspace_id)

    def _live_elsewhere(self, telegram_bot_id: int, workspace_id: uuid.UUID) -> bool:
        with self._session_factory() as s:
            return (
                s.scalar(
                    select(WorkspaceBot.workspace_id).where(
                        WorkspaceBot.telegram_bot_id == telegram_bot_id,
                        WorkspaceBot.workspace_id != workspace_id,
                        WorkspaceBot.status != BotStatus.DISCONNECTED,
                    )
                )
                is not None
            )

    def _store(self, workspace_id, telegram_bot_id, username, token_encrypted, secret) -> None:
        with self._session_factory() as s, s.begin():
            row = s.get(WorkspaceBot, workspace_id, with_for_update=True)
            if row is None:
                row = WorkspaceBot(workspace_id=workspace_id)
                s.add(row)
            row.telegram_bot_id = telegram_bot_id
            row.username = username
            row.token_encrypted = token_encrypted
            row.webhook_secret = secret
            row.status = BotStatus.ACTIVE
            row.last_error = None
            row.connected_at = datetime.now(timezone.utc)
            s.flush()
            # DMs that waited for a bot can go now.
            wake_workspace(s, workspace_id)

    def _set_status(self, workspace_id, status: BotStatus, reason: Optional[str]) -> bool:
        with self._session_factory() as s, s.begin():
            result = s.execute(
                update(WorkspaceBot)
                .where(WorkspaceBot.workspace_id == workspace_id, WorkspaceBot.status != status)
                .values(status=status, last_error=reason)
            )
            return result.rowcount > 0


async def _log_handler_error(update: object, context) -> None:
    workspace = context.application.bot_data.get("workspace_id")
    if isinstance(context.error, Forbidden):
        return  # the user blocked the bot mid-conversation
    logger.error("Bot handler failed", exc_info=context.error, extra={"workspace_id": str(workspace)})
