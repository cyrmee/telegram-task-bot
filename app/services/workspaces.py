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
    OnboardingStep,
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
            display_name=user.name,
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
    onboarding = changes.pop("onboarding", None) or {}
    if onboarding.get("dismissed") is not None:
        workspace.onboarding_dismissed_at = datetime.now(timezone.utc) if onboarding["dismissed"] else None
    if onboarding.get("skipped_steps") is not None:
        workspace.onboarding_skipped_steps = sorted({OnboardingStep(s).value for s in onboarding["skipped_steps"]})
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
    pending = (
        select(func.count())
        .select_from(other)
        .where(other.workspace_id == Workspace.id, other.status == MemberStatus.PENDING)
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
        select(
            Workspace.id,
            Workspace.name,
            Member.role,
            has_bot,
            members,
            pending,
            Member.telegram_user_id.is_not(None),
            projects,
            tasks,
            Workspace.onboarding_dismissed_at,
            Workspace.onboarding_skipped_steps,
        )
        .join(Member, Member.workspace_id == Workspace.id)
        .where(Member.user_id == user_id, Member.status == MemberStatus.ACTIVE)
        .order_by(Workspace.created_at)
    ).all()
    return [
        {
            "id": r[0],
            "name": r[1],
            "role": r[2],
            "onboarding": {
                "has_bot": r[3],
                "member_count": r[4],
                "pending_member_count": r[5],
                "manager_telegram_linked": r[6],
                "project_count": r[7],
                "task_count": r[8],
                "dismissed_at": r[9],
                "skipped_steps": r[10],
            },
        }
        for r in rows
    ]


def workspace_out(workspace: Workspace) -> dict:
    """API shape for a workspace row (schemas.Workspace)."""
    return {
        "id": workspace.id,
        "name": workspace.name,
        "timezone": workspace.timezone,
        "reminder_lead_hours": workspace.reminder_lead_hours,
        "onboarding": {
            "dismissed_at": workspace.onboarding_dismissed_at,
            "skipped_steps": workspace.onboarding_skipped_steps,
        },
        "created_at": workspace.created_at,
        "updated_at": workspace.updated_at,
    }


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
