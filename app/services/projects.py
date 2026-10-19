"""Projects ("Groups"). Every function is scoped to one workspace."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.models import Member, Project, ProjectStatus, Task
from app.services.views import project_out, task_counts_by_project


def get_project(db: Session, workspace_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    project = db.scalar(select(Project).where(Project.workspace_id == workspace_id, Project.id == project_id))
    if project is None:
        raise ApiError(404, "not_found", "Project not found.")
    return project


def out(db: Session, project: Project) -> dict:
    counts = task_counts_by_project(db, project.workspace_id, [project.id])[project.id]
    return project_out(project, counts)


def list_projects(db: Session, workspace_id: uuid.UUID, status: str = "active") -> list[dict]:
    query = select(Project).where(Project.workspace_id == workspace_id)
    if status != "all":
        query = query.where(Project.status == ProjectStatus(status))
    projects = list(db.scalars(query.order_by(Project.name, Project.created_at)))
    counts = task_counts_by_project(db, workspace_id, [p.id for p in projects])
    return [project_out(p, counts[p.id]) for p in projects]


def create_project(db: Session, actor: Member, name: str, description: str | None) -> Project:
    project = Project(
        workspace_id=actor.workspace_id,
        name=name.strip(),
        description=description,
        created_by_member_id=actor.id,
    )
    db.add(project)
    db.commit()
    return project


def update_project(db: Session, project: Project, changes: dict) -> Project:
    if changes.get("name", "") is None:
        raise ApiError(422, "validation_error", "Some fields are invalid.", {"fields": [{"field": "name", "message": "Cannot be null"}]})
    if "name" in changes:
        changes["name"] = changes["name"].strip()
    for field, value in changes.items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


def set_archived(db: Session, project: Project, archived: bool) -> Project:
    target = ProjectStatus.ARCHIVED if archived else ProjectStatus.ACTIVE
    if project.status != target:
        project.status = target
        project.archived_at = datetime.now(timezone.utc) if archived else None
        db.commit()
        db.refresh(project)
    return project


def delete_project(db: Session, project: Project) -> None:
    if db.scalar(select(exists().where(Task.project_id == project.id))):
        raise ApiError(409, "project_not_empty", "This project still has tasks. Archive it instead.")
    db.delete(project)
    db.commit()
