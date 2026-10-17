"""Alembic migrations: empty DB round trip, model/migration drift, legacy DBs.

Needs a Postgres server. TEST_DATABASE_ADMIN_URL (default: the local
nest_postgres container) must be able to CREATE/DROP DATABASE; each test
gets a throwaway database.
"""
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.db import normalize_database_url
from app.models import Base

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = normalize_database_url(
    os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql://nest:nest@localhost:5432/postgres")
)
V1_TABLES = {
    "workspaces", "workspace_bots", "telegram_users", "members", "invites",
    "projects", "tasks", "task_reminders", "task_events", "notifications",
}


@pytest.fixture
def db_url():
    name = f"nest_mig_test_{uuid.uuid4().hex[:10]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def alembic_cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def tables(url: str, schema: str = "public") -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names(schema=schema))
    finally:
        engine.dispose()


def test_upgrade_downgrade_round_trip(db_url):
    cfg = alembic_cfg(db_url)
    command.upgrade(cfg, "head")
    assert tables(db_url) == V1_TABLES | {"alembic_version"}

    command.downgrade(cfg, "base")
    assert tables(db_url) <= {"alembic_version"}

    command.upgrade(cfg, "head")
    assert tables(db_url) == V1_TABLES | {"alembic_version"}


def test_models_match_migrations(db_url):
    command.upgrade(alembic_cfg(db_url), "head")
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})
            assert compare_metadata(ctx, Base.metadata) == []
    finally:
        engine.dispose()


def _create_legacy_db(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        # better-auth (must survive untouched)
        conn.execute(text('CREATE TABLE "user" (id text PRIMARY KEY, email text NOT NULL)'))
        conn.execute(text("INSERT INTO \"user\" VALUES ('u1', 'a@example.com')"))
        # old bot
        conn.execute(text("CREATE TYPE taskstatus AS ENUM ('NEW', 'IN_PROGRESS', 'DONE')"))
        conn.execute(text("CREATE TABLE users (id serial PRIMARY KEY, telegram_id bigint UNIQUE NOT NULL)"))
        conn.execute(text("CREATE TABLE workspaces (id varchar PRIMARY KEY, name varchar NOT NULL)"))
        conn.execute(text("CREATE TABLE projects (id serial PRIMARY KEY, name varchar)"))
        conn.execute(text(
            "CREATE TABLE tasks (id serial PRIMARY KEY, project_id int REFERENCES projects(id), status taskstatus)"
        ))
        conn.execute(text("INSERT INTO tasks (status) VALUES ('DONE')"))
        # Prisma
        conn.execute(text('CREATE TABLE "Workspace" (id text PRIMARY KEY)'))
    engine.dispose()


def test_legacy_tables_moved_aside_then_dropped(db_url):
    _create_legacy_db(db_url)
    cfg = alembic_cfg(db_url)

    command.upgrade(cfg, "0001")
    assert tables(db_url, "legacy") == {"users", "workspaces", "projects", "tasks", "Workspace"}
    assert V1_TABLES <= tables(db_url)
    assert "user" in tables(db_url)

    command.downgrade(cfg, "base")
    assert {"users", "workspaces", "projects", "tasks", "Workspace", "user"} <= tables(db_url)
    engine = create_engine(db_url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT status::text FROM tasks")).scalar() == "DONE"
        assert conn.execute(text("SELECT to_regnamespace('legacy')")).scalar() is None
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT to_regnamespace('legacy')")).scalar() is None
        assert conn.execute(text('SELECT email FROM "user"')).scalar() == "a@example.com"
    engine.dispose()
    assert tables(db_url) == V1_TABLES | {"alembic_version", "user"}


def test_workspace_delete_cascades(db_url):
    command.upgrade(alembic_cfg(db_url), "head")
    engine = create_engine(db_url)
    with engine.begin() as conn:
        ws = conn.execute(text("INSERT INTO workspaces (name, owner_user_id) VALUES ('W', 'u1') RETURNING id")).scalar()
        m = conn.execute(
            text("INSERT INTO members (workspace_id, user_id, role) VALUES (:w, 'u1', 'manager') RETURNING id"),
            {"w": ws},
        ).scalar()
        p = conn.execute(text("INSERT INTO projects (workspace_id, name) VALUES (:w, 'P') RETURNING id"), {"w": ws}).scalar()
        conn.execute(
            text(
                "INSERT INTO tasks (workspace_id, number, project_id, assignee_member_id, title, due_at) "
                "VALUES (:w, 1, :p, :m, 'T', now())"
            ),
            {"w": ws, "p": p, "m": m},
        )
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM workspaces WHERE id = :w"), {"w": ws})
        assert conn.execute(text("SELECT count(*) FROM tasks")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM members")).scalar() == 0
    engine.dispose()
