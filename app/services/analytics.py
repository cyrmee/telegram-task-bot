"""Analytics screen (PRD 6.7). Aggregation happens in SQL.

Semantics (docs/api-v1.md, Analytics): a task is "in range" when its due
date, in the workspace timezone, falls between `from` and `to` inclusive.
Workload (open tasks) and the overdue list ignore the range.
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.api.errors import ApiError
from app.models import Member, MemberStatus, Project, ProjectStatus, Task, TaskStatus, Workspace
from app.services import tasks as task_service
from app.services.views import display_name, tasks_out

DEFAULT_DAYS = 30
MAX_DAYS = 366
OVERDUE_LIST_LIMIT = 20


def _rates(total: int, completed: int, pending: int, overdue: int) -> dict:
    return {
        "total": total,
        "completed": completed,
        "pending": pending,
        "overdue": overdue,
        "completion_rate": round(completed / total, 4) if total else None,
    }


def resolve_range(workspace: Workspace, from_: date | None, to: date | None) -> tuple[date, date, datetime, datetime]:
    tz = ZoneInfo(workspace.timezone)
    today = datetime.now(tz).date()
    to = to or today
    from_ = from_ or to - timedelta(days=DEFAULT_DAYS - 1)
    if from_ > to:
        raise ApiError(422, "validation_error", "Some fields are invalid.", {"fields": [{"field": "from", "message": "Must be on or before `to`"}]})
    if (to - from_).days + 1 > MAX_DAYS:
        raise ApiError(422, "validation_error", "Some fields are invalid.", {"fields": [{"field": "from", "message": f"Range is limited to {MAX_DAYS} days"}]})
    start = datetime.combine(from_, time.min, tz).astimezone(timezone.utc)
    end = datetime.combine(to + timedelta(days=1), time.min, tz).astimezone(timezone.utc)
    return from_, to, start, end


def get_analytics(db: Session, workspace_id: uuid.UUID, from_: date | None = None, to: date | None = None) -> dict:
    workspace = db.get(Workspace, workspace_id)
    from_, to, start, end = resolve_range(workspace, from_, to)
    now = func.now()
    in_range = and_(Task.workspace_id == workspace_id, Task.due_at >= start, Task.due_at < end)
    completed = func.count().filter(Task.status == TaskStatus.COMPLETED)
    pending = func.count().filter(Task.status == TaskStatus.PENDING)
    overdue = func.count().filter(and_(Task.status == TaskStatus.PENDING, Task.due_at < now))

    totals = db.execute(select(func.count(), completed, pending, overdue).where(in_range)).one()

    project_rows = db.execute(
        select(Project.id, Project.name, Project.status, func.count(Task.id), completed, pending, overdue)
        .select_from(Project)
        .outerjoin(Task, and_(Task.project_id == Project.id, in_range))
        .where(Project.workspace_id == workspace_id)
        .group_by(Project.id)
        .order_by(Project.name)
    ).all()

    open_tasks = (
        select(func.count())
        .where(Task.assignee_member_id == Member.id, Task.status == TaskStatus.PENDING)
        .correlate(Member)
        .scalar_subquery()
    )
    member_rows = db.execute(
        select(Member, func.count(Task.id), completed, pending, overdue, open_tasks)
        .options(selectinload(Member.telegram_user))
        .outerjoin(Task, and_(Task.assignee_member_id == Member.id, in_range))
        .where(Member.workspace_id == workspace_id)
        .group_by(Member.id)
    ).all()

    overdue_tasks = list(
        db.scalars(
            task_service.eager(
                select(Task)
                .where(Task.workspace_id == workspace_id, Task.status == TaskStatus.PENDING, Task.due_at < now)
                .order_by(Task.due_at)
                .limit(OVERDUE_LIST_LIMIT)
            )
        )
    )

    by_member = []
    for m, total, c, p, o, open_count in member_rows:
        if m.status == MemberStatus.REMOVED and total == 0:
            continue
        by_member.append({"member_id": m.id, "display_name": display_name(m), "open_tasks": open_count, **_rates(total, c, p, o)})
    by_member.sort(key=lambda r: (-r["open_tasks"], r["display_name"].lower()))

    return {
        "range": {"from": from_, "to": to},
        "totals": _rates(*totals),
        "by_project": [
            {"project_id": pid, "name": name, **_rates(total, c, p, o)}
            for pid, name, status, total, c, p, o in project_rows
            if status == ProjectStatus.ACTIVE or total > 0
        ],
        "by_member": by_member,
        "overdue_tasks": tasks_out(db, workspace, overdue_tasks),
        "generated_at": datetime.now(timezone.utc),
    }
