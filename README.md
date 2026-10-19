# Nest backend (`telegram-task-bot`)

FastAPI + python-telegram-bot + Postgres. This repo serves the REST API for the
[Nest dashboard](../nest-dashboard) and the Telegram bot that DMs tasks and reminders to members.
Product specs are in `../../docs` (PRD v1, UX & Flow Map). The API contract is
[`docs/api-v1.md`](docs/api-v1.md) and the schema is [`docs/data-model.md`](docs/data-model.md).

## Quick start (docker compose)

Requires Docker with Compose v2.24+, and `nest-dashboard` cloned next to this repo:

```
repos/
  telegram-task-bot/   <- you are here
  nest-dashboard/
```

```bash
cp .env.example .env          # defaults work; add your dev bot token later
make up
```

`make up` starts:

| Service     | URL                          | Notes                                                   |
|-------------|------------------------------|---------------------------------------------------------|
| `postgres`  | `localhost:5432`             | Postgres 16, user/password `nest`/`nest`, db `nest_db`  |
| `backend`   | http://localhost:8000/docs   | Runs `alembic upgrade head`, then uvicorn with reload   |
| `dashboard` | http://localhost:3000        | `npm ci`, better-auth migration, then `next dev`        |

It then seeds a demo workspace. The dashboard's first start runs `npm ci`, which takes
about a minute; follow it with `make logs`.

To see the seed data in the dashboard, sign up at http://localhost:3000, then link the seed
workspace to that account:

```bash
make seed OWNER_EMAIL=you@example.com
```

### Make targets

| Target         | What it does                                                           |
|----------------|------------------------------------------------------------------------|
| `make up`      | Build and start the stack, then seed                                   |
| `make down`    | Stop everything (data is kept)                                         |
| `make logs`    | Follow logs from all services                                          |
| `make migrate` | `alembic upgrade head` (also runs on every backend start)              |
| `make seed`    | Recreate the seed workspace. Takes an optional `OWNER_EMAIL=...`       |
| `make tunnel`  | Start ngrok and print the public URL (needs `NGROK_AUTHTOKEN`)         |
| `make ps`      | Show service status                                                    |
| `make reset`   | Stop everything and **delete the local database volume**               |

### Ports already taken?

Override them in `.env`: `POSTGRES_PORT`, `API_PORT`, `DASHBOARD_PORT`, `NGROK_INSPECT_PORT`.
`DASHBOARD_DIR` points at a dashboard checkout somewhere other than `../nest-dashboard`.

An older `nest_postgres` container (from the dashboard repo's previous compose file) also
binds :5432. Either stop it (`docker stop nest_postgres`) or set `POSTGRES_PORT`. The new
stack uses its own volume and doesn't read the old container's data.

## Telegram bot (local)

1. Create a **personal dev bot** with [@BotFather](https://t.me/botfather). Never use the
   production bot locally. A webhook set with a shared token steals delivery from every
   other deployment of that bot.
2. Put its token in `.env` as `TELEGRAM_BOT_TOKEN`.
3. Add `NGROK_AUTHTOKEN` to `.env` and run `make tunnel`. It prints a URL like
   `https://abc123.ngrok-free.app`.
4. Set `PUBLIC_BASE_URL` in `.env` to that URL and run `make up`. On start the bot registers
   its webhook at `<PUBLIC_BASE_URL>/webhook`.

The free ngrok URL changes every time the tunnel restarts, so repeat steps 3–4 after each restart.

The placeholder token in `.env.example` lets the REST API run without a bot. Bot startup
fails with a warning, and the API keeps working.

## Running without Docker

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
# Postgres from `docker compose up -d postgres`, or any other server
export DATABASE_URL=postgresql://nest:nest@localhost:5432/nest_db
.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed.py
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Use `.venv/bin/python -m <tool>` if you ever move the checkout. Venv scripts hardcode the
venv's absolute path.

## Tests

```bash
.venv/bin/pytest
```

The tests need a Postgres server where they can create and drop databases. The default is
`postgresql://nest:nest@localhost:5432/postgres`; set `TEST_DATABASE_ADMIN_URL` to use another.

## Configuration

Every variable is listed with a comment in [`.env.example`](.env.example). Compose sets
`DATABASE_URL` and the dashboard-auth variables for the containers itself.

Natural-language `/add_task` (Gemini) is out of v1 scope. It stays off unless
`AI_TASK_PARSING_ENABLED=true` and `GEMINI_API_KEY` are both set.

## Production

Neon Postgres plus Render. The start command has to serve `app.main:app` on `$PORT` (`bot:app` still works as a shim). `python main.py`
does that. Run `alembic upgrade head` against Neon before deploying any migration.
