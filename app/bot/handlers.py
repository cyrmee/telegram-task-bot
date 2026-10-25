"""Command handlers for workspace bots. Every handler is tenant-scoped: it only
reads the workspace whose bot received the update (context.bot_data).

v1 surface (B6): /start (onboarding.py), /mytasks and /done (tasks.py), /help,
plus the task buttons. The pre-v1 commands live in app/bot/legacy, unregistered.
"""
import uuid

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from . import onboarding, tasks

COMMANDS = [
    ("mytasks", "Your open tasks"),
    ("done", "Mark a task complete, e.g. /done 12"),
    ("help", "How Nest works"),
]

PRIVATE = filters.ChatType.PRIVATE


def workspace_id(context: ContextTypes.DEFAULT_TYPE) -> uuid.UUID:
    return context.bot_data["workspace_id"]


def register(application: Application) -> None:
    application.add_handler(CommandHandler("start", onboarding.start, filters=PRIVATE))
    application.add_handler(CommandHandler("help", help_command, filters=PRIVATE))
    application.add_handler(CommandHandler("mytasks", tasks.send_my_tasks, filters=PRIVATE))
    application.add_handler(CommandHandler("done", tasks.done_command, filters=PRIVATE))
    tasks.register(application)
    # Anything else in a private chat (old commands, free text) gets a short pointer.
    application.add_handler(MessageHandler(PRIVATE & (filters.TEXT | filters.COMMAND), fallback))


HELP = (
    "Your manager assigns tasks in Nest, and they arrive here. "
    "You'll get a reminder before each one is due.\n\n"
    "Tap <b>✅ Mark complete</b> on a task when it's done, or use these:\n"
    "/mytasks — see your open tasks\n"
    "/done 12 — mark task #12 complete\n"
    "/help — show this message"
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html(HELP)


async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html("Sorry, I didn't understand that.\n\n" + HELP.split("\n\n", 1)[1])
