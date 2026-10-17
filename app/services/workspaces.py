"""Workspace business logic, shared by the API and (later) the bot."""
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.api.errors import ApiError
from app.auth import AuthUser
from app.models import (
    BotStatus,
    Member,
    MemberRole,
    MemberStatus,
    Project,
    ProjectStatus,
    Task,
    Workspace,
    WorkspaceBot,
)


def validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ApiError(422, "invalid_timezone", f"{name!r} is not a known timezone.") from None
    if name not in available_timezones() and name != "UTC":
        raise ApiError(422, "invalid_timezone", f"{name!r} is not a known timezone.")
    return name


def create_workspace(db: Session, user: AuthUser, name: str, tz: str) -> Workspace:
    workspace = Workspace(name=name.strip(), timezone=validate_timezone(tz), owner_user_id=user.id)
    db.add(workspace)
    db.flush()
    db.add(
        Member(
            workspace_id=workspace.id,
            user_id=user.id,
            role=MemberRole.MANAGER,
            status=MemberStatus.ACTIVE,
            joined_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return workspace


def update_workspace(db: Session, workspace_id: uuid.UUID, changes: dict) -> Workspace:
    workspace = db.get(Workspace, workspace_id)
    cleared = sorted(k for k, v in changes.items() if v is None)
    if cleared:
        raise ApiError(
            422, "validation_error", "Some fields are invalid.",
            {"fields": [{"field": f, "message": "Cannot be null"} for f in cleared]},
        )
    if "timezone" in changes:
        validate_timezone(changes["timezone"])
    if "name" in changes:
        changes["name"] = changes["name"].strip()
    for field, value in changes.items():
        setattr(workspace, field, value)
    db.commit()
    db.refresh(workspace)
    return workspace


def workspaces_for_user(db: Session, user_id: str) -> list[dict]:
    """The user's workspaces with role and onboarding counts, one query."""
    other = aliased(Member)
    members = (
        select(func.count())
        .select_from(other)
        .where(
            other.workspace_id == Workspace.id,
            other.role == MemberRole.MEMBER,
            other.status == MemberStatus.ACTIVE,
        )
        .correlate(Workspace)
        .scalar_subquery()
    )
    projects = (
        select(func.count())
        .where(Project.workspace_id == Workspace.id, Project.status == ProjectStatus.ACTIVE)
        .scalar_subquery()
    )
    tasks = select(func.count()).where(Task.workspace_id == Workspace.id).scalar_subquery()
    has_bot = (
        select(WorkspaceBot.workspace_id)
        .where(WorkspaceBot.workspace_id == Workspace.id, WorkspaceBot.status == BotStatus.ACTIVE)
        .exists()
    )
    rows = db.execute(
        select(Workspace.id, Workspace.name, Member.role, has_bot, members, projects, tasks)
        .join(Member, Member.workspace_id == Workspace.id)
        .where(Member.user_id == user_id, Member.status == MemberStatus.ACTIVE)
        .order_by(Workspace.created_at)
    ).all()
    return [
        {
            "id": r[0],
            "name": r[1],
            "role": r[2],
            "onboarding": {"has_bot": r[3], "member_count": r[4], "project_count": r[5], "task_count": r[6]},
        }
        for r in rows
    ]


def manager_workspace_ids(db: Session, user_id: str) -> list[uuid.UUID]:
    """Workspaces whose realtime room this user may join."""
    return list(
        db.scalars(
            select(Member.workspace_id).where(
                Member.user_id == user_id,
                Member.status == MemberStatus.ACTIVE,
                Member.role == MemberRole.MANAGER,
            )
        )
    )
