"""Command handlers for workspace bots. Every handler is tenant-scoped: it only
reads the workspace whose bot received the update (context.bot_data).

v1 surface: /start, /mytasks, /help, plus the task buttons in tasks.py (B3).
B2 adds invite redemption to /start; B6 adds /done.
"""
import asyncio
import html
import uuid
from typing import Optional

from sqlalchemy import select
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from app import db
from app.models import Member, MemberStatus, Workspace

from . import tasks

COMMANDS = [
    ("mytasks", "Your open tasks"),
    ("help", "How Nest works"),
]

PRIVATE = filters.ChatType.PRIVATE


def workspace_id(context: ContextTypes.DEFAULT_TYPE) -> uuid.UUID:
    return context.bot_data["workspace_id"]


def register(application: Application) -> None:
    application.add_handler(CommandHandler("start", start, filters=PRIVATE))
    application.add_handler(CommandHandler("help", help_command, filters=PRIVATE))
    application.add_handler(CommandHandler("mytasks", tasks.send_my_tasks, filters=PRIVATE))
    tasks.register(application)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ws = workspace_id(context)
    name, member = await asyncio.to_thread(_workspace_and_member, ws, update.effective_user.id)
    if member is not None:
        text = f"You're connected to <b>{html.escape(name)}</b> on Nest. Send /mytasks to see your open tasks."
    else:
        text = (
            f"This is <b>{html.escape(name)}</b>'s Nest bot. Your manager sends tasks here.\n\n"
            "To join, open the invite link your manager shared with you."
        )
    await update.effective_message.reply_html(text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html(
        "Nest sends you the tasks your manager assigns, with reminders before they're due.\n\n"
        "/mytasks — your open tasks\n/help — this message"
    )


# --- queries (sync, run in a thread) ---


def _member(session, ws: uuid.UUID, telegram_user_id: int) -> Optional[Member]:
    return session.scalar(
        select(Member).where(
            Member.workspace_id == ws,
            Member.telegram_user_id == telegram_user_id,
            Member.status.in_([MemberStatus.ACTIVE, MemberStatus.UNREACHABLE]),
        )
    )


def _workspace_and_member(ws: uuid.UUID, telegram_user_id: int) -> tuple[str, Optional[Member]]:
    with db.SessionLocal() as session:
        return session.get(Workspace, ws).name, _member(session, ws, telegram_user_id)
