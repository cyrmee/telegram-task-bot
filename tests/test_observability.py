"""D3: request/workspace ids in logs, Sentry scrubbing, alerts, /health."""
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.logging_config import JsonFormatter, RedactSecretsFilter
from app.observability import ContextFilter, alert, scrub_event, workspace_context

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1"


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.addFilter(ContextFilter())
        self.addFilter(RedactSecretsFilter())
        self.setFormatter(JsonFormatter())
        self.lines: list[dict] = []

    def emit(self, record):
        self.lines.append(json.loads(self.format(record)))


def capture(logger_name: str = "") -> Capture:
    handler = Capture()
    target = logging.getLogger(logger_name)
    target.disabled = False  # alembic's fileConfig (migrated_db_url) disables existing loggers
    target.setLevel(logging.INFO)
    target.addHandler(handler)
    return handler


def test_request_id_is_generated_echoed_and_logged(db_session, issuer):
    from app.main import create_app

    handler = capture("app.api.probe")
    api = create_app()
    probe_logger = logging.getLogger("app.api.probe")
    ws = str(uuid.uuid4())

    @api.other_asgi_app.get("/api/v1/workspaces/{workspace_id}/probe")
    def probe(workspace_id: str):
        probe_logger.info("probing")
        return {}

    with TestClient(api) as client:
        r = client.get(f"/api/v1/workspaces/{ws}/probe")
        generated = r.headers["x-request-id"]
        assert len(generated) == 32
        r = client.get(f"/api/v1/workspaces/{ws}/probe", headers={"X-Request-ID": "client-abc.1"})
        assert r.headers["x-request-id"] == "client-abc.1"
        # A malformed id is replaced, and error bodies carry the effective id.
        r = client.get("/api/v1/me", headers={"X-Request-ID": "bad id\n"})
        assert r.status_code == 401 and r.json()["error"]["requestId"] == r.headers["x-request-id"] != "bad id\n"

    first, second = handler.lines[-2:]
    assert first["request_id"] == generated and first["workspace_id"] == ws
    assert second["request_id"] == "client-abc.1"
    logging.getLogger("app.api.probe").removeHandler(handler)


def test_workspace_context_and_alerts():
    handler = capture("nest.alerts")
    with workspace_context("ws-1"):
        alert("notification_failed", f"send failed for bot{TOKEN}", notification_id="n1")
    (line,) = handler.lines
    assert line["level"] == "ERROR" and line["alert"] == "notification_failed"
    assert line["workspace_id"] == "ws-1" and line["notification_id"] == "n1"
    assert TOKEN not in json.dumps(line)
    logging.getLogger("nest.alerts").removeHandler(handler)


def test_sentry_events_are_scrubbed():
    event = {
        "message": f"Unauthorized for https://api.telegram.org/bot{TOKEN}/getMe",
        "request": {
            "headers": [["Authorization", "Bearer eyJ..."], ["X-Telegram-Bot-Api-Secret-Token", "s3cret"], ["Accept", "*/*"]],
            "cookies": {"better-auth.session_token": "abc"},
            "data": {"token": TOKEN, "name": "ok"},
        },
        "extra": {"nested": [{"webhook_secret": "x", "note": f"bot{TOKEN}"}]},
    }
    out = json.dumps(scrub_event(event))
    for secret in (TOKEN, "eyJ...", "s3cret", "abc"):
        assert secret not in out
    assert "*/*" in out and '"name": "ok"' in out


def test_sentry_is_a_no_op_without_dsn(monkeypatch):
    from app.config import get_settings
    from app.observability import init_sentry

    monkeypatch.delenv("SENTRY_DSN", raising=False)
    get_settings.cache_clear()
    assert init_sentry() is False


def test_health_reports_db_and_runtime_disabled(db_session):
    from app.main import create_app

    with TestClient(create_app()) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok" and body["database"] == "ok" and body["bots"] == "disabled"


def test_health_degrades_when_a_loop_stops(db_session):
    from types import SimpleNamespace

    from app.main import create_app

    api = create_app()
    stale = datetime.now(timezone.utc) - timedelta(minutes=10)
    api.other_asgi_app.state.bot_manager = SimpleNamespace(health=lambda: {"live_bots": 2, "last_webhook_check": None})
    api.other_asgi_app.state.outbox_worker = SimpleNamespace(last_round_at=stale)
    api.other_asgi_app.state.reminder_scheduler = SimpleNamespace(last_sweep_at=datetime.now(timezone.utc))
    r = TestClient(api).get("/health")
    assert r.status_code == 503 and r.json()["status"] == "degraded"
    assert r.json()["outbox_worker"]["ok"] is False and r.json()["reminder_scheduler"]["ok"] is True
    assert r.json()["bots"]["live_bots"] == 2
