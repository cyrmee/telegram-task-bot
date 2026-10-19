"""Fixtures for bot tests: the app with its bot runtime on, Telegram faked."""
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import get_settings

from .fake_telegram import FakeTelegram
from .helpers import TOKEN_A, TOKEN_B


@pytest.fixture
def runtime(monkeypatch, db_session):
    monkeypatch.setenv("BOT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://nest.test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def tg():
    fake = FakeTelegram()
    fake.add_bot(TOKEN_A, 111111, "alpha_nest_bot")
    fake.add_bot(TOKEN_B, 222222, "beta_nest_bot")
    return fake


@pytest.fixture
def client(runtime, tg):
    from app.main import create_app

    with TestClient(create_app(bot_request_factory=tg.request)) as c:
        yield c
