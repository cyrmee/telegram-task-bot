"""Reminders (B5): pre-due, overdue, and the managers' daily digest.

A sweep runs every minute. Instead of firing inside a time window, it asks
"what should have gone out by now and hasn't?", so a late run or an outage
only delays reminders: they catch up on the next sweep. Every reminder goes
through the outbox with an idempotency key, so sweeps can repeat freely.

Keys carry the task's assignee and due time. When the due date moves or the
task is reassigned, A4 cancels the queued reminders and the next sweep queues
fresh ones under new keys.

- Pre-due: at due_at minus each offset (the task's task_reminders, or the
  workspace's reminder_lead_hours). After an outage only the nearest elapsed
  offset is sent. Offsets that had already passed when the task was created
  are skipped; the assignment DM covers those.
- Overdue: at due_at, then 09:00 workspace time on each following day, at most
  OVERDUE_MAX times. After an outage only the latest elapsed slot is sent.
- Digest: at 09:00 workspace time, each Telegram-linked manager gets the list
  of overdue tasks (nothing is sent when there are none).
"""
import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from html import escape
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.models import Member, MemberRole, MemberStatus, NotificationType, Project, Task, TaskStatus, Workspace
from app.notifications import enqueue, message_payload
from app.services.task_messages import _actions, _card

logger = logging.getLogger(__name__)

OVERDUE_MAX = 3
DAILY_AT = time(9, 0)
# Pending tasks further out than this can't have a pre-due reminder yet (lead hours max 720).
HORIZON = timedelta(days=31)


@dataclass(frozen=True)
class Planned:
    task_id: uuid.UUID
    key: str
    type: NotificationType
    slot: str


def _due_epoch(task: Task) -> int:
    return int(task.due_at.timestamp())


def offsets_minutes(task: Task, workspace: Workspace) -> list[int]:
    if task.reminders:
        return sorted({r.offset_minutes for r in task.reminders})
    return [workspace.reminder_lead_hours * 60] if workspace.reminder_lead_hours > 0 else []


def plan_pre_due(task: Task, workspace: Workspace, now: datetime) -> Optional[Planned]:
    """The pre-due reminder that should exist by `now`, if any."""
    if task.due_at <= now:
        return None
    elapsed = [m for m in offsets_minutes(task, workspace) if task.due_at - timedelta(minutes=m) <= now]
    if not elapsed:
        return None
    nearest = min(elapsed)  # the latest one to have come due
    if task.due_at - timedelta(minutes=nearest) < task.created_at:
        return None
    key = f"reminder:{task.id}:{task.assignee_member_id}:{_due_epoch(task)}:pre{nearest}"
    return Planned(task.id, key, NotificationType.REMINDER, f"pre{nearest}")


def overdue_slots(task: Task, tz: ZoneInfo) -> list[datetime]:
    """When overdue reminders fire: at the due time, then 09:00 local on the following days."""
    slots = [task.due_at]
    day = task.due_at.astimezone(tz).date()
    while len(slots) < OVERDUE_MAX:
        day += timedelta(days=1)
        slots.append(datetime.combine(day, DAILY_AT, tzinfo=tz))
    return slots


def plan_overdue(task: Task, workspace: Workspace, now: datetime) -> Optional[Planned]:
    if task.due_at > now:
        return None
    slots = overdue_slots(task, ZoneInfo(workspace.timezone))
    n = max(i for i, at in enumerate(slots, 1) if at <= now)
    key = f"overdue:{task.id}:{task.assignee_member_id}:{_due_epoch(task)}:{n}"
    return Planned(task.id, key, NotificationType.OVERDUE, str(n))


def render_reminder(task: Task, workspace: Workspace, now: datetime) -> dict:
    left = task.due_at - now
    hours = int(left.total_seconds() // 3600)
    when = f"in {hours} hour{'s' if hours != 1 else ''}" if hours >= 1 else "within the hour"
    if hours >= 48:
        when = f"in {hours // 24} days"
    return message_payload(f"⏰ Reminder: due {when}\n\n{_card(task, workspace)}", _actions(task))


def render_overdue(task: Task, workspace: Workspace) -> dict:
    return message_payload(f"⚠️ This task is overdue\n\n{_card(task, workspace)}", _actions(task))


def render_digest(workspace: Workspace, tasks: list[Task], now: datetime) -> dict:
    tz = ZoneInfo(workspace.timezone)
    lines = [f"☀️ <b>{escape(workspace.name)}</b>: {len(tasks)} overdue task{'s' if len(tasks) != 1 else ''}"]
    for task in tasks[:20]:
        days = (now.astimezone(tz).date() - task.due_at.astimezone(tz).date()).days
        late = "due today" if days <= 0 else f"{days} day{'s' if days != 1 else ''} late"
        who = escape(_assignee_name(task))
        lines.append(f"\n#{task.number} {escape(task.title)}\n<i>{who} · {escape(task.project.name)} · {late}</i>")
    if len(tasks) > 20:
        lines.append(f"\n…and {len(tasks) - 20} more in the dashboard.")
    return message_payload("\n".join(lines))


def _assignee_name(task: Task) -> str:
    from app.services.views import display_name

    return display_name(task.assignee)


# --- sweep ---


def sweep(session: Session, now: datetime) -> int:
    """Queue every reminder and digest that's due by `now`. Commits. Returns how
    many notifications were newly queued."""
    queued = 0
    tasks = session.scalars(
        select(Task)
        .join(Member, Member.id == Task.assignee_member_id)
        .options(selectinload(Task.reminders), selectinload(Task.project), selectinload(Task.assignee))
        .where(
            Task.status == TaskStatus.PENDING,
            Task.due_at <= now + HORIZON,
            Member.status.in_([MemberStatus.ACTIVE, MemberStatus.PENDING]),
        )
    ).all()
    workspaces = {w.id: w for w in session.scalars(select(Workspace).where(Workspace.id.in_({t.workspace_id for t in tasks})))}
    for task in tasks:
        workspace = workspaces[task.workspace_id]
        for plan in (plan_pre_due(task, workspace, now), plan_overdue(task, workspace, now)):
            if plan is None:
                continue
            payload = render_reminder(task, workspace, now) if plan.type == NotificationType.REMINDER else render_overdue(task, workspace)
            if enqueue(
                session, workspace_id=task.workspace_id, member_id=task.assignee_member_id, task_id=task.id,
                type=plan.type, idempotency_key=plan.key, payload=payload,
            ):
                queued += 1
    queued += _digests(session, now)
    session.commit()
    return queued


def _digests(session: Session, now: datetime) -> int:
    queued = 0
    managers = session.scalars(
        select(Member).where(
            Member.role == MemberRole.MANAGER,
            Member.status == MemberStatus.ACTIVE,
            Member.private_chat_id.is_not(None),
        )
    ).all()
    by_workspace: dict[uuid.UUID, list[Member]] = {}
    for m in managers:
        by_workspace.setdefault(m.workspace_id, []).append(m)
    for ws_id, ws_managers in by_workspace.items():
        workspace = session.get(Workspace, ws_id)
        local = now.astimezone(ZoneInfo(workspace.timezone))
        if local.time() < DAILY_AT:
            continue
        overdue = session.scalars(
            select(Task)
            .options(selectinload(Task.project), selectinload(Task.assignee))
            .join(Project, Project.id == Task.project_id)
            .where(Task.workspace_id == ws_id, Task.status == TaskStatus.PENDING, Task.due_at < now)
            .order_by(Task.due_at)
        ).all()
        if not overdue:
            continue
        payload = render_digest(workspace, overdue, now)
        for manager in ws_managers:
            if enqueue(
                session, workspace_id=ws_id, member_id=manager.id, type=NotificationType.DIGEST,
                idempotency_key=f"digest:{manager.id}:{local.date().isoformat()}", payload=payload,
            ):
                queued += 1
    return queued


class ReminderScheduler:
    """Runs sweep() every `interval` seconds until stopped."""

    def __init__(self, session_factory: sessionmaker[Session], *, interval: float = 60.0,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._session_factory = session_factory
        self.interval = interval
        self._clock = clock
        self.last_sweep_at: Optional[datetime] = None  # heartbeat for /health

    def run_once(self) -> int:
        with self._session_factory() as session:
            queued = sweep(session, self._clock())
        self.last_sweep_at = datetime.now(timezone.utc)
        return queued

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                queued = await asyncio.to_thread(self.run_once)
                if queued:
                    logger.info("Queued %s reminder(s)", queued)
            except Exception:
                logger.exception("Reminder sweep failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass


__all__ = ["ReminderScheduler", "overdue_slots", "plan_overdue", "plan_pre_due", "sweep"]
