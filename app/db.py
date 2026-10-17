"""Database engine, session factory and the FastAPI session dependency."""
import os
from collections.abc import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()


def normalize_database_url(url: str) -> str:
    """Accept the URL shapes Render/Neon hand out and return one SQLAlchemy takes."""
    url = url.strip()
    # Pin the driver: SQLAlchemy 2.1 maps a bare postgresql:// to psycopg 3.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg2://" + url[len(prefix):]
    is_local = any(h in url for h in ("localhost", "127.0.0.1", "@postgres:", "@db:"))
    if not is_local and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return normalize_database_url(url)


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
engine = None


def configure(url: str | None = None) -> None:
    """(Re)bind SessionLocal. Called at import; tests call it with their own DB."""
    global engine
    if engine is not None:
        engine.dispose()
    engine = create_engine(normalize_database_url(url) if url else get_database_url(), pool_pre_ping=True)
    SessionLocal.configure(bind=engine)


if os.environ.get("DATABASE_URL"):
    configure()


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
