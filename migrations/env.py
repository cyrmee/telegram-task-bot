from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.db import get_database_url, normalize_database_url
from app.models import Base

config = context.config

if config.config_file_name is not None:
    # Keep app loggers alive when migrations run in-process (tests, scripts).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# Tables in public that Alembic must never create, alter or drop.
# better-auth owns its tables; the dashboard's `npx @better-auth/cli migrate` manages them.
UNMANAGED_TABLES = {"user", "session", "account", "verification", "jwks"}


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name in (None, "public")
    if type_ == "table":
        return name not in UNMANAGED_TABLES
    return True


def _url() -> str:
    # `alembic -x url=...` or a programmatic sqlalchemy.url (tests) overrides DATABASE_URL.
    override = context.get_x_argument(as_dictionary=True).get("url") or config.get_main_option("sqlalchemy.url")
    return normalize_database_url(override) if override else get_database_url()


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        connectable = create_engine(_url(), poolclass=pool.NullPool)
        with connectable.connect() as connection:
            _run(connection)
    else:
        _run(connectable)


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_name=include_name,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
