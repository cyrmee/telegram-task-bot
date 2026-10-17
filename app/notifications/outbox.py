"""Transactional outbox: write notifications in the caller's transaction.

None of these functions commit. Call them inside the transaction that makes the
change (task assigned, due date moved, ...), so the notification exists exactly
when the change does. The worker (worker.py) delivers them.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Notification, NotificationStatus, NotificationType, Task


def assigned_key(task_id: uuid.UUID, member_id: uuid.UUID) -> str:
    return f"assigned:{task_id}:{member_id}"


def reminder_key(task_id: uuid.UUID, offset: str) -> str:
    """`offset` names the reminder slot, e.g. "pre-24h" or "overdue-2"."""
    return f"reminder:{task_id}:{offset}"


def enqueue(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
    type: NotificationType,
    idempotency_key: str,
    payload: dict[str, Any],
    task_id: Optional[uuid.UUID] = None,
    send_at: Optional[datetime] = None,
) -> Optional[uuid.UUID]:
    """Queue a DM to a member. Returns the new notification id, or None when a
    notification with this idempotency_key already exists (nothing is changed).

    `payload` comes from messages.message_payload(). `send_at` defers delivery
    (reminders); default is as soon as possible.
    """
    if task_id is not None:
        # notifications.task_id is a single-column FK (SET NULL on delete), so the
        # DB can't check its workspace; the member side is checked by a composite FK.
        task_workspace = session.scalar(select(Task.workspace_id).where(Task.id == task_id))
        if task_workspace != workspace_id:
            raise ValueError(f"task {task_id} is not in workspace {workspace_id}")

    values: dict[str, Any] = dict(
        workspace_id=workspace_id,
        member_id=member_id,
        task_id=task_id,
        type=type,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    if send_at is not None:
        values["next_attempt_at"] = send_at
    stmt = (
        insert(Notification)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[Notification.idempotency_key])
        .returning(Notification.id)
    )
    return session.scalar(stmt)


def cancel_queued(
    session: Session,
    *,
    task_id: uuid.UUID,
    types: Optional[Iterable[NotificationType]] = None,
    reason: str = "superseded",
) -> int:
    """Cancel a task's not-yet-sent notifications (e.g. reminders after the due
    date moved). Returns how many were cancelled. A row a worker is sending right
    now may still go out."""
    stmt = (
        update(Notification)
        .where(Notification.task_id == task_id, Notification.status == NotificationStatus.QUEUED)
        .values(status=NotificationStatus.CANCELLED, last_error=reason)
    )
    if types is not None:
        stmt = stmt.where(Notification.type.in_(list(types)))
    return session.execute(stmt).rowcount


def wake_member(session: Session, member_id: uuid.UUID) -> int:
    """Make a member's waiting notifications due now. Call when the member links
    their Telegram chat (B2), so DMs queued before they joined go out."""
    return _wake(session, Notification.member_id == member_id)


def wake_workspace(session: Session, workspace_id: uuid.UUID) -> int:
    """Make a workspace's waiting notifications due now. Call when a bot is
    (re)connected (B1)."""
    return _wake(session, Notification.workspace_id == workspace_id)


def _wake(session: Session, condition) -> int:
    now = datetime.now(timezone.utc)
    stmt = (
        update(Notification)
        .where(condition, Notification.status == NotificationStatus.QUEUED, Notification.next_attempt_at > now)
        # Only rows the worker already tried and deferred (they carry last_error);
        # reminders scheduled for later keep their time.
        .where(Notification.last_error.is_not(None))
        .values(next_attempt_at=now)
    )
    return session.execute(stmt).rowcount

