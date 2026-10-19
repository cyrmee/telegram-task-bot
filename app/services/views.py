"""Row → API-shape helpers (dicts that validate against app/api/v1/schemas.py).

Services return these so the API, the realtime events and the bot all see
the same shapes.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import (
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


def display_name(member: Member) -> str:
    tg = member.telegram_user
    if member.display_name:
        return member.display_name
    if tg is not None:
        full = " ".join(p for p in (tg.first_name, tg.last_name) if p)
        if full:
            return full
        if tg.username:
            return f"@{tg.username}"
    if member.invited_username:
        return f"@{member.invited_username}"
    return "Member"


def member_summary(member: Optional[Member]) -> Optional[dict]:
    if member is None:
        return None
    tg = member.telegram_user
    return {
        "id": member.id,
        "display_name": display_name(member),
        "telegram_username": (tg.username if tg else None) or member.invited_username,
        "status": member.status,
    }


def task_counts_by_project(db: Session, workspace_id: uuid.UUID, project_ids: list[uuid.UUID]) -> dict:
    if not project_ids:
        return {}
    now = func.now()
    rows = db.execute(
        select(
            Task.project_id,
            func.count(),
            func.count().filter(Task.status == TaskStatus.PENDING),
            func.count().filter(Task.status == TaskStatus.COMPLETED),
            func.count().filter(and_(Task.status == TaskStatus.PENDING, Task.due_at < now)),
        )
        .where(Task.workspace_id == workspace_id, Task.project_id.in_(project_ids))
        .group_by(Task.project_id)
    ).all()
    counts = {r[0]: {"total": r[1], "pending": r[2], "completed": r[3], "overdue": r[4]} for r in rows}
    empty = {"total": 0, "pending": 0, "completed": 0, "overdue": 0}
    return {pid: counts.get(pid, dict(empty)) for pid in project_ids}


def project_out(project: Project, counts: dict) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "archived_at": project.archived_at,
        "task_counts": counts,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def _delivery_states(db: Session, tasks: list[Task]) -> dict:
    """Latest assignment DM per task, for its current assignee."""
    if not tasks:
        return {}
    pairs = [(t.id, t.assignee_member_id) for t in tasks]
    latest = (
        select(
            Notification.task_id,
            Notification.member_id,
            Notification.status,
            Notification.sent_at,
            Notification.last_error,
            func.row_number()
            .over(partition_by=(Notification.task_id, Notification.member_id), order_by=Notification.created_at.desc())
            .label("rn"),
        )
        .where(
            Notification.task_id.in_([p[0] for p in pairs]),
            Notification.type == NotificationType.ASSIGNED,
        )
        .subquery()
    )
    rows = db.execute(select(latest).where(latest.c.rn == 1)).all()
    return {(r.task_id, r.member_id): r for r in rows}


def delivery_out(task: Task, row) -> Optional[dict]:
    assignee = task.assignee
    if assignee.status == MemberStatus.UNREACHABLE:
        return {"state": "unreachable", "sent_at": None, "last_error": row.last_error if row else None}
    if row is None:
        return None
    if row.status == NotificationStatus.SENT:
        return {"state": "sent", "sent_at": row.sent_at, "last_error": None}
    if row.status == NotificationStatus.FAILED:
        return {"state": "failed", "sent_at": None, "last_error": row.last_error}
    if row.status == NotificationStatus.QUEUED and assignee.private_chat_id is None:
        return {"state": "not_linked", "sent_at": None, "last_error": None}
    if row.status == NotificationStatus.QUEUED:
        return {"state": "queued", "sent_at": None, "last_error": row.last_error}
    return None  # cancelled: superseded by a newer DM


def tasks_out(db: Session, workspace: Workspace, tasks: list[Task]) -> list[dict]:
    """Serialize tasks (relationships should be eager-loaded by the caller)."""
    deliveries = _delivery_states(db, tasks)
    now = datetime.now(timezone.utc)
    default_offsets = [workspace.reminder_lead_hours * 60] if workspace.reminder_lead_hours else []
    out = []
    for t in tasks:
        offsets = sorted((r.offset_minutes for r in t.reminders), reverse=True) or default_offsets
        out.append(
            {
                "id": t.id,
                "number": t.number,
                "title": t.title,
                "description": t.description,
                "project": {"id": t.project.id, "name": t.project.name},
                "assignee": member_summary(t.assignee),
                "due_at": t.due_at,
                "status": t.status,
                "is_overdue": t.status == TaskStatus.PENDING and t.due_at < now,
                "completed_at": t.completed_at,
                "completed_via": t.completed_via,
                "completed_by": member_summary(t.completed_by),
                "created_by": member_summary(t.created_by),
                "reminder_offsets_minutes": offsets,
                "delivery": delivery_out(t, deliveries.get((t.id, t.assignee_member_id))),
                "created_at": t.created_at,
                "updated_at": t.updated_at,
            }
        )
    return out


# Sort orders for task lists (schemas.TaskSort values).
TASK_SORTS = {
    "dueAt": (Task.due_at.asc(), Task.number.asc()),
    "-dueAt": (Task.due_at.desc(), Task.number.desc()),
    "createdAt": (Task.created_at.asc(), Task.number.asc()),
    "-createdAt": (Task.created_at.desc(), Task.number.desc()),
    "number": (Task.number.asc(),),
    "-number": (Task.number.desc(),),
}
