"""
Main entry point for the Telegram Task Management Bot.
Initializes the bot, database, scheduler, and registers command handlers.
"""

import os
import logging
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler
from database import Database
from scheduler import TaskScheduler
from ai_parser import TaskParser
from handlers.commands import (
    start_command,
    add_task_command,
    my_tasks_command,
    edit_task_reminders_command,
    help_command,
)

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


class TaskBot:
    """
    Main bot class that orchestrates all components.
    """

    def __init__(self, token: str):
        """
        Initialize the bot with token and set up components.

        Args:
            token (str): Telegram Bot API token
        """
        self.token = token
        self.database = Database()
        self.ai_parser = TaskParser()
        self.application = None
        self.scheduler = None

    def setup_handlers(self):
        """
        Register all command handlers with the application.
        """

        # Wrapper functions to pass database to handlers
        async def start_wrapper(update, context):
            await start_command(update, context, self.database)

        async def add_task_wrapper(update, context):
            await add_task_command(update, context, self.database, self.ai_parser)

        async def my_tasks_wrapper(update, context):
            await my_tasks_command(update, context, self.database)

        async def edit_task_reminders_wrapper(update, context):
            await edit_task_reminders_command(update, context, self.database)

        async def help_wrapper(update, context):
            await help_command(update, context, self.database)

        # Register command handlers
        self.application.add_handler(CommandHandler("start", start_wrapper))
        self.application.add_handler(CommandHandler("add_task", add_task_wrapper))
        self.application.add_handler(CommandHandler("my_tasks", my_tasks_wrapper))
        self.application.add_handler(
            CommandHandler("edit_task_reminders", edit_task_reminders_wrapper)
        )
        self.application.add_handler(CommandHandler("help", help_wrapper))

        logger.info("Command handlers registered")

    async def post_init(self, application: Application):
        """
        Post-initialization hook to start the scheduler and set bot commands.
        Called after the bot is initialized but before it starts polling.

        Args:
            application (Application): The bot application
        """
        # Set bot commands for better user experience
        from telegram import BotCommand

        commands = [
            BotCommand("start", "Register/update your profile"),
            BotCommand("add_task", "Add a new task (admins only, in groups)"),
            BotCommand("my_tasks", "View your assigned tasks"),
            BotCommand(
                "edit_task_reminders", "Customize reminder settings for your tasks"
            ),
            BotCommand("help", "Get help using the bot"),
        ]

        try:
            await application.bot.set_my_commands(commands)
            logger.info("Bot commands set successfully")
        except Exception as e:
            logger.warning(f"Failed to set bot commands: {e}")

        # Initialize and start the scheduler
        self.scheduler = TaskScheduler(application.bot, self.database)
        self.scheduler.start()
        logger.info("Task scheduler initialized and started")

    async def post_shutdown(self, application: Application):
        """
        Post-shutdown hook to clean up resources.

        Args:
            application (Application): The bot application
        """
        if self.scheduler:
            self.scheduler.shutdown()
        logger.info("Bot shutdown complete")

    def run(self):
        """
        Start the bot and begin polling for updates.
        """
        # Create the Application
        self.application = Application.builder().token(self.token).build()

        # Setup handlers
        self.setup_handlers()

        # Register post_init and post_shutdown callbacks
        self.application.post_init = self.post_init
        self.application.post_shutdown = self.post_shutdown

        # Start the bot
        logger.info("Starting bot...")
        self.application.run_polling(allowed_updates=["message", "chat_member"])


def main():
    """
    Main function to load configuration and start the bot.
    """
    # Load environment variables from .env file
    load_dotenv()

    # Get bot token from environment
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment variables!")
        logger.error("Please create a .env file with your bot token.")
        return

    # Create and run the bot
    bot = TaskBot(token)
    bot.run()


if __name__ == "__main__":
    main()
