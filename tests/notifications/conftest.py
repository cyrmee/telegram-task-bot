"""Fixtures for outbox tests: one migrated throwaway database per test session,
emptied between tests. Uses TEST_DATABASE_ADMIN_URL like tests/test_migrations.py."""
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.db import normalize_database_url
from app.models import Member, MemberRole, Project, Task, TelegramUser, Workspace

ROOT = Path(__file__).resolve().parents[2]
ADMIN_URL = normalize_database_url(
    os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql://nest:nest@localhost:5432/postgres")
)


@pytest.fixture(scope="session")
def engine():
    name = f"nest_outbox_test_{uuid.uuid4().hex[:10]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    eng = create_engine(url)
    try:
        yield eng
    finally:
        eng.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def session_factory(engine):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE workspaces, telegram_users CASCADE"))


class Clock:
    """Worker clock. Starts slightly ahead of the DB, so rows enqueued with the
    default next_attempt_at (server now()) are already due."""

    def __init__(self):
        self.now = datetime.now(timezone.utc) + timedelta(seconds=5)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return Clock()


@dataclass
class World:
    workspace_id: uuid.UUID
    member_id: uuid.UUID
    chat_id: int
    task_id: uuid.UUID


_ids = count(1)


@pytest.fixture
def make_world(session_factory):
    """Create a workspace with one linked member, a project and a task assigned to them."""

    def make(*, linked: bool = True) -> World:
        n = next(_ids)
        tg_id = 1_000_000 + n
        with session_factory() as s, s.begin():
            ws = Workspace(name=f"WS {n}", owner_user_id=f"owner-{n}")
            s.add(ws)
            s.flush()
            s.add(TelegramUser(telegram_user_id=tg_id, username=f"user{n}"))
            s.flush()
            member = Member(
                workspace_id=ws.id,
                role=MemberRole.MEMBER,
                telegram_user_id=tg_id,
                private_chat_id=tg_id if linked else None,
            )
            project = Project(workspace_id=ws.id, name="Launch")
            s.add_all([member, project])
            s.flush()
            task = Task(
                workspace_id=ws.id,
                number=1,
                project_id=project.id,
                assignee_member_id=member.id,
                title="Write copy",
                due_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
            s.add(task)
            s.flush()
            return World(ws.id, member.id, tg_id, task.id)

    return make
