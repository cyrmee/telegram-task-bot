"""B1: per-workspace bots through the real API, PTB stack and DB, with Telegram faked
at the HTTP layer (tests/bot/fake_telegram.py)."""
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db
from app.config import get_settings
from app.models import (
    BotStatus,
    Member,
    MemberRole,
    Notification,
    NotificationStatus,
    NotificationType,
    Project,
    Task,
    TelegramUser,
    WorkspaceBot,
)
from app.notifications import enqueue, message_payload

from .fake_telegram import FakeTelegram, private_message

TOKEN_A = "111111:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
TOKEN_B = "222222:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
PERSON = 7001  # one Telegram user who is a member of both workspaces


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


def make_workspace(client, issuer, owner: str, task_title: str) -> uuid.UUID:
    r = client.post("/api/v1/workspaces", json={"name": f"{owner} co", "timezone": "Africa/Addis_Ababa"}, headers=issuer.headers(owner))
    assert r.status_code == 201, r.text
    ws = uuid.UUID(r.json()["id"])
    with db.SessionLocal() as s, s.begin():
        if s.get(TelegramUser, PERSON) is None:
            s.add(TelegramUser(telegram_user_id=PERSON, username="person"))
            s.flush()
        member = Member(workspace_id=ws, role=MemberRole.MEMBER, telegram_user_id=PERSON, private_chat_id=PERSON)
        project = Project(workspace_id=ws, name=f"{owner} launch")
        s.add_all([member, project])
        s.flush()
        s.add(
            Task(
                workspace_id=ws, number=1, project_id=project.id, assignee_member_id=member.id,
                title=task_title, due_at=datetime.now(timezone.utc) + timedelta(days=2),
            )
        )
    return ws


def connect(client, issuer, owner, ws, token):
    return client.put(f"/api/v1/workspaces/{ws}/bot", json={"token": token}, headers=issuer.headers(owner))


def webhook(client, bot_id, secret, update):
    return client.post(f"/telegram/{bot_id}", json=update, headers={SECRET_HEADER: secret} if secret else {})


def test_two_workspaces_two_bots_isolated(client, issuer, tg):
    ws_a = make_workspace(client, issuer, "alice", "Alpha task <draft>")
    ws_b = make_workspace(client, issuer, "bob", "Beta task")

    r = connect(client, issuer, "alice", ws_a, TOKEN_A)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active" and r.json()["username"] == "alpha_nest_bot"
    assert "token" not in r.text and TOKEN_A not in r.text
    assert connect(client, issuer, "bob", ws_b, TOKEN_B).status_code == 200

    bot_a, bot_b = tg.by_id(111111), tg.by_id(222222)
    assert bot_a.webhook_url == "https://nest.test/telegram/111111"
    assert bot_b.webhook_url == "https://nest.test/telegram/222222"
    assert bot_a.webhook_secret and bot_a.webhook_secret != bot_b.webhook_secret
    assert [c["command"] for c in bot_a.commands] == ["mytasks", "help"]

    # The token is stored encrypted.
    with db.SessionLocal() as s:
        row = s.get(WorkspaceBot, ws_a)
        assert TOKEN_A not in row.token_encrypted and row.status == BotStatus.ACTIVE

    # The same person asks each bot for their tasks and only sees that workspace's.
    assert webhook(client, 111111, bot_a.webhook_secret, private_message("/mytasks", PERSON)).status_code == 200
    assert webhook(client, 222222, bot_b.webhook_secret, private_message("/mytasks", PERSON, 2)).status_code == 200
    (reply_a,), (reply_b,) = bot_a.sent, bot_b.sent
    assert "Alpha task &lt;draft&gt;" in reply_a["text"] and "Beta task" not in reply_a["text"]
    assert "Beta task" in reply_b["text"] and "Alpha" not in reply_b["text"]


def test_webhook_rejects_bad_or_foreign_secret(client, issuer, tg):
    ws_a = make_workspace(client, issuer, "alice", "Alpha task")
    ws_b = make_workspace(client, issuer, "bob", "Beta task")
    connect(client, issuer, "alice", ws_a, TOKEN_A)
    connect(client, issuer, "bob", ws_b, TOKEN_B)
    update = private_message("/mytasks", PERSON)

    assert webhook(client, 111111, None, update).status_code == 403
    assert webhook(client, 111111, "wrong", update).status_code == 403
    assert webhook(client, 111111, tg.by_id(222222).webhook_secret, update).status_code == 403
    assert webhook(client, 999999, "x", update).status_code == 404
    assert tg.by_id(111111).sent == []


def test_connect_errors(client, issuer, tg):
    ws_a = make_workspace(client, issuer, "alice", "Alpha task")
    ws_b = make_workspace(client, issuer, "bob", "Beta task")
    assert connect(client, issuer, "alice", ws_a, TOKEN_A).status_code == 200

    r = connect(client, issuer, "bob", ws_b, TOKEN_A)
    assert r.status_code == 409 and r.json()["error"]["code"] == "bot_in_use"

    r = connect(client, issuer, "bob", ws_b, "333333:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_bot_token"

    tg.down = True
    r = connect(client, issuer, "bob", ws_b, TOKEN_B)
    assert r.status_code == 502 and r.json()["error"]["code"] == "telegram_unavailable"

    # Members can't manage the bot.
    assert client.get(f"/api/v1/workspaces/{ws_a}/bot", headers=issuer.headers("bob")).status_code in (403, 404)


def test_status_replace_and_disconnect(client, issuer, tg):
    ws = make_workspace(client, issuer, "alice", "Alpha task")
    headers = issuer.headers("alice")
    assert client.get(f"/api/v1/workspaces/{ws}/bot", headers=headers).json()["status"] == "not_connected"

    connect(client, issuer, "alice", ws, TOKEN_A)
    body = client.get(f"/api/v1/workspaces/{ws}/bot", headers=headers).json()
    assert body["status"] == "active" and body["webhook"]["urlOk"] is True

    # Replacing the bot unhooks the old one.
    assert connect(client, issuer, "alice", ws, TOKEN_B).status_code == 200
    assert tg.by_id(111111).webhook_url == "" and tg.by_id(222222).webhook_url.endswith("/telegram/222222")
    assert webhook(client, 111111, "x", private_message("/help", PERSON)).status_code == 404

    assert client.delete(f"/api/v1/workspaces/{ws}/bot", headers=headers).status_code == 204
    assert tg.by_id(222222).webhook_url == ""
    assert client.get(f"/api/v1/workspaces/{ws}/bot", headers=headers).json()["status"] == "disconnected"
    secret = tg.by_id(222222).webhook_secret
    assert webhook(client, 222222, secret, private_message("/help", PERSON)).status_code == 404

    # A disconnected bot can be connected by another workspace.
    other = make_workspace(client, issuer, "bob", "Beta task")
    assert connect(client, issuer, "bob", other, TOKEN_B).status_code == 200


def test_startup_reloads_bots_and_marks_revoked_invalid(runtime, issuer, tg, monkeypatch):
    from app.main import create_app

    with TestClient(create_app(bot_request_factory=tg.request)) as c:
        ws_a = make_workspace(c, issuer, "alice", "Alpha task")
        ws_b = make_workspace(c, issuer, "bob", "Beta task")
        connect(c, issuer, "alice", ws_a, TOKEN_A)
        connect(c, issuer, "bob", ws_b, TOKEN_B)

    tg.bots[TOKEN_B].revoked = True
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://new-tunnel.test")  # ngrok restarted
    get_settings.cache_clear()
    with TestClient(create_app(bot_request_factory=tg.request)) as c:
        bot_a = tg.by_id(111111)
        assert bot_a.webhook_url == "https://new-tunnel.test/telegram/111111"
        assert webhook(c, 111111, bot_a.webhook_secret, private_message("/mytasks", PERSON)).status_code == 200
        assert "Alpha task" in bot_a.sent[-1]["text"]
        body = c.get(f"/api/v1/workspaces/{ws_b}/bot", headers=issuer.headers("bob")).json()
        assert body["status"] == "invalid" and "revoked" in body["lastError"]


def test_outbox_delivers_through_the_workspace_bot(client, issuer, tg):
    ws_a = make_workspace(client, issuer, "alice", "Alpha task")
    ws_b = make_workspace(client, issuer, "bob", "Beta task")
    connect(client, issuer, "alice", ws_a, TOKEN_A)
    connect(client, issuer, "bob", ws_b, TOKEN_B)
    with db.SessionLocal() as s, s.begin():
        member = s.scalar(select(Member).where(Member.workspace_id == ws_b, Member.telegram_user_id == PERSON))
        nid = enqueue(
            s, workspace_id=ws_b, member_id=member.id, type=NotificationType.ASSIGNED,
            idempotency_key="assigned:test", payload=message_payload("New task for you"),
        )

    deadline = time.monotonic() + 10
    while not tg.by_id(222222).sent and time.monotonic() < deadline:
        time.sleep(0.1)
    assert [m["text"] for m in tg.by_id(222222).sent] == ["New task for you"]
    assert tg.by_id(111111).sent == []
    with db.SessionLocal() as s:
        row = s.get(Notification, nid)
        assert row.status == NotificationStatus.SENT and row.telegram_message_id is not None


def test_group_messages_are_ignored(client, issuer, tg):
    ws = make_workspace(client, issuer, "alice", "Alpha task")
    connect(client, issuer, "alice", ws, TOKEN_A)
    update = private_message("/mytasks", PERSON)
    update["message"]["chat"] = {"id": -100123, "type": "supergroup", "title": "Team"}
    assert webhook(client, 111111, tg.by_id(111111).webhook_secret, update).status_code == 200
    assert tg.by_id(111111).sent == []
