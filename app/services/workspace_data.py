"""Workspace export (everything a manager may take with them) and deletion (D4)."""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.api.errors import ApiError
from app.auth import AuthUser
from app.models import Invite, Member, Notification, Project, Task, TaskEvent, Workspace, WorkspaceBot
from app.services.members import members_out
from app.services.views import project_out, task_counts_by_project
from app.services.workspaces import workspace_out

logger = logging.getLogger(__name__)


def export_workspace(db: Session, workspace_id: uuid.UUID) -> dict:
    ws = db.get(Workspace, workspace_id)
    bot = db.get(WorkspaceBot, workspace_id)
    members = list(db.scalars(
        select(Member).options(selectinload(Member.telegram_user)).where(Member.workspace_id == workspace_id).order_by(Member.created_at)
    ))
    projects = list(db.scalars(select(Project).where(Project.workspace_id == workspace_id).order_by(Project.created_at)))
    counts = task_counts_by_project(db, workspace_id, [p.id for p in projects])
    tasks = list(db.scalars(
        select(Task).options(selectinload(Task.reminders)).where(Task.workspace_id == workspace_id).order_by(Task.number)
    ))
    events = db.scalars(select(TaskEvent).where(TaskEvent.workspace_id == workspace_id).order_by(TaskEvent.created_at))
    invites = db.scalars(select(Invite).where(Invite.workspace_id == workspace_id).order_by(Invite.created_at))
    notifications = db.scalars(select(Notification).where(Notification.workspace_id == workspace_id).order_by(Notification.created_at))
    return {
        "exported_at": datetime.now(timezone.utc),
        "workspace": workspace_out(ws),
        "bot": (
            {"username": bot.username, "telegram_bot_id": bot.telegram_bot_id, "status": bot.status.value, "connected_at": bot.connected_at}
            if bot else None
        ),
        "members": members_out(db, members),
        "projects": [project_out(p, counts[p.id]) for p in projects],
        "tasks": [
            {
                "id": t.id, "number": t.number, "title": t.title, "description": t.description,
                "project_id": t.project_id, "assignee_id": t.assignee_member_id, "due_at": t.due_at,
                "status": t.status, "completed_at": t.completed_at, "completed_via": t.completed_via,
                "completed_by_id": t.completed_by_member_id, "created_by_id": t.created_by_member_id,
                "reminder_offsets_minutes": sorted(r.offset_minutes for r in t.reminders),
                "created_at": t.created_at, "updated_at": t.updated_at,
            }
            for t in tasks
        ],
        "task_events": [
            {"id": e.id, "task_id": e.task_id, "type": e.type, "source": e.source, "actor_id": e.actor_member_id,
             "data": e.data, "created_at": e.created_at}
            for e in events
        ],
        "invites": [
            {"id": i.id, "role": i.role, "expires_at": i.expires_at, "max_uses": i.max_uses, "use_count": i.use_count,
             "revoked_at": i.revoked_at, "created_by_id": i.created_by_member_id, "created_at": i.created_at}
            for i in invites
        ],
        "notifications": [
            {"id": n.id, "task_id": n.task_id, "member_id": n.member_id, "type": n.type, "status": n.status,
             "attempts": n.attempts, "sent_at": n.sent_at, "last_error": n.last_error, "created_at": n.created_at}
            for n in notifications
        ],
    }


def check_delete_confirmation(db: Session, workspace_id: uuid.UUID, confirm_name: str) -> Workspace:
    ws = db.get(Workspace, workspace_id)
    if confirm_name != ws.name:
        raise ApiError(422, "confirmation_mismatch", "Type the workspace name exactly to confirm deletion.")
    return ws


def delete_workspace(db: Session, workspace_id: uuid.UUID, user: AuthUser) -> None:
    """Hard-delete the workspace; the FKs cascade to everything it owns."""
    ws = db.get(Workspace, workspace_id)
    name = ws.name
    db.execute(delete(Workspace).where(Workspace.id == workspace_id))
    db.commit()
    # Audit line: who deleted what, and when. No personal data beyond the actor's id.
    logger.warning(
        "workspace deleted",
        extra={"audit": "workspace.deleted", "workspace_id": str(workspace_id), "workspace_name": name, "actor_user_id": user.id},
    )
