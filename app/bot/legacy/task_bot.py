"""Pre-v1 single-bot runtime (TELEGRAM_BOT_TOKEN), moved here unchanged by A1.

Runs against the legacy tables in ./models.py, so it doesn't work on a v1
database. Started by app.main only when LEGACY_BOT_ENABLED=true. Stream B
replaces it with the per-workspace BotManager (B1) and rewrites the
handlers (B6).
"""
import os
import asyncio
import logging
import secrets

from fastapi import APIRouter, HTTPException, Request
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler

from .database import Database
from .scheduler import TaskScheduler
from .handlers.commands import (
    start_command,
    register_command,
    add_task_command,
    my_tasks_command,
    list_tasks_command,
    edit_task_reminders_command,
    update_task_status_command,
    delete_task_command,
    help_command,
)
from .constants import BOT_COMMANDS, AI_TASK_PARSING_ENABLED
from app.auth import telegram_secret_ok

logger = logging.getLogger(__name__)


class TaskBot:
    def __init__(self, token: str):
        self.token = token
        self.database = Database()
        self.ai_parser = None
        if AI_TASK_PARSING_ENABLED:
            from .ai_parser import TaskParser

            self.ai_parser = TaskParser()
        self.application = Application.builder().token(self.token).build()
        self.scheduler = None
        self.initialized = False
        # Telegram echoes this in X-Telegram-Bot-Api-Secret-Token; anything without it is spoofed.
        self.webhook_secret = secrets.token_urlsafe(32)

        self.setup_handlers()

    def setup_handlers(self):
        async def start_wrapper(update, context):
            await start_command(update, context, self.database)

        async def register_wrapper(update, context):
            await register_command(update, context, self.database)

        async def add_task_wrapper(update, context):
            await add_task_command(update, context, self.database, self.ai_parser)

        async def my_tasks_wrapper(update, context):
            await my_tasks_command(update, context, self.database)

        async def list_tasks_wrapper(update, context):
            await list_tasks_command(update, context, self.database)

        async def edit_task_reminders_wrapper(update, context):
            await edit_task_reminders_command(update, context, self.database)

        async def update_status_wrapper(update, context):
            await update_task_status_command(update, context, self.database)

        async def delete_task_wrapper(update, context):
            await delete_task_command(update, context, self.database)

        async def help_wrapper(update, context):
            await help_command(update, context)

        self.application.add_handler(CommandHandler("start", start_wrapper))
        self.application.add_handler(CommandHandler("register", register_wrapper))
        self.application.add_handler(CommandHandler("add_task", add_task_wrapper))
        self.application.add_handler(CommandHandler("my_tasks", my_tasks_wrapper))
        self.application.add_handler(CommandHandler("list_tasks", list_tasks_wrapper))
        self.application.add_handler(
            CommandHandler("edit_task_reminders", edit_task_reminders_wrapper)
        )
        self.application.add_handler(
            CommandHandler("update_status", update_status_wrapper)
        )
        self.application.add_handler(CommandHandler("delete_task", delete_task_wrapper))
        self.application.add_handler(CommandHandler("help", help_wrapper))

        logger.info("Command handlers registered")

    async def initialize(self):
        await self.application.initialize()

        # Set bot instance in database for fetching user info
        self.database.set_bot(self.application.bot)

        # Try to set bot commands (may fail if Telegram API is unreachable)
        commands = [
            BotCommand(command, description) for command, description in BOT_COMMANDS
        ]

        try:
            await asyncio.wait_for(
                self.application.bot.set_my_commands(commands),
                timeout=10
            )
            logger.info("Bot commands set successfully")
        except asyncio.TimeoutError:
            logger.warning("Timed out setting bot commands (Telegram API may be unreachable)")
        except Exception as e:
            logger.warning(f"Failed to set bot commands: {e}")

        self.scheduler = TaskScheduler(self.application.bot, self.database)
        self.scheduler.start()
        logger.info("Task scheduler initialized and started")

        # Webhook setup
        webhook_url = os.getenv("PUBLIC_BASE_URL") or os.getenv("WEBHOOK_URL")
        if webhook_url:
            full_webhook_url = f"{webhook_url.rstrip('/')}/webhook"
            try:
                await self.application.bot.set_webhook(
                    url=full_webhook_url, secret_token=self.webhook_secret, drop_pending_updates=True
                )
                logger.info(f"Webhook set to: {full_webhook_url}")
            except Exception as e:
                logger.error(f"Failed to set webhook: {e}")
        else:
            logger.warning("PUBLIC_BASE_URL not set. Webhook not registered, so the bot will not receive updates.")

        await self.application.start()
        self.initialized = True

    async def shutdown(self):
        if self.scheduler:
            self.scheduler.shutdown()
        if self.initialized:
            await self.application.stop()
            await self.application.shutdown()
        logger.info("Bot shutdown complete")


def webhook_router(bot: TaskBot) -> APIRouter:
    router = APIRouter()

    @router.post("/webhook", include_in_schema=False)
    async def telegram_webhook(request: Request):
        if not telegram_secret_ok(request.headers.get("X-Telegram-Bot-Api-Secret-Token"), bot.webhook_secret):
            raise HTTPException(status_code=403, detail="Forbidden")
        try:
            data = await request.json()
            update = Update.de_json(data, bot.application.bot)
            await bot.application.process_update(update)
            return {"ok": True}
        except Exception as e:
            logger.error(f"Error processing webhook: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

    return router
