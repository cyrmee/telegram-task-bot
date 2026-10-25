# Nest backend: telegram-task-bot

Nest is a Telegram-native project manager by Gezat Communications. Managers plan work in the web
dashboard (`../nest-dashboard`). Members receive tasks, reminders and a Complete button by Telegram
DM. This repo holds the FastAPI API and the Telegram bot. Both share one Postgres.

- Product scope: `../../docs/Nest Prd V1.pdf` and `../../docs/Nest – User Experience & Flow Map.pdf`. **PRD v1 only**: Manager/Member roles, Pending/Completed status, one assignee per task, no AI.
- Build plan: [Notion "Nest" project](https://app.notion.com/p/Nest-3a09d3b7ae23807ea4a2fa36378efa67), mirrored word for word in `../../plan/TASKS.md`. Every task has a scope checklist and a **done when**. Read yours before starting, and treat the done-when as the bar.
- Contract and schema: `docs/api-v1.md` (endpoints, auth, errors) and `docs/data-model.md` (tables, invariants).

## Run, test, migrate

`README.md` covers setup. The short version: `cp .env.example .env && make up` (targets are in the `Makefile`).

- Migrations are Alembic (`migrations/`). The backend container runs `alembic upgrade head` on every start. Create one with `.venv/bin/alembic revision -m "..."`. This is **Stream A only** (see Ownership).
- Tests: `make test` (or `.venv/bin/pytest`; pass extra args with `make test ARGS="-k tasks"`). They create and drop throwaway databases through `TEST_DATABASE_ADMIN_URL` (default: `postgres` on localhost:5432).
- Test helpers: `tests/conftest.py` (migrated test DB, `issuer` fake better-auth JWTs), `tests/factories.py` (workspace/member/project/task rows), `tests/bot/fake_telegram.py` (records what bots send). `tests/test_tenancy_sweep.py` checks every workspace route for cross-tenant 404s and unauthenticated 401s automatically.
- Each worktree gets its own `.venv` (`python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`). A venv breaks if it's moved, because its scripts hardcode the absolute path.
- Pin every dependency in `requirements.txt` to an exact version.

## Architecture and ownership

Three parallel Claude sessions build Nest, one stream each. Edit only what your stream owns. For a change elsewhere, ask the owning session.

| Path | Owner |
|---|---|
| `app/models/`, `migrations/`, `docs/data-model.md`: the schema | **Stream A** (nest 1) |
| `app/` API modules, `app/db.py`, `docs/api-v1.md`, `scripts/seed.py` | **Stream A** (nest 1) |
| `app/bot/`, `app/notifications/`, `app/scheduler/`: bots, outbox, reminders | **Stream B** (nest 2) |
| `../nest-dashboard` (whole repo) | **Stream C** (nest 3) |
| `docker-compose.yml`, `Makefile`, `Dockerfile.dev`, `README.md` | shared; tell the other sessions when you change them |

The app is `app.main:app` (factory in `app/main.py`; root `bot.py`/`main.py` are shims). Pre-v1 bot code lives in `app/bot/legacy/` (Stream B ports or deletes it) and only runs with `LEGACY_BOT_ENABLED=true`. It targets the legacy schema, so leave it off against a v1 database.

Key v1 decisions:
- **One Telegram bot per workspace.** The customer brings a BotFather token. It's stored encrypted (`TOKEN_ENCRYPTION_KEY`) in `workspace_bots`, and each bot's webhook is `POST /telegram/{botId}`.
- **Project = Group.** Say "Project" in code and UI.
- **Neon** in production, the compose Postgres in dev.
- **Tenant scoping:** composite `(workspace_id, id)` foreign keys. Every insert into `tasks`, `notifications` or `task_events` sets `workspace_id` to the referenced rows' workspace, or the DB rejects it.

## Conventions

- Branch per task: `stream-<a|b|c|d>/<slug>`, with a git worktree per backend stream under `../telegram-task-bot-wt/`.
- Commits: Conventional Commits ending with the task id, e.g. `feat(db): v1 data model with Alembic migrations (0.2)`.
- Keep each branch to one task. Rebase on local `main` daily, then merge with `git merge --ff-only` into local `main`.
- Endpoint changes update `docs/api-v1.md` in the same commit.
- Schema changes go through Stream A only.
- Pushing to GitHub and anything on Render, Neon or a Telegram webhook need Zak's explicit OK.

## Gotchas

- **Bot tokens:** `setWebhook` (and the backend's startup, when `PUBLIC_BASE_URL` is set) moves update delivery for that token away from every other deployment. Locally, use a personal dev bot from @BotFather, never the production bot `@gezat_tasks_bot`.
- **ngrok:** the free URL changes on every tunnel restart. Rerun `make tunnel`, update `PUBLIC_BASE_URL` in `.env`, and `make up` again.
- **Shared Postgres:** the old `nest_postgres` container (postgres 15) holds :5432, and other sessions use its databases. Don't recreate it or delete its volume. Run the compose stack next to it by overriding `POSTGRES_PORT` / `API_PORT` / `DASHBOARD_PORT` and `COMPOSE_PROJECT_NAME` in `.env`.
- **SSL:** legacy `models.py` forces `sslmode=require` for non-localhost hosts, so in-network URLs carry `?sslmode=disable`. `app/db.py` already treats `@postgres:` as local.
- **SQLAlchemy:** stay on the 2.0 pin. 2.1 maps a bare `postgresql://` to psycopg 3, which isn't installed.
- **better-auth tables** (`user`, `session`, `account`, `verification`, `jwks`) belong to the dashboard and are created by its CLI, `npx auth@<better-auth version> migrate` (the old `@better-auth/cli` package stopped at 1.4). Alembic never touches them, and the API never reads the session tables.
- **Gemini `/add_task`** is off unless `AI_TASK_PARSING_ENABLED=true`. It's v2 scope.
