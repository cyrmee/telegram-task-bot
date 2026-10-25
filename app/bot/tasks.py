"""Task DMs in Telegram (B3): the ✅ Mark complete button, /mytasks, and retiring
old DMs so they stop looking actionable.

Callback data (64 bytes max):
  task:complete:{task_id}   button on an assignment/update DM (from task_messages.py)
  list:complete:{task_id}   button on a /mytasks list; the list is re-rendered in place
  tasks:mine                📋 My tasks
"""
import asyncio
import html
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from app import db
from app.api.errors import ApiError
from app.models import (
    ChangeSource,
    Member,
    MemberStatus,
    Notification,
    NotificationStatus,
    NotificationType,
    Project,
    Task,
    TaskStatus,
    Workspace,
)
from app.services import tasks as task_service
from app.services.task_realtime import publish_task_event

logger = logging.getLogger(__name__)

LIST_LIMIT = 30


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(complete_from_dm, pattern=r"^task:complete:"))
    application.add_handler(CallbackQueryHandler(complete_from_list, pattern=r"^list:complete:"))
    application.add_handler(CallbackQueryHandler(show_my_tasks, pattern=r"^tasks:mine$"))


def _workspace(context: ContextTypes.DEFAULT_TYPE) -> uuid.UUID:
    return context.bot_data["workspace_id"]


def _task_id(data: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(data.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


# --- /mytasks ---


@dataclass(frozen=True)
class TaskLine:
    id: uuid.UUID
    number: int
    title: str
    project: str
    due_at: datetime


def render_task_list(rows: list[TaskLine], tz: ZoneInfo, now: Optional[datetime] = None) -> tuple[str, Optional[InlineKeyboardMarkup]]:
    """Open tasks grouped by due day (workspace time), one Complete button each."""
    if not rows:
        return "You have no open tasks. 🎉", None
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    today = now.date()
    lines = ["<b>Your open tasks</b>"]
    group = None
    for row in rows:
        local = row.due_at.astimezone(tz)
        if row.due_at < now:
            heading = "⚠️ Overdue"
        elif local.date() == today:
            heading = "Today"
        elif local.date() == today + timedelta(days=1):
            heading = "Tomorrow"
        else:
            heading = local.strftime("%a %d %b")
        if heading != group:
            lines.append(f"\n<b>{heading}</b>")
            group = heading
        when = local.strftime("%d %b %H:%M") if heading == "⚠️ Overdue" else local.strftime("%H:%M")
        lines.append(f"#{row.number} {html.escape(row.title)} · <i>{html.escape(row.project)}, {when}</i>")
    if len(rows) == LIST_LIMIT:
        lines.append(f"\n<i>Showing the first {LIST_LIMIT}. See the rest in the dashboard.</i>")
    buttons = [
        [InlineKeyboardButton(_button_label(row), callback_data=f"list:complete:{row.id}")] for row in rows
    ]
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


def _button_label(row: TaskLine) -> str:
    title = row.title if len(row.title) <= 40 else row.title[:39] + "…"
    return f"✅ #{row.number} {title}"


async def send_my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows, tz = await asyncio.to_thread(_open_tasks, _workspace(context), update.effective_user.id)
    if rows is None:
        await update.effective_message.reply_text("You're not a member of this workspace yet. Ask your manager for an invite link.")
        return
    text, markup = render_task_list(rows, tz)
    await update.effective_message.reply_html(text, reply_markup=markup)


async def show_my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await send_my_tasks(update, context)


# --- completing ---


async def complete_from_dm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    outcome = await _complete(update, context)
    if outcome is None:
        return
    await query.answer("Marked complete ✅" if outcome == "completed" else "Already completed")
    await _edit(query.message, f"{query.message.text_html}\n\n✅ <b>Completed</b>")


async def complete_from_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    outcome = await _complete(update, context)
    if outcome is None:
        return
    await query.answer("Marked complete ✅" if outcome == "completed" else "Already completed")
    rows, tz = await asyncio.to_thread(_open_tasks, _workspace(context), update.effective_user.id)
    text, markup = render_task_list(rows or [], tz)
    await _edit(query.message, text, markup)


async def _complete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    """Complete the task on the pressed button. Returns "completed" / "already",
    or None after telling the user why not."""
    query = update.callback_query
    ws = _workspace(context)
    task_id = _task_id(query.data)
    if task_id is None:
        await query.answer()
        return None
    result, member = await asyncio.to_thread(_complete_sync, ws, task_id, update.effective_user.id)
    if result == "not_member":
        await query.answer("You're not a member of this workspace.", show_alert=True)
        return None
    if result == "forbidden":
        await query.answer("Only the person this task is assigned to can complete it.", show_alert=True)
        return None
    if result == "not_found":
        await query.answer("This task no longer exists.", show_alert=True)
        await _edit(query.message, f"{query.message.text_html}\n\n🗑 <i>This task was cancelled.</i>")
        return None
    if result == "completed":
        with db.SessionLocal() as session:
            await publish_task_event(session, ws, task_id, "task.completed", member, source="telegram")
        await retire_task_dms(context.bot, task_id, member.id, "✅ <b>Completed</b>", keep=query.message.message_id)
    return result


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/done 12: the text fallback for the Complete button."""
    message = update.effective_message
    arg = (context.args[0] if context.args else "").lstrip("#")
    if not arg.isdigit():
        await message.reply_html("Send the task number, like <code>/done 12</code>. /mytasks shows the numbers.")
        return
    ws = _workspace(context)
    task_id, title = await asyncio.to_thread(_task_by_number, ws, int(arg))
    if task_id is None:
        await message.reply_text(f"There's no task #{arg} here. /mytasks shows your open tasks.")
        return
    result, member = await asyncio.to_thread(_complete_sync, ws, task_id, update.effective_user.id)
    replies = {
        "completed": f"✅ Done: <b>#{arg} {html.escape(title)}</b>. Nice work!",
        "already": f"#{arg} is already completed.",
        "forbidden": f"#{arg} isn't assigned to you, so you can't complete it.",
        "not_member": "You're not a member of this workspace yet. Ask your manager for an invite link.",
        "not_found": f"There's no task #{arg} here.",
    }
    await message.reply_html(replies.get(result, "Something went wrong. Try again in a minute."))
    if result == "completed":
        with db.SessionLocal() as session:
            await publish_task_event(session, ws, task_id, "task.completed", member, source="telegram")
        await retire_task_dms(context.bot, task_id, member.id, "✅ <b>Completed</b>")


def _task_by_number(ws: uuid.UUID, number: int) -> tuple[Optional[uuid.UUID], Optional[str]]:
    with db.SessionLocal() as session:
        row = session.execute(select(Task.id, Task.title).where(Task.workspace_id == ws, Task.number == number)).first()
        return (row.id, row.title) if row else (None, None)


def _complete_sync(ws: uuid.UUID, task_id: uuid.UUID, telegram_user_id: int):
    with db.SessionLocal() as session:
        member = _member(session, ws, telegram_user_id)
        if member is None:
            return "not_member", None
        try:
            change = task_service.complete_task(session, ws, task_id, member, ChangeSource.TELEGRAM)
        except ApiError as e:
            return ("forbidden" if e.status == 403 else "not_found" if e.status == 404 else "error"), member
        return ("completed" if change.changed else "already"), member


async def _edit(message, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logger.warning("Editing a task DM failed: %s", e)


# --- retiring old DMs ---

# Sent notifications that carry task buttons.
ACTIONABLE = (NotificationType.ASSIGNED, NotificationType.TASK_UPDATED, NotificationType.REMINDER, NotificationType.OVERDUE)
ACTIONABLE_PREFIXES = ("assigned", "updated", "reminder", "overdue")


async def retire_task_dms(bot, task_id: uuid.UUID, member_id: uuid.UUID, note_html: str, keep: Optional[int] = None) -> None:
    """Edit a member's earlier DMs about a task: drop the buttons and add a note.
    Found by idempotency-key prefix, so it still works after the task is deleted
    (notifications.task_id is SET NULL then)."""
    dms = await asyncio.to_thread(_actionable_dms, task_id, member_id)
    for chat_id, message_id, text in dms:
        if message_id == keep:
            continue
        try:
            await bot.edit_message_text(f"{text}\n\n{note_html}", chat_id=chat_id, message_id=message_id, parse_mode="HTML")
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                logger.info("Couldn't retire DM %s: %s", message_id, e)
        except TelegramError as e:
            logger.info("Couldn't retire DM %s: %s", message_id, e)


RETIRE_NOTES = {
    NotificationType.UNASSIGNED: "↪️ <i>Reassigned to someone else.</i>",
    NotificationType.TASK_DELETED: "🗑 <i>This task was cancelled.</i>",
}


def retire_note(notification_type: NotificationType, idempotency_key: str) -> Optional[str]:
    """The note for old DMs when this notification goes out, or None if it doesn't retire any."""
    if notification_type in RETIRE_NOTES:
        return RETIRE_NOTES[notification_type]
    if notification_type == NotificationType.TASK_UPDATED and idempotency_key.startswith("completed:"):
        return "✅ <b>Completed</b>"
    return None


def task_id_from_key(idempotency_key: str) -> Optional[uuid.UUID]:
    """`kind:{task_id}:...` -> task_id."""
    parts = idempotency_key.split(":")
    try:
        return uuid.UUID(parts[1])
    except (IndexError, ValueError):
        return None


def _actionable_dms(task_id: uuid.UUID, member_id: uuid.UUID) -> list[tuple[int, int, str]]:
    with db.SessionLocal() as session:
        rows = session.execute(
            select(Notification.telegram_chat_id, Notification.telegram_message_id, Notification.payload)
            .where(
                Notification.member_id == member_id,
                Notification.status == NotificationStatus.SENT,
                Notification.type.in_(ACTIONABLE),
                Notification.telegram_message_id.is_not(None),
                or_(*(Notification.idempotency_key.startswith(f"{p}:{task_id}:") for p in ACTIONABLE_PREFIXES)),
            )
        ).all()
        return [(r.telegram_chat_id, r.telegram_message_id, r.payload["text"]) for r in rows]


# --- queries ---


def _member(session, ws: uuid.UUID, telegram_user_id: int) -> Optional[Member]:
    return session.scalar(
        select(Member).where(
            Member.workspace_id == ws,
            Member.telegram_user_id == telegram_user_id,
            Member.status.in_([MemberStatus.ACTIVE, MemberStatus.UNREACHABLE]),
        )
    )


def _open_tasks(ws: uuid.UUID, telegram_user_id: int) -> tuple[Optional[list[TaskLine]], ZoneInfo]:
    with db.SessionLocal() as session:
        tz = ZoneInfo(session.get(Workspace, ws).timezone)
        member = _member(session, ws, telegram_user_id)
        if member is None:
            return None, tz
        rows = session.execute(
            select(Task.id, Task.number, Task.title, Project.name, Task.due_at)
            .join(Project, Project.id == Task.project_id)
            .where(Task.workspace_id == ws, Task.assignee_member_id == member.id, Task.status == TaskStatus.PENDING)
            .order_by(Task.due_at)
            .limit(LIST_LIMIT)
        ).all()
        return [TaskLine(*r) for r in rows], tz
