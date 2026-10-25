# Local dev stack. See README.md.
COMPOSE := docker compose
-include .env
API_PORT ?= 8000
DASHBOARD_PORT ?= 3000
NGROK_INSPECT_PORT ?= 4040
.PHONY: up down logs migrate seed tunnel ps reset test live-test

up:
	$(COMPOSE) up -d --build --wait postgres backend
	$(COMPOSE) up -d dashboard
	$(MAKE) seed
	@echo "API:       http://localhost:$(API_PORT)/docs"
	@echo "Dashboard: http://localhost:$(DASHBOARD_PORT) (first start runs npm install; follow with 'make logs')"

down:
	$(COMPOSE) --profile tunnel down

logs:
	$(COMPOSE) --profile tunnel logs -f --tail=100

# Backend schema; also runs on backend start. The dashboard runs its better-auth migration itself on start (see docker-compose.yml).
migrate:
	$(COMPOSE) exec backend alembic upgrade head

# Recreates the seed workspace. `make seed OWNER_EMAIL=you@example.com` links it to your dashboard login.
seed:
	$(COMPOSE) exec backend python scripts/seed.py $(if $(OWNER_EMAIL),--owner-email $(OWNER_EMAIL))

# Public HTTPS URL for Telegram webhooks. Copy it into PUBLIC_BASE_URL in .env, then `make up`.
tunnel:
	@test -n "$(NGROK_AUTHTOKEN)" || { echo "Set NGROK_AUTHTOKEN in .env first"; exit 1; }
	$(COMPOSE) --profile tunnel up -d ngrok
	@sleep 3
	@curl -s http://localhost:$(NGROK_INSPECT_PORT)/api/tunnels | python3 -c "import json,sys; print(json.load(sys.stdin)['tunnels'][0]['public_url'])"

ps:
	$(COMPOSE) ps

# Deletes the local database volume.
reset:
	$(COMPOSE) --profile tunnel down -v

# Backend tests against a Postgres server (each run creates and drops throwaway databases).
# Works with the compose postgres (`make up`) or any server via TEST_DATABASE_ADMIN_URL. CI calls this too.
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python)
TEST_DATABASE_ADMIN_URL ?= postgresql://nest:nest@localhost:$(or $(POSTGRES_PORT),5432)/postgres
test:
	TEST_DATABASE_ADMIN_URL=$(TEST_DATABASE_ADMIN_URL) $(PYTHON) -m pytest $(ARGS)

# Guided end-to-end run with two real dev bots and a phone (scripts/live_test.py).
# Reads ~/.config/nest/dev-bots.env; ARGS=--check does the preflight only.
live-test:
	$(PYTHON) scripts/live_test.py $(ARGS)
