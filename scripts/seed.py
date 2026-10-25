"""Seed a dev workspace with members, projects and tasks.

    python scripts/seed.py [--owner-email you@example.com]

Idempotent: the seed workspace has a fixed id and is deleted (cascading) and
recreated on every run. Other workspaces are left alone. Run migrations first.

--owner-email links the workspace to an existing better-auth account, so it
shows up when you log in to the dashboard. Without it the owner is a
placeholder user id. No bot is seeded: connect a personal dev bot through
the API so its token gets encrypted properly.
"""
import argparse
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    ChangeSource,
    Invite,
    Member,
    MemberRole,
    MemberStatus,
    Project,
    ProjectStatus,
    Task,
    TaskEvent,
    TaskEventType,
    TaskStatus,
    TelegramUser,
    Workspace,
)

SEED_WORKSPACE_ID = uuid.UUID("00000000-0000-4000-8000-000000005eed")
PLACEHOLDER_OWNER = "seed-owner"

# Fake Telegram ids in a range real accounts don't use in dev.
TEAM = [
    # telegram_user_id, username, first, last, status
    (9_000_000_001, "abebe_k", "Abebe", "Kebede", MemberStatus.ACTIVE),
    (9_000_000_002, "hana_t", "Hana", "Tesfaye", MemberStatus.ACTIVE),
    (9_000_000_003, "dawit_m", "Dawit", "Mengistu", MemberStatus.ACTIVE),
    (9_000_000_004, "sara_g", "Sara", "Girma", MemberStatus.UNREACHABLE),
]

PROJECTS = [
    ("Client: Habesha Coffee rebrand", "Logo refresh, packaging and launch posts.", ProjectStatus.ACTIVE),
    ("Nest training launch", "Prepare and run the team training sessions.", ProjectStatus.ACTIVE),
    ("Q3 social calendar", None, ProjectStatus.ARCHIVED),
]

# project index, assignee index (0 = manager), title, due offset in days, completed?
TASKS = [
    (0, 1, "Draft three logo directions", -6, True),
    (0, 2, "Collect packaging dielines from printer", -3, True),
    (0, 1, "Present logo directions to client", -1, False),  # overdue
    (0, 3, "Write launch post captions (EN + AM)", 1, False),
    (0, 4, "Shoot product photos", 2, False),
    (0, 2, "Final packaging artwork", 6, False),
    (1, 0, "Book training room", -4, True),
    (1, 3, "Update training deck with v1 screens", -2, False),  # overdue
    (1, 1, "Create practice workspace for trainees", 0.5, False),
    (1, 2, "Print quick-start handouts", 3, False),
    (1, 0, "Send training invites", 4, False),
    (2, 3, "Schedule September posts", -40, True),
    (2, 1, "Monthly engagement report", -35, True),
]


def resolve_owner(session, email: str | None) -> tuple[str, str | None]:
    """(better-auth user id, their name) for the seed workspace's owner."""
    if not email:
        return PLACEHOLDER_OWNER, "Seed Owner"
    if session.execute(text("SELECT to_regclass('public.\"user\"')")).scalar() is None:
        sys.exit("better-auth's \"user\" table doesn't exist; sign up in the dashboard first or drop --owner-email")
    row = session.execute(text('SELECT id, name FROM "user" WHERE email = :e'), {"e": email}).first()
    if row is None:
        sys.exit(f"No better-auth user with email {email}")
    return row.id, row.name or email


def seed(owner_email: str | None) -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal.begin() as session:
        owner_user_id, owner_name = resolve_owner(session, owner_email)
        session.execute(delete(Workspace).where(Workspace.id == SEED_WORKSPACE_ID))

        ws = Workspace(
            id=SEED_WORKSPACE_ID,
            name="Gezat Communications (seed)",
            owner_user_id=owner_user_id,
            timezone="Africa/Addis_Ababa",
            reminder_lead_hours=24,
        )
        session.add(ws)
        session.flush()

        manager = Member(
            workspace_id=ws.id,
            user_id=owner_user_id,
            display_name=owner_name,
            role=MemberRole.MANAGER,
            status=MemberStatus.ACTIVE,
            joined_at=now - timedelta(days=30),
        )
        session.add(manager)
        session.flush()

        invite = Invite(
            workspace_id=ws.id,
            token="seed-team-invite",
            created_by_member_id=manager.id,
            expires_at=now + timedelta(days=7),
            max_uses=None,
            use_count=len(TEAM),
        )
        session.add(invite)
        session.add(
            Invite(
                workspace_id=ws.id,
                token="seed-expired-invite",
                created_by_member_id=manager.id,
                expires_at=now - timedelta(days=1),
            )
        )
        session.flush()

        people = [manager]
        for tg_id, username, first, last, status in TEAM:
            session.merge(TelegramUser(telegram_user_id=tg_id, username=username, first_name=first, last_name=last))
            m = Member(
                workspace_id=ws.id,
                telegram_user_id=tg_id,
                role=MemberRole.MEMBER,
                status=status,
                private_chat_id=tg_id,
                invite_id=invite.id,
                joined_at=now - timedelta(days=20),
            )
            session.add(m)
            people.append(m)
        session.add(
            Member(
                workspace_id=ws.id,
                invited_username="meron_new",
                role=MemberRole.MEMBER,
                status=MemberStatus.PENDING,
            )
        )
        session.flush()

        projects = []
        for name, description, status in PROJECTS:
            p = Project(
                workspace_id=ws.id,
                name=name,
                description=description,
                status=status,
                archived_at=now - timedelta(days=30) if status == ProjectStatus.ARCHIVED else None,
                created_by_member_id=manager.id,
            )
            session.add(p)
            projects.append(p)
        session.flush()

        for number, (p_idx, a_idx, title, due_days, done) in enumerate(TASKS, start=1):
            due_at = now + timedelta(days=due_days)
            completed_at = min(due_at, now) - timedelta(hours=3) if done else None
            assignee = people[a_idx]
            task = Task(
                workspace_id=ws.id,
                number=number,
                project_id=projects[p_idx].id,
                assignee_member_id=assignee.id,
                title=title,
                due_at=due_at,
                status=TaskStatus.COMPLETED if done else TaskStatus.PENDING,
                completed_at=completed_at,
                completed_via=ChangeSource.TELEGRAM if done else None,
                completed_by_member_id=assignee.id if done else None,
                created_by_member_id=manager.id,
                created_at=due_at - timedelta(days=5),
            )
            session.add(task)
            session.flush()
            session.add(
                TaskEvent(
                    workspace_id=ws.id,
                    task_id=task.id,
                    type=TaskEventType.CREATED,
                    source=ChangeSource.DASHBOARD,
                    actor_member_id=manager.id,
                    created_at=task.created_at,
                )
            )
            if done:
                session.add(
                    TaskEvent(
                        workspace_id=ws.id,
                        task_id=task.id,
                        type=TaskEventType.COMPLETED,
                        source=ChangeSource.TELEGRAM,
                        actor_member_id=assignee.id,
                        created_at=completed_at,
                    )
                )
        ws.next_task_number = len(TASKS) + 1

    print(
        f"Seeded workspace {SEED_WORKSPACE_ID} (owner {owner_user_id}): "
        f"{len(people) + 1} members, {len(PROJECTS)} projects, {len(TASKS)} tasks"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--owner-email", help="better-auth user to own the seed workspace")
    seed(parser.parse_args().owner_email)
