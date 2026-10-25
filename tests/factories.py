"""Row factories for tests. Each commits and returns the ORM object."""
import uuid
from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import (
    Member,
    MemberRole,
    MemberStatus,
    Project,
    Task,
    TaskStatus,
    TelegramUser,
    Workspace,
)

_telegram_ids = count(900_000_000)


def make_workspace(db: Session, owner: str = "alice", name: str = "Acme", tz: str = "UTC") -> Workspace:
    ws = Workspace(name=name, owner_user_id=owner, timezone=tz)
    db.add(ws)
    db.flush()
    db.add(Member(workspace_id=ws.id, user_id=owner, display_name=owner.title(), role=MemberRole.MANAGER,
                  status=MemberStatus.ACTIVE, joined_at=datetime.now(timezone.utc)))
    db.commit()
    return ws


def manager_of(db: Session, ws: Workspace) -> Member:
    return db.query(Member).filter_by(workspace_id=ws.id, user_id=ws.owner_user_id).one()


def make_member(db: Session, ws: Workspace, name: str = "Hana", role: MemberRole = MemberRole.MEMBER,
                status: MemberStatus = MemberStatus.ACTIVE, telegram_id: Optional[int] = None) -> Member:
    tg_id = telegram_id or next(_telegram_ids)
    if db.get(TelegramUser, tg_id) is None:
        db.add(TelegramUser(telegram_user_id=tg_id, username=name.lower(), first_name=name))
        db.flush()
    member = Member(workspace_id=ws.id, telegram_user_id=tg_id, private_chat_id=tg_id, role=role, status=status,
                    joined_at=datetime.now(timezone.utc))
    db.add(member)
    db.commit()
    return member


def make_project(db: Session, ws: Workspace, name: str = "Launch") -> Project:
    project = Project(workspace_id=ws.id, name=name)
    db.add(project)
    db.commit()
    return project


def make_task(db: Session, project: Project, assignee: Member, title: str = "Task",
              due_in: timedelta = timedelta(days=1), completed: bool = False) -> Task:
    number = db.execute(
        update(Workspace).where(Workspace.id == project.workspace_id)
        .values(next_task_number=Workspace.next_task_number + 1).returning(Workspace.next_task_number - 1)
    ).scalar_one()
    now = datetime.now(timezone.utc)
    task = Task(
        workspace_id=project.workspace_id, number=number, project_id=project.id, assignee_member_id=assignee.id,
        title=title, due_at=now + due_in,
        status=TaskStatus.COMPLETED if completed else TaskStatus.PENDING, completed_at=now if completed else None,
    )
    db.add(task)
    db.commit()
    return task


def uid() -> str:
    return str(uuid.uuid4())
