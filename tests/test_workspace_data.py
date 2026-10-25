"""D4: workspace export and deletion."""
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Invite, Member, Notification, Project, Task, TaskEvent, TelegramUser, Workspace, WorkspaceBot
from app.services.invites import TelegramIdentity, redeem_invite


@pytest.fixture
def env(db_session, issuer, monkeypatch):
    events = []

    async def record(workspace_id, event, actor=None, source="dashboard", **payload):
        events.append((event, str(workspace_id)))

    for module in ("app.api.v1.workspaces", "app.api.v1.tasks", "app.api.v1.projects", "app.api.v1.members"):
        monkeypatch.setattr(f"{module}.emit_workspace_event", record)
    app = create_app()
    with TestClient(app) as client:
        h = issuer.headers("alice")
        ws = client.post("/api/v1/workspaces", json={"name": "Acme Ltd", "timezone": "UTC"}, headers=h).json()["id"]
        db_session.add(WorkspaceBot(workspace_id=uuid.UUID(ws), telegram_bot_id=55, username="acme_bot",
                                    token_encrypted="CIPHERTEXT-SHOULD-NOT-LEAK", webhook_secret="SECRET-SHOULD-NOT-LEAK"))
        db_session.commit()
        invite = client.post(f"/api/v1/workspaces/{ws}/invites", json={}, headers=h).json()
        hana = redeem_invite(db_session, uuid.UUID(ws), invite["token"], TelegramIdentity(id=7, username="hana"), 7).member
        p = client.post(f"/api/v1/workspaces/{ws}/projects", json={"name": "Launch"}, headers=h).json()
        t = client.post(f"/api/v1/workspaces/{ws}/tasks", headers=h, json={
            "title": "Logo", "projectId": p["id"], "assigneeId": str(hana.id), "dueAt": "2026-12-01T09:00:00Z",
            "reminderOffsetsMinutes": [60]}).json()
        client.post(f"/api/v1/workspaces/{ws}/tasks/{t['id']}/complete", headers=h)
        yield SimpleNamespace(client=client, app=app, h=h, ws=ws, db=db_session, events=events, invite=invite, task=t)


def test_export_contains_everything_but_secrets(env):
    r = env.client.get(f"/api/v1/workspaces/{env.ws}/export", headers=env.h)
    assert r.status_code == 200
    assert r.headers["content-disposition"] == f'attachment; filename="nest-workspace-{env.ws}.json"'
    body = r.json()
    assert body["formatVersion"] == 1 and body["workspace"]["name"] == "Acme Ltd"
    assert body["bot"] == {"username": "acme_bot", "telegramBotId": 55, "status": "active", "connectedAt": body["bot"]["connectedAt"]}
    assert {m["displayName"] for m in body["members"]} == {"Alice", "@hana"}
    assert [p["name"] for p in body["projects"]] == ["Launch"]
    (task,) = body["tasks"]
    assert task["title"] == "Logo" and task["status"] == "completed" and task["reminderOffsetsMinutes"] == [60]
    assert {e["type"] for e in body["taskEvents"]} == {"created", "completed"}
    assert body["invites"][0]["useCount"] == 1 and "token" not in body["invites"][0]
    assert {n["type"] for n in body["notifications"]} >= {"assigned"}
    raw = r.text
    for secret in ("CIPHERTEXT-SHOULD-NOT-LEAK", "SECRET-SHOULD-NOT-LEAK", env.invite["token"], "payload"):
        assert secret not in raw


def test_delete_requires_exact_name(env):
    url = f"/api/v1/workspaces/{env.ws}"
    for wrong in ("acme ltd", "Acme", ""):
        r = env.client.request("DELETE", url, json={"confirmName": wrong}, headers=env.h)
        assert r.status_code == 422 and r.json()["error"]["code"] == "confirmation_mismatch"
    assert env.client.request("DELETE", url, json={}, headers=env.h).status_code == 422
    assert env.client.get(url, headers=env.h).status_code == 200


def test_delete_disconnects_bot_cascades_and_emits(env, caplog):
    disconnected = []

    async def disconnect(workspace_id, actor=None):
        disconnected.append(workspace_id)

    env.app.other_asgi_app.state.bot_manager = SimpleNamespace(disconnect=disconnect)
    ws_id = uuid.UUID(env.ws)
    with caplog.at_level("WARNING"):
        r = env.client.request("DELETE", f"/api/v1/workspaces/{env.ws}", json={"confirmName": "Acme Ltd"}, headers=env.h)
    assert r.status_code == 204
    assert disconnected == [ws_id]
    assert ("workspace.deleted", env.ws) in env.events
    audit = [rec for rec in caplog.records if getattr(rec, "audit", None) == "workspace.deleted"]
    assert audit and audit[0].actor_user_id == "alice" and audit[0].workspace_id == env.ws

    db = env.db
    db.expire_all()
    assert db.get(Workspace, ws_id) is None
    for model in (Member, Project, Task, TaskEvent, Invite, Notification, WorkspaceBot):
        assert db.query(model).filter_by(workspace_id=ws_id).count() == 0, model.__name__
    assert db.get(TelegramUser, 7) is not None  # global profile cache, shared across workspaces
    assert env.client.get("/api/v1/me", headers=env.h).json()["workspaces"] == []


def test_delete_still_works_if_bot_disconnect_fails(env):
    async def broken(workspace_id, actor=None):
        raise RuntimeError("Telegram down")

    env.app.other_asgi_app.state.bot_manager = SimpleNamespace(disconnect=broken)
    r = env.client.request("DELETE", f"/api/v1/workspaces/{env.ws}", json={"confirmName": "Acme Ltd"}, headers=env.h)
    assert r.status_code == 204
    env.db.expire_all()
    assert env.db.get(Workspace, uuid.UUID(env.ws)) is None


def test_member_role_cannot_export_or_delete(env, issuer):
    env.db.add(Member(workspace_id=uuid.UUID(env.ws), user_id="carol", role="member", status="active"))
    env.db.commit()
    carol = issuer.headers("carol")
    assert env.client.get(f"/api/v1/workspaces/{env.ws}/export", headers=carol).status_code == 403
    r = env.client.request("DELETE", f"/api/v1/workspaces/{env.ws}", json={"confirmName": "Acme Ltd"}, headers=carol)
    assert r.status_code == 403
    assert json.loads(env.client.get(f"/api/v1/workspaces/{env.ws}", headers=env.h).text)["name"] == "Acme Ltd"
