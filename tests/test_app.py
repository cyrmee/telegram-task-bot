"""A1: the app factory boots, logs stay free of bot tokens, legacy code still imports."""
import importlib
import json
import logging

from app.logging_config import JsonFormatter, RedactSecretsFilter, redact

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1"


def test_redact_bot_tokens():
    assert TOKEN not in redact(f"POST https://api.telegram.org/bot{TOKEN}/getMe")
    assert redact("task 12345 due 10:30") == "task 12345 due 10:30"


def test_log_records_are_json_and_redacted():
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, "HTTP Request: POST %s", (f"https://api.telegram.org/bot{TOKEN}/x",), None)
    record.workspace_id = "ws-1"
    RedactSecretsFilter().filter(record)
    line = JsonFormatter().format(record)
    assert TOKEN not in line
    entry = json.loads(line)
    assert entry["workspace_id"] == "ws-1" and entry["level"] == "INFO"


def test_exception_text_is_redacted():
    try:
        raise RuntimeError(f"Unauthorized for bot{TOKEN}")
    except RuntimeError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", None, sys.exc_info())
    RedactSecretsFilter().filter(record)
    assert TOKEN not in JsonFormatter().format(record)


def test_app_boots_without_legacy_bot(db_session):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.post("/webhook", json={}).status_code == 404  # legacy bot is off by default


def test_root_shim_exposes_app():
    import bot

    from app.main import app

    assert type(bot.app) is type(app)


def test_legacy_bot_package_imports(migrated_db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", migrated_db_url)
    module = importlib.import_module("app.bot.legacy.task_bot")
    assert hasattr(module, "TaskBot") and hasattr(module, "webhook_router")


def test_legacy_webhook_rejects_missing_secret(migrated_db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", migrated_db_url)
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.bot.legacy.task_bot import webhook_router

    fake_bot = SimpleNamespace(webhook_secret="s3cret", application=None)
    api = FastAPI()
    api.include_router(webhook_router(fake_bot))
    client = TestClient(api)
    assert client.post("/webhook", json={}).status_code == 403
    assert client.post("/webhook", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}).status_code == 403
