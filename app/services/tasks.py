"""Task lifecycle, shared by the API and the bot (B3 calls complete_task).

Each mutating function makes the change, records a task_event and queues
the Telegram DMs (B4 outbox) in one transaction, then commits. Emitting the
realtime event is the caller's job, after the commit.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.api.errors import ApiError
from app.models import (
    ChangeSource,
    Member,
    MemberRole,
    MemberStatus,
    Notification,
    NotificationType,
    Project,
    ProjectStatus,
    Task,
    TaskEvent,
    TaskEventType,
    TaskReminder,
    TaskStatus,
    Workspace,
)
from app.notifications import assigned_key, cancel_queued, enqueue
from app.services import task_messages as msg
from app.services.views import TASK_SORTS, display_name, member_summary, tasks_out


@dataclass
class TaskFilters:
    project_ids: Optional[list[uuid.UUID]] = None
    assignee_ids: Optional[list[uuid.UUID]] = None
    status: Optional[TaskStatus] = None
    overdue: Optional[bool] = None
    due_after: Optional[datetime] = None
    due_before: Optional[datetime] = None
    q: Optional[str] = None
    include_archived: bool = False


@dataclass
class TaskChange:
    """Result of a mutation: which task, and what changed (camelCase names)."""

    task_id: uuid.UUID
    changed: list[str] = field(default_factory=list)


def _field_error(name: str, message: str) -> ApiError:
    return ApiError(422, "validation_error", "Some fields are invalid.", {"fields": [{"field": name, "message": message}]})


def eager(query):
    """Eager-load what tasks_out() needs."""
    return _eager(query)


def _eager(query):
    return query.options(
        selectinload(Task.project),
        selectinload(Task.assignee).selectinload(Member.telegram_user),
        selectinload(Task.completed_by).selectinload(Member.telegram_user),
        selectinload(Task.created_by).selectinload(Member.telegram_user),
        selectinload(Task.reminders),
    )


def _get(db: Session, workspace_id: uuid.UUID, task_id: uuid.UUID, lock: bool = False) -> Task:
    query = _eager(select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id))
    if lock:
        query = query.with_for_update(of=Task)
    task = db.scalar(query)
    if task is None:
        raise ApiError(404, "not_found", "Task not found.")
    return task


def get_task_out(db: Session, workspace_id: uuid.UUID, task_id: uuid.UUID) -> dict:
    task = _get(db, workspace_id, task_id)
    return tasks_out(db, db.get(Workspace, workspace_id), [task])[0]


def _project(db: Session, workspace_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    project = db.scalar(select(Project).where(Project.workspace_id == workspace_id, Project.id == project_id))
    if project is None:
        raise _field_error("projectId", "Unknown project")
    if project.status != ProjectStatus.ACTIVE:
        raise ApiError(422, "project_archived", f"“{project.name}” is archived. Unarchive it first.")
    return project


def _assignee(db: Session, workspace_id: uuid.UUID, member_id: uuid.UUID) -> Member:
    member = db.scalar(select(Member).where(Member.workspace_id == workspace_id, Member.id == member_id))
    if member is None:
        raise _field_error("assigneeId", "Unknown member")
    if member.status == MemberStatus.REMOVED:
        raise ApiError(422, "assignee_not_active", "That person was removed from the workspace.")
    return member


def _next_number(db: Session, workspace_id: uuid.UUID) -> int:
    # Row lock on the workspace serializes concurrent creates (docs/data-model.md).
    return db.execute(
        update(Workspace)
        .where(Workspace.id == workspace_id)
        .values(next_task_number=Workspace.next_task_number + 1)
        .returning(Workspace.next_task_number - 1)
    ).scalar_one()


def _event(db: Session, task: Task, type_: TaskEventType, source: ChangeSource, actor: Optional[Member], **data) -> TaskEvent:
    event = TaskEvent(
        workspace_id=task.workspace_id,
        task_id=task.id,
        type=type_,
        source=source,
        actor_member_id=actor.id if actor else None,
        data={k: (str(v) if isinstance(v, (uuid.UUID, datetime)) else v) for k, v in data.items()},
    )
    db.add(event)
    db.flush()
    return event


def _dm(db: Session, task: Task, member: Member, type_: NotificationType, key: str, payload: dict) -> None:
    if member.status == MemberStatus.REMOVED:
        return
    enqueue(
        db,
        workspace_id=task.workspace_id,
        member_id=member.id,
        task_id=task.id,
        type=type_,
        idempotency_key=key,
        payload=payload,
    )


def _set_reminders(db: Session, task: Task, offsets: Optional[list[int]]) -> None:
    if offsets is None:
        return
    task.reminders = [TaskReminder(offset_minutes=m) for m in sorted(set(offsets))]


# --- mutations -------------------------------------------------------------------

def create_task(db: Session, actor: Member, data: dict, source: ChangeSource = ChangeSource.DASHBOARD) -> TaskChange:
    workspace_id = actor.workspace_id
    project = _project(db, workspace_id, data["project_id"])
    assignee = _assignee(db, workspace_id, data["assignee_id"])
    task = Task(
        workspace_id=workspace_id,
        number=_next_number(db, workspace_id),
        project_id=project.id,
        assignee_member_id=assignee.id,
        title=data["title"].strip(),
        description=data.get("description"),
        due_at=data["due_at"],
        created_by_member_id=actor.id,
    )
    db.add(task)
    db.flush()
    _set_reminders(db, task, data.get("reminder_offsets_minutes"))
    _event(db, task, TaskEventType.CREATED, source, actor)
    task = _get(db, workspace_id, task.id)
    workspace = db.get(Workspace, workspace_id)
    _dm(db, task, assignee, NotificationType.ASSIGNED, assigned_key(task.id, assignee.id), msg.render_assigned(task, workspace))
    db.commit()
    return TaskChange(task.id)


def update_task(
    db: Session, actor: Member, task_id: uuid.UUID, changes: dict, source: ChangeSource = ChangeSource.DASHBOARD
) -> TaskChange:
    workspace_id = actor.workspace_id
    task = _get(db, workspace_id, task_id, lock=True)
    for required in ("title", "project_id", "assignee_id", "due_at"):
        if required in changes and changes[required] is None:
            raise _field_error({"project_id": "projectId", "assignee_id": "assigneeId", "due_at": "dueAt"}.get(required, required), "Cannot be null")

    changed: list[str] = []
    old_assignee = task.assignee
    old_due = task.due_at

    if "title" in changes and changes["title"].strip() != task.title:
        task.title = changes["title"].strip()
        changed.append("title")
    if "description" in changes and changes["description"] != task.description:
        task.description = changes["description"]
        changed.append("description")
    if "project_id" in changes and changes["project_id"] != task.project_id:
        task.project_id = _project(db, workspace_id, changes["project_id"]).id
        changed.append("projectId")
    if "reminder_offsets_minutes" in changes:
        before = sorted(r.offset_minutes for r in task.reminders)
        _set_reminders(db, task, changes["reminder_offsets_minutes"] or [])
        if sorted(set(changes["reminder_offsets_minutes"] or [])) != before:
            changed.append("reminderOffsetsMinutes")
    if "due_at" in changes and changes["due_at"] != task.due_at:
        task.due_at = changes["due_at"]
        changed.append("dueAt")
    new_assignee = None
    if "assignee_id" in changes and changes["assignee_id"] != task.assignee_member_id:
        new_assignee = _assignee(db, workspace_id, changes["assignee_id"])
        task.assignee_member_id = new_assignee.id
        changed.append("assigneeId")

    if not changed:
        return TaskChange(task.id)

    db.flush()
    db.expire(task)
    task = _get(db, workspace_id, task.id)
    workspace = db.get(Workspace, workspace_id)

    detail_fields = [c for c in changed if c not in ("assigneeId", "dueAt")]
    if detail_fields:
        _event(db, task, TaskEventType.UPDATED, source, actor, changed=detail_fields)
    if "dueAt" in changed:
        _event(db, task, TaskEventType.DUE_CHANGED, source, actor, previous_due_at=old_due, new_due_at=task.due_at)
        cancel_queued(db, task_id=task.id, types=[NotificationType.REMINDER, NotificationType.OVERDUE], reason="due date changed")
    if "reminderOffsetsMinutes" in changed and "dueAt" not in changed:
        cancel_queued(db, task_id=task.id, types=[NotificationType.REMINDER], reason="reminders changed")

    if new_assignee is not None:
        event = _event(db, task, TaskEventType.REASSIGNED, source, actor, **{"from": old_assignee.id, "to": new_assignee.id})
        cancel_queued(db, task_id=task.id, reason="reassigned")
        if task.status == TaskStatus.PENDING:
            _dm(db, task, old_assignee, NotificationType.UNASSIGNED, f"unassigned:{task.id}:{event.id}", msg.render_unassigned(task, workspace))
            _dm(db, task, new_assignee, NotificationType.ASSIGNED, f"assigned:{task.id}:{new_assignee.id}:{event.id}", msg.render_assigned(task, workspace))
    elif task.status == TaskStatus.PENDING and ({"dueAt", "title", "description", "projectId"} & set(changed)):
        event_id = uuid.uuid4()
        _dm(
            db, task, task.assignee, NotificationType.TASK_UPDATED, f"updated:{task.id}:{event_id}",
            msg.render_updated(task, workspace, [c for c in changed if c != "reminderOffsetsMinutes"]),
        )
    db.commit()
    return TaskChange(task.id, changed)


def complete_task(db: Session, workspace_id: uuid.UUID, task_id: uuid.UUID, actor: Member, source: ChangeSource) -> TaskChange:
    """Idempotent. Allowed for the assignee and for managers."""
    task = _get(db, workspace_id, task_id, lock=True)
    if actor.workspace_id != workspace_id or (actor.role != MemberRole.MANAGER and actor.id != task.assignee_member_id):
        raise ApiError(403, "forbidden", "Only the assignee or a manager can complete this task.")
    if task.status == TaskStatus.COMPLETED:
        return TaskChange(task.id)
    task.status = TaskStatus.COMPLETED
    task.completed_at = datetime.now(timezone.utc)
    task.completed_via = source
    task.completed_by_member_id = actor.id
    db.flush()
    event = _event(db, task, TaskEventType.COMPLETED, source, actor)
    cancel_queued(db, task_id=task.id, types=[NotificationType.REMINDER, NotificationType.OVERDUE], reason="completed")
    workspace = db.get(Workspace, workspace_id)
    if actor.id != task.assignee_member_id:
        _dm(db, task, task.assignee, NotificationType.TASK_UPDATED, f"completed:{task.id}:{event.id}", msg.render_completed_for_assignee(task, workspace))
    managers = db.scalars(
        select(Member).where(
            Member.workspace_id == workspace_id,
            Member.role == MemberRole.MANAGER,
            Member.status == MemberStatus.ACTIVE,
            Member.telegram_user_id.is_not(None),
            Member.id != actor.id,
        )
    )
    completer = display_name(actor)
    for manager in managers:
        _dm(
            db, task, manager, NotificationType.COMPLETED_MANAGER, f"completed_manager:{task.id}:{manager.id}:{event.id}",
            msg.render_completed_for_manager(task, workspace, completer),
        )
    db.commit()
    return TaskChange(task.id, ["status"])


def reopen_task(db: Session, workspace_id: uuid.UUID, task_id: uuid.UUID, actor: Member, source: ChangeSource) -> TaskChange:
    """Idempotent. Managers only."""
    task = _get(db, workspace_id, task_id, lock=True)
    if actor.workspace_id != workspace_id or actor.role != MemberRole.MANAGER:
        raise ApiError(403, "forbidden", "Only a manager can reopen a task.")
    if task.status == TaskStatus.PENDING:
        return TaskChange(task.id)
    task.status = TaskStatus.PENDING
    task.completed_at = None
    task.completed_via = None
    task.completed_by_member_id = None
    db.flush()
    _event(db, task, TaskEventType.REOPENED, source, actor)
    db.commit()
    return TaskChange(task.id, ["status"])


def delete_task(db: Session, actor: Member, task_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    task = _get(db, actor.workspace_id, task_id, lock=True)
    project_id = task.project_id
    cancel_queued(db, task_id=task.id, reason="task deleted")
    if task.status == TaskStatus.PENDING:
        workspace = db.get(Workspace, actor.workspace_id)
        _dm(db, task, task.assignee, NotificationType.TASK_DELETED, f"deleted:{task.id}:{task.assignee_member_id}", msg.render_deleted(task, workspace))
    db.delete(task)
    db.commit()
    return task_id, project_id


# --- reads -----------------------------------------------------------------------

def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_tasks(
    db: Session, workspace_id: uuid.UUID, filters: TaskFilters, sort: str, page: int, page_size: int
) -> tuple[list[dict], int]:
    query = select(Task).where(Task.workspace_id == workspace_id)
    if not filters.include_archived:
        query = query.join(Project, Project.id == Task.project_id).where(Project.status == ProjectStatus.ACTIVE)
    if filters.project_ids:
        query = query.where(Task.project_id.in_(filters.project_ids))
    if filters.assignee_ids:
        query = query.where(Task.assignee_member_id.in_(filters.assignee_ids))
    if filters.status is not None:
        query = query.where(Task.status == filters.status)
    if filters.overdue is True:
        query = query.where(Task.status == TaskStatus.PENDING, Task.due_at < func.now())
    elif filters.overdue is False:
        query = query.where(or_(Task.status == TaskStatus.COMPLETED, Task.due_at >= func.now()))
    if filters.due_after is not None:
        query = query.where(Task.due_at >= filters.due_after)
    if filters.due_before is not None:
        query = query.where(Task.due_at < filters.due_before)
    if filters.q:
        like = "%" + _escape_like(filters.q) + "%"
        query = query.where(or_(Task.title.ilike(like, escape="\\"), Task.description.ilike(like, escape="\\")))

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = list(
        db.scalars(_eager(query).order_by(*TASK_SORTS[sort]).offset((page - 1) * page_size).limit(page_size))
    )
    return tasks_out(db, db.get(Workspace, workspace_id), rows), total


def activity(db: Session, workspace_id: uuid.UUID, task_id: uuid.UUID) -> list[dict]:
    _get(db, workspace_id, task_id)
    members: dict[uuid.UUID, Member] = {}

    def summary(member_id) -> Optional[dict]:
        if not member_id:
            return None
        key = uuid.UUID(str(member_id))
        if key not in members:
            m = db.scalar(select(Member).options(selectinload(Member.telegram_user)).where(Member.id == key))
            if m is None:
                return None
            members[key] = m
        return member_summary(members[key])

    items: list[dict] = []
    for e in db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.workspace_id == workspace_id)):
        item = {"kind": "event", "at": e.created_at, "event_type": e.type, "source": e.source, "actor": summary(e.actor_member_id)}
        if e.type == TaskEventType.REASSIGNED:
            item["reassigned_from"] = summary(e.data.get("from"))
            item["reassigned_to"] = summary(e.data.get("to"))
        elif e.type == TaskEventType.DUE_CHANGED:
            item["previous_due_at"] = e.data.get("previous_due_at")
            item["new_due_at"] = e.data.get("new_due_at")
        elif e.type == TaskEventType.UPDATED:
            item["changed"] = e.data.get("changed", [])
        items.append(item)
    for n in db.scalars(select(Notification).where(Notification.task_id == task_id, Notification.workspace_id == workspace_id)):
        items.append(
            {
                "kind": "notification",
                "at": n.sent_at or n.updated_at or n.created_at,
                "notification_type": n.type,
                "notification_status": n.status,
                "recipient": summary(n.member_id),
                "error": n.last_error if n.status.value in ("failed", "cancelled") else None,
            }
        )
    items.sort(key=lambda i: i["at"])
    return items
