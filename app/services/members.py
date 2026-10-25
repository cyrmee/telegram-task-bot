"""Workspace members: list, add by @username, change role, remove."""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.api.errors import ApiError
from app.models import (
    Invite,
    Member,
    MemberRole,
    MemberStatus,
    Notification,
    NotificationStatus,
    Task,
    TaskStatus,
    TelegramUser,
)
from app.services import tasks as task_service
from app.services.views import display_name


def normalize_username(username: str) -> str:
    return username.strip().lstrip("@").lower()


def _open_counts(db: Session, member_ids: list[uuid.UUID]) -> dict:
    if not member_ids:
        return {}
    rows = db.execute(
        select(Task.assignee_member_id, func.count())
        .where(Task.assignee_member_id.in_(member_ids), Task.status == TaskStatus.PENDING)
        .group_by(Task.assignee_member_id)
    ).all()
    return {member_id: n for member_id, n in rows}


def members_out(db: Session, members: list[Member]) -> list[dict]:
    counts = _open_counts(db, [m.id for m in members])
    out = []
    for m in members:
        tg = m.telegram_user
        out.append(
            {
                "id": m.id,
                "role": m.role,
                "status": m.status,
                "display_name": display_name(m),
                "user_id": m.user_id,
                "email": None,  # better-auth owns emails; the dashboard has its own
                "telegram": (
                    {"user_id": tg.telegram_user_id, "username": tg.username, "first_name": tg.first_name, "last_name": tg.last_name}
                    if tg
                    else None
                ),
                "invited_username": m.invited_username,
                "open_task_count": counts.get(m.id, 0),
                "joined_at": m.joined_at,
                "created_at": m.created_at,
            }
        )
    return out


def member_out(db: Session, member: Member) -> dict:
    db.refresh(member)
    member = db.scalar(select(Member).options(selectinload(Member.telegram_user)).where(Member.id == member.id))
    return members_out(db, [member])[0]


def get_member(db: Session, workspace_id: uuid.UUID, member_id: uuid.UUID) -> Member:
    member = db.scalar(
        select(Member)
        .options(selectinload(Member.telegram_user))
        .where(Member.workspace_id == workspace_id, Member.id == member_id)
    )
    if member is None:
        raise ApiError(404, "not_found", "Member not found.")
    return member


def list_members(
    db: Session, workspace_id: uuid.UUID, statuses: Optional[list[MemberStatus]] = None, role: Optional[MemberRole] = None
) -> list[dict]:
    query = select(Member).options(selectinload(Member.telegram_user)).where(Member.workspace_id == workspace_id)
    if statuses:
        query = query.where(Member.status.in_(statuses))
    else:
        query = query.where(Member.status != MemberStatus.REMOVED)
    if role is not None:
        query = query.where(Member.role == role)
    members = list(db.scalars(query.order_by(Member.role, Member.created_at)))
    return members_out(db, members)


def add_by_username(db: Session, actor: Member, username: str) -> Member:
    name = normalize_username(username)
    existing = db.scalar(
        select(Member)
        .outerjoin(TelegramUser, TelegramUser.telegram_user_id == Member.telegram_user_id)
        .where(
            Member.workspace_id == actor.workspace_id,
            or_(func.lower(TelegramUser.username) == name, func.lower(Member.invited_username) == name),
        )
    )
    if existing is not None and existing.status != MemberStatus.REMOVED:
        raise ApiError(409, "member_exists", f"@{name} is already in this workspace.")
    if existing is not None:  # removed earlier: bring back as a pending plain member
        existing.role = MemberRole.MEMBER
        existing.status = MemberStatus.PENDING
        existing.removed_at = None
        existing.invited_username = name
        member = existing
    else:
        member = Member(workspace_id=actor.workspace_id, invited_username=name, role=MemberRole.MEMBER, status=MemberStatus.PENDING)
        db.add(member)
    db.commit()
    return member


def _active_managers(db: Session, workspace_id: uuid.UUID) -> int:
    # Lock the manager rows so two concurrent demotions/removals can't both pass the check.
    return len(
        db.scalars(
            select(Member.id)
            .where(Member.workspace_id == workspace_id, Member.role == MemberRole.MANAGER, Member.status == MemberStatus.ACTIVE)
            .with_for_update()
        ).all()
    )


def _guard_last_manager(db: Session, member: Member) -> None:
    if member.role == MemberRole.MANAGER and member.status == MemberStatus.ACTIVE and _active_managers(db, member.workspace_id) <= 1:
        raise ApiError(409, "last_manager", "A workspace needs at least one manager.")


def update_role(db: Session, actor: Member, member_id: uuid.UUID, role: MemberRole) -> Member:
    member = get_member(db, actor.workspace_id, member_id)
    if member.status == MemberStatus.REMOVED:
        raise ApiError(404, "not_found", "Member not found.")
    if member.role == role:
        return member
    if role == MemberRole.MEMBER:
        _guard_last_manager(db, member)
    member.role = role
    db.commit()
    return member


def remove_member(db: Session, actor: Member, member_id: uuid.UUID, reassign_to: Optional[uuid.UUID]) -> Member:
    """Soft-remove. Pending tasks move to `reassign_to` (PRD: every task has an assignee)."""
    member = get_member(db, actor.workspace_id, member_id)
    if member.status == MemberStatus.REMOVED:
        return member
    _guard_last_manager(db, member)
    open_ids = list(
        db.scalars(select(Task.id).where(Task.assignee_member_id == member.id, Task.status == TaskStatus.PENDING))
    )
    if open_ids:
        if reassign_to is None:
            raise ApiError(
                409, "member_has_open_tasks",
                f"{display_name(member)} has {len(open_ids)} pending task(s). Choose who takes them over.",
                {"openTaskCount": len(open_ids)},
            )
        if reassign_to == member.id:
            raise ApiError(422, "assignee_not_active", "Pick someone other than the member being removed.")
        target = get_member(db, actor.workspace_id, reassign_to)
        if target.status == MemberStatus.REMOVED:
            raise ApiError(422, "assignee_not_active", "That person was removed from the workspace.")
        for task_id in open_ids:
            task_service.update_task(db, actor, task_id, {"assignee_id": target.id})
    member = get_member(db, actor.workspace_id, member_id)
    member.status = MemberStatus.REMOVED
    member.removed_at = datetime.now(timezone.utc)
    db.execute(
        update(Notification)
        .where(Notification.member_id == member.id, Notification.status == NotificationStatus.QUEUED)
        .values(status=NotificationStatus.CANCELLED, last_error="member removed")
    )
    # Their self-link invites and the team invites they created stop working.
    db.execute(
        update(Invite)
        .where(
            Invite.workspace_id == member.workspace_id,
            Invite.revoked_at.is_(None),
            or_(Invite.member_id == member.id, Invite.created_by_member_id == member.id),
        )
        .values(revoked_at=func.now())
    )
    db.commit()
    return member
