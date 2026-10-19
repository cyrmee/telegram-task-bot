"""FastAPI app factory: `uvicorn app.main:app`."""
import asyncio
import logging
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import db
from app.api.errors import register_error_handlers
from app.api.v1 import api_router
from app.config import get_settings
from app.logging_config import configure_logging
from app.realtime import sio

logger = logging.getLogger(__name__)


def _legacy_bot(api: FastAPI):
    """The pre-v1 single bot, behind LEGACY_BOT_ENABLED (see app/bot/legacy)."""
    settings = get_settings()
    if not settings.legacy_bot_enabled:
        return None
    if not settings.telegram_bot_token:
        logger.error("LEGACY_BOT_ENABLED is set but TELEGRAM_BOT_TOKEN is empty")
        return None
    from app.bot.legacy.task_bot import TaskBot, webhook_router

    bot = TaskBot(settings.telegram_bot_token)
    api.include_router(webhook_router(bot))
    return bot


def create_app() -> socketio.ASGIApp:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    legacy_bot = None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if legacy_bot:
            try:
                await asyncio.wait_for(legacy_bot.initialize(), timeout=15)
            except Exception:
                logger.exception("Legacy bot failed to start; the API keeps running")
        yield
        if legacy_bot:
            await legacy_bot.shutdown()

    api = FastAPI(title="Nest API", version="1.0.0", separate_input_output_schemas=False, lifespan=lifespan)
    register_error_handlers(api)
    api.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens, no cookies
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    api.include_router(api_router)
    legacy_bot = _legacy_bot(api)

    @api.get("/health", tags=["health"], include_in_schema=False)
    def health():
        with db.SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok"}

    # Socket.IO handles /socket.io/*, everything else goes to FastAPI (tests reach it as app.other_asgi_app).
    return socketio.ASGIApp(sio, other_asgi_app=api, socketio_path="socket.io")


app = create_app()
