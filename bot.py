import os
import logging
from telegram.ext import Application, CommandHandler
from telegram import BotCommand

from database import Database
from scheduler import TaskScheduler
from ai_parser import TaskParser
from handlers.commands import (
    start_command,
    add_task_command,
    my_tasks_command,
    edit_task_reminders_command,
    update_task_status_command,
    view_done_tasks_command,
    delete_task_command,
    help_command,
)
from constants import BOT_COMMANDS

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


class TaskBot:
    def __init__(self, token: str):
        self.token = token
        self.database = Database()
        self.ai_parser = TaskParser()
        self.application = None
        self.scheduler = None

    def setup_handlers(self):
        async def start_wrapper(update, context):
            await start_command(update, context, self.database)

        async def add_task_wrapper(update, context):
            await add_task_command(update, context, self.database, self.ai_parser)

        async def my_tasks_wrapper(update, context):
            await my_tasks_command(update, context, self.database)

        async def edit_task_reminders_wrapper(update, context):
            await edit_task_reminders_command(update, context, self.database)

        async def update_status_wrapper(update, context):
            await update_task_status_command(update, context, self.database)

        async def view_done_wrapper(update, context):
            await view_done_tasks_command(update, context, self.database)

        async def delete_task_wrapper(update, context):
            await delete_task_command(update, context, self.database)

        async def help_wrapper(update, context):
            await help_command(update, context)

        self.application.add_handler(CommandHandler("start", start_wrapper))
        self.application.add_handler(CommandHandler("add_task", add_task_wrapper))
        self.application.add_handler(CommandHandler("my_tasks", my_tasks_wrapper))
        self.application.add_handler(
            CommandHandler("edit_task_reminders", edit_task_reminders_wrapper)
        )
        self.application.add_handler(
            CommandHandler("update_status", update_status_wrapper)
        )
        self.application.add_handler(CommandHandler("view_done", view_done_wrapper))
        self.application.add_handler(CommandHandler("delete_task", delete_task_wrapper))
        self.application.add_handler(CommandHandler("help", help_wrapper))

        logger.info("Command handlers registered")

    async def post_init(self, application: Application):
        # Set bot instance in database for fetching user info
        self.database.set_bot(application.bot)

        commands = [
            BotCommand(command, description) for command, description in BOT_COMMANDS
        ]

        try:
            await application.bot.set_my_commands(commands)
            logger.info("Bot commands set successfully")
        except Exception as e:
            logger.warning(f"Failed to set bot commands: {e}")

        self.scheduler = TaskScheduler(application.bot, self.database)
        self.scheduler.start()
        logger.info("Task scheduler initialized and started")

    async def post_shutdown(self, application: Application):
        if self.scheduler:
            self.scheduler.shutdown()
        logger.info("Bot shutdown complete")

    def run(self):
        self.application = Application.builder().token(self.token).build()

        self.setup_handlers()

        self.application.post_init = self.post_init
        self.application.post_shutdown = self.post_shutdown

        logger.info("Starting bot...")
        self.application.run_polling(allowed_updates=["message", "chat_member"])


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment variables!")
        logger.error("Please create a .env file with your bot token.")
        return

    bot = TaskBot(token)
    bot.run()


if __name__ == "__main__":
    main()
