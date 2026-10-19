"""Command handlers for workspace bots. Every handler is tenant-scoped: it only
reads the workspace whose bot received the update (context.bot_data).

B1 ships the minimal v1 surface (/start, /mytasks, /help). B2 adds invite
redemption to /start, B3 the Complete button, and B6 the remaining commands.
"""
import asyncio
import html
import uuid
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from app import db
from app.models import Member, MemberStatus, Project, Task, TaskStatus, Workspace

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
    application.add_handler(CommandHandler("mytasks", my_tasks, filters=PRIVATE))


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


async def my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ws = workspace_id(context)
    rows, tz = await asyncio.to_thread(_open_tasks, ws, update.effective_user.id)
    if rows is None:
        await update.effective_message.reply_text("You're not a member of this workspace yet. Ask your manager for an invite link.")
        return
    if not rows:
        await update.effective_message.reply_text("You have no open tasks. 🎉")
        return
    lines = ["<b>Your open tasks</b>"]
    for number, title, project, due_at in rows:
        due = due_at.astimezone(tz).strftime("%a %d %b, %H:%M")
        lines.append(f"\n#{number} {html.escape(title)}\n<i>{html.escape(project)} · due {due}</i>")
    await update.effective_message.reply_html("\n".join(lines))


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


def _open_tasks(ws: uuid.UUID, telegram_user_id: int) -> tuple[Optional[list[tuple[int, str, str, datetime]]], ZoneInfo]:
    with db.SessionLocal() as session:
        tz = ZoneInfo(session.get(Workspace, ws).timezone)
        member = _member(session, ws, telegram_user_id)
        if member is None:
            return None, tz
        rows = session.execute(
            select(Task.number, Task.title, Project.name, Task.due_at)
            .join(Project, Project.id == Task.project_id)
            .where(Task.workspace_id == ws, Task.assignee_member_id == member.id, Task.status == TaskStatus.PENDING)
            .order_by(Task.due_at)
            .limit(50)
        ).all()
        return [tuple(r) for r in rows], tz
