"""Shared fixtures: a migrated throwaway Postgres DB and a fake better-auth JWT issuer.

TEST_DATABASE_ADMIN_URL (default: local nest_postgres) must be allowed to
CREATE/DROP DATABASE.
"""
import json
import os
import time
import uuid
from pathlib import Path

import jwt
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt import PyJWKClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import auth, db
from app.config import get_settings
from app.db import normalize_database_url

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = normalize_database_url(
    os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql://nest:nest@localhost:5432/postgres")
)
V1_TABLES = [
    "notifications", "task_events", "task_reminders", "tasks", "projects", "invites",
    "members", "telegram_users", "workspace_bots", "workspaces",
]


def alembic_cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def _admin():
    return create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")


def create_test_database(prefix: str) -> str:
    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    with _admin().connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    return make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)


def drop_test_database(url: str) -> None:
    name = make_url(url).database
    with _admin().connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def migrated_db_url():
    """One migrated database per test session; SessionLocal is bound to it."""
    url = create_test_database("nest_test")
    try:
        command.upgrade(alembic_cfg(url), "head")
        db.configure(url)
        yield url
    finally:
        if db.engine is not None:
            db.engine.dispose()
        drop_test_database(url)


@pytest.fixture
def db_session(migrated_db_url):
    with db.SessionLocal() as session:
        yield session
    with db.engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(V1_TABLES)} CASCADE"))


class FakeIssuer:
    """Stands in for the dashboard's better-auth JWT plugin."""

    kid = "test-key-1"

    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        public = json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(self.key.public_key()))
        self.jwks = {"keys": [{**public, "kid": self.kid, "alg": "EdDSA", "use": "sig"}]}

    def token(self, sub: str, *, expires_in: int = 900, key=None, algorithm="EdDSA", **overrides) -> str:
        s = get_settings()
        now = int(time.time())
        claims = {
            "sub": sub,
            "iss": s.auth_jwt_issuer,
            "aud": s.auth_jwt_audience,
            "iat": now,
            "exp": now + expires_in,
            "email": f"{sub}@example.com",
            "name": sub.title(),
            **overrides,
        }
        return jwt.encode(claims, key or self.key, algorithm=algorithm, headers={"kid": self.kid})

    def headers(self, sub: str, **kw) -> dict:
        return {"Authorization": f"Bearer {self.token(sub, **kw)}"}


class _StaticJWKClient(PyJWKClient):
    def __init__(self, jwks: dict):
        super().__init__("http://jwks.invalid/jwks")
        self._static = jwks

    def fetch_data(self):
        return self._static


@pytest.fixture
def issuer():
    fake = FakeIssuer()
    auth.set_jwks_client(_StaticJWKClient(fake.jwks))
    yield fake
    auth.set_jwks_client(None)
