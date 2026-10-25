"""Command handlers for workspace bots. Every handler is tenant-scoped: it only
reads the workspace whose bot received the update (context.bot_data).

v1 surface: /start, /mytasks, /help, plus the task buttons in tasks.py (B3).
/start (invites, B2) lives in onboarding.py; B6 adds /done.
"""
import uuid

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from . import onboarding, tasks

COMMANDS = [
    ("mytasks", "Your open tasks"),
    ("help", "How Nest works"),
]

PRIVATE = filters.ChatType.PRIVATE


def workspace_id(context: ContextTypes.DEFAULT_TYPE) -> uuid.UUID:
    return context.bot_data["workspace_id"]


def register(application: Application) -> None:
    application.add_handler(CommandHandler("start", onboarding.start, filters=PRIVATE))
    application.add_handler(CommandHandler("help", help_command, filters=PRIVATE))
    application.add_handler(CommandHandler("mytasks", tasks.send_my_tasks, filters=PRIVATE))
    tasks.register(application)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html(
        "Nest sends you the tasks your manager assigns, with reminders before they're due.\n\n"
        "/mytasks — your open tasks\n/help — this message"
    )
