"""A5: members and invites API, and redeem_invite for the bot's /start."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import (
    Invite,
    Member,
    MemberRole,
    MemberStatus,
    Notification,
    NotificationStatus,
    NotificationType,
    TelegramUser,
    WorkspaceBot,
)
from app.services.invites import TelegramIdentity, match_pending_by_username, redeem_invite


@pytest.fixture
def env(db_session, issuer, monkeypatch):
    with TestClient(create_app()) as client:
        events = []

        async def record(workspace_id, event, actor=None, source="dashboard", **payload):
            events.append((event, payload))

        for module in ("app.api.v1.members", "app.api.v1.tasks", "app.api.v1.projects"):
            monkeypatch.setattr(f"{module}.emit_workspace_event", record)
        h = issuer.headers("alice")
        ws = client.post("/api/v1/workspaces", json={"name": "Acme", "timezone": "UTC"}, headers=h).json()["id"]
        db_session.add(WorkspaceBot(workspace_id=uuid.UUID(ws), telegram_bot_id=77, username="acme_bot", token_encrypted="x", webhook_secret="y"))
        db_session.commit()
        yield client, h, ws, db_session, events


def tg(i: int, username: str | None = None) -> TelegramIdentity:
    return TelegramIdentity(id=i, username=username, first_name=(username or f"user{i}").title())


def create_invite(client, h, ws, **body):
    r = client.post(f"/api/v1/workspaces/{ws}/invites", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_invite_create_list_revoke(env):
    client, h, ws, db, _ = env
    inv = create_invite(client, h, ws, expiresInHours=24, maxUses=None)
    assert inv["url"] == f"https://t.me/acme_bot?start={inv['token']}" and len(inv["token"]) <= 64
    assert inv["state"] == "active" and inv["maxUses"] is None and inv["createdBy"]["displayName"] == "Alice"
    assert [i["id"] for i in client.get(f"/api/v1/workspaces/{ws}/invites", headers=h).json()["items"]] == [inv["id"]]
    for _ in range(2):
        assert client.delete(f"/api/v1/workspaces/{ws}/invites/{inv['id']}", headers=h).status_code == 204
    assert client.get(f"/api/v1/workspaces/{ws}/invites", headers=h).json()["items"] == []
    revoked = client.get(f"/api/v1/workspaces/{ws}/invites?state=revoked", headers=h).json()["items"]
    assert revoked[0]["state"] == "revoked"


def test_invite_needs_bot(env):
    client, h, ws, db, _ = env
    db.query(WorkspaceBot).delete()
    db.commit()
    r = client.post(f"/api/v1/workspaces/{ws}/invites", json={}, headers=h)
    assert r.json()["error"]["code"] == "bot_not_connected"


def test_redeem_outcomes(env):
    client, h, ws, db, _ = env
    ws_id = uuid.UUID(ws)
    single = create_invite(client, h, ws)  # maxUses defaults to 1

    first = redeem_invite(db, ws_id, single["token"], tg(1, "hana"), chat_id=1)
    assert first.outcome == "joined" and first.member.status == MemberStatus.ACTIVE and first.workspace_name == "Acme"
    assert first.member.private_chat_id == 1 and first.member.invite_id == uuid.UUID(single["id"])
    assert redeem_invite(db, ws_id, single["token"], tg(2), chat_id=2).outcome == "used_up"
    assert redeem_invite(db, ws_id, "nope", tg(2), chat_id=2).outcome == "invalid"

    multi = create_invite(client, h, ws, maxUses=None)
    assert redeem_invite(db, ws_id, multi["token"], tg(1, "hana"), chat_id=1).outcome == "already_member"
    assert redeem_invite(db, ws_id, multi["token"], tg(2), chat_id=2).outcome == "joined"
    db.expire_all()
    assert db.get(Invite, uuid.UUID(multi["id"])).use_count == 1  # already_member doesn't count

    client.delete(f"/api/v1/workspaces/{ws}/invites/{multi['id']}", headers=h)
    assert redeem_invite(db, ws_id, multi["token"], tg(3), chat_id=3).outcome == "revoked"
    db.query(Invite).filter_by(id=uuid.UUID(single["id"])).update({"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1), "use_count": 0})
    db.commit()
    assert redeem_invite(db, ws_id, single["token"], tg(3), chat_id=3).outcome == "expired"
    # Telegram profile cached
    assert db.get(TelegramUser, 1).username == "hana"
    # Another workspace can't use this workspace's token
    other = client.post("/api/v1/workspaces", json={"name": "B", "timezone": "UTC"}, headers=h).json()["id"]
    assert redeem_invite(db, uuid.UUID(other), create_invite(client, h, ws)["token"], tg(9), 9).outcome == "invalid"


def test_redeem_reactivates_removed_and_unreachable(env):
    client, h, ws, db, _ = env
    ws_id = uuid.UUID(ws)
    inv = create_invite(client, h, ws, maxUses=None)
    member = redeem_invite(db, ws_id, inv["token"], tg(1, "hana"), 1).member
    assert client.delete(f"/api/v1/workspaces/{ws}/members/{member.id}", headers=h).status_code == 204
    again = redeem_invite(db, ws_id, inv["token"], tg(1, "hana"), 1)
    assert again.outcome == "reactivated" and again.member.id == member.id and again.member.status == MemberStatus.ACTIVE
    again.member.status = MemberStatus.UNREACHABLE
    db.commit()
    assert redeem_invite(db, ws_id, inv["token"], tg(1, "hana"), 1).member.status == MemberStatus.ACTIVE


def test_pending_username_member_matched(env):
    client, h, ws, db, events = env
    r = client.post(f"/api/v1/workspaces/{ws}/members", json={"telegramUsername": "@Meron_New"}, headers=h)
    assert r.status_code == 201 and r.json()["status"] == "pending" and r.json()["displayName"] == "@meron_new"
    assert client.post(f"/api/v1/workspaces/{ws}/members", json={"telegramUsername": "meron_new"}, headers=h).json()["error"]["code"] == "member_exists"
    assert match_pending_by_username(db, uuid.UUID(ws), tg(5, "someone_else"), 5) is None
    result = match_pending_by_username(db, uuid.UUID(ws), tg(5, "MERON_NEW"), 5)
    assert result.outcome == "joined" and str(result.member.id) == r.json()["id"]


def test_link_invite_attaches_manager_telegram(env):
    client, h, ws, db, _ = env
    link = client.post(f"/api/v1/workspaces/{ws}/members/me/telegram-link", headers=h).json()
    token = link["url"].rsplit("=", 1)[1]
    result = redeem_invite(db, uuid.UUID(ws), token, tg(42, "alice_tg"), 42)
    manager = db.query(Member).filter_by(user_id="alice").one()
    assert result.outcome == "linked" and result.member.id == manager.id
    assert manager.telegram_user_id == 42 and manager.role == MemberRole.MANAGER
    me = client.get("/api/v1/me", headers=h).json()["workspaces"][0]["onboarding"]
    assert me["managerTelegramLinked"] is True
    # Team invites list doesn't show self-link invites.
    assert client.get(f"/api/v1/workspaces/{ws}/invites?state=used_up", headers=h).json()["items"] == []


def test_redeem_wakes_deferred_notifications(env):
    client, h, ws, db, _ = env
    pending = Member(workspace_id=uuid.UUID(ws), invited_username="late_joiner", status=MemberStatus.PENDING)
    db.add(pending)
    db.commit()
    p = client.post(f"/api/v1/workspaces/{ws}/projects", json={"name": "P"}, headers=h).json()
    client.post(f"/api/v1/workspaces/{ws}/tasks", headers=h, json={
        "title": "t", "projectId": p["id"], "assigneeId": str(pending.id), "dueAt": "2026-12-01T09:00:00Z"})
    n = db.query(Notification).filter_by(member_id=pending.id).one()
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    n.next_attempt_at, n.last_error = later, "member has not started the bot"  # as the worker would defer it
    db.commit()
    inv = create_invite(client, h, ws)
    redeem_invite(db, uuid.UUID(ws), inv["token"], tg(8, "late_joiner"), 8)
    db.expire_all()
    assert db.get(Notification, n.id).next_attempt_at <= datetime.now(timezone.utc)


def test_members_list_role_and_remove_with_reassign(env):
    client, h, ws, db, events = env
    ws_id = uuid.UUID(ws)
    inv = create_invite(client, h, ws, maxUses=None)
    hana = redeem_invite(db, ws_id, inv["token"], tg(1, "hana"), 1).member
    dawit = redeem_invite(db, ws_id, inv["token"], tg(2, "dawit"), 2).member
    manager = db.query(Member).filter_by(user_id="alice").one()
    p = client.post(f"/api/v1/workspaces/{ws}/projects", json={"name": "P"}, headers=h).json()
    task_ids = [
        client.post(f"/api/v1/workspaces/{ws}/tasks", headers=h, json={
            "title": f"t{i}", "projectId": p["id"], "assigneeId": str(hana.id), "dueAt": "2026-12-01T09:00:00Z"}).json()["id"]
        for i in range(2)
    ]

    items = client.get(f"/api/v1/workspaces/{ws}/members", headers=h).json()["items"]
    by_id = {i["id"]: i for i in items}
    assert by_id[str(hana.id)]["openTaskCount"] == 2 and by_id[str(hana.id)]["telegram"]["username"] == "hana"
    assert by_id[str(manager.id)]["role"] == "manager" and by_id[str(manager.id)]["displayName"] == "Alice"
    assert len(client.get(f"/api/v1/workspaces/{ws}/members?role=manager", headers=h).json()["items"]) == 1

    # last manager can't be demoted or removed
    assert client.patch(f"/api/v1/workspaces/{ws}/members/{manager.id}", json={"role": "member"}, headers=h).json()["error"]["code"] == "last_manager"
    assert client.delete(f"/api/v1/workspaces/{ws}/members/{manager.id}", headers=h).json()["error"]["code"] == "last_manager"
    r = client.patch(f"/api/v1/workspaces/{ws}/members/{dawit.id}", json={"role": "manager"}, headers=h)
    assert r.json()["role"] == "manager" and events[-1][0] == "member.updated"

    r = client.delete(f"/api/v1/workspaces/{ws}/members/{hana.id}", headers=h)
    assert r.status_code == 409 and r.json()["error"]["details"] == {"openTaskCount": 2}
    r = client.delete(f"/api/v1/workspaces/{ws}/members/{hana.id}?reassignTo={manager.id}", headers=h)
    assert r.status_code == 204 and events[-1] == ("member.removed", {"memberId": str(hana.id)})
    for tid in task_ids:
        assert client.get(f"/api/v1/workspaces/{ws}/tasks/{tid}", headers=h).json()["assignee"]["id"] == str(manager.id)
    db.expire_all()
    queued_for_hana = db.query(Notification).filter_by(member_id=hana.id, status=NotificationStatus.QUEUED).count()
    assert queued_for_hana == 0
    assert db.query(Notification).filter_by(member_id=hana.id, type=NotificationType.UNASSIGNED).count() == 2
    assert str(hana.id) not in {i["id"] for i in client.get(f"/api/v1/workspaces/{ws}/members", headers=h).json()["items"]}
    removed = client.get(f"/api/v1/workspaces/{ws}/members?status=removed", headers=h).json()["items"]
    assert [m["id"] for m in removed] == [str(hana.id)]


def test_members_tenancy(env, issuer):
    client, h, ws, db, _ = env
    bob = issuer.headers("bob")
    ws_b = client.post("/api/v1/workspaces", json={"name": "B", "timezone": "UTC"}, headers=bob).json()["id"]
    alice_member = db.query(Member).filter_by(user_id="alice").one()
    assert client.patch(f"/api/v1/workspaces/{ws_b}/members/{alice_member.id}", json={"role": "member"}, headers=bob).status_code == 404
    assert client.delete(f"/api/v1/workspaces/{ws_b}/members/{alice_member.id}", headers=bob).status_code == 404
    inv = create_invite(client, h, ws)
    assert client.delete(f"/api/v1/workspaces/{ws_b}/invites/{inv['id']}", headers=bob).status_code == 404


def test_removed_manager_cannot_regain_role_via_invites(env, issuer):
    client, h, ws, db, _ = env
    ws_id = uuid.UUID(ws)
    # Bob is a second manager with Telegram linked, then removed.
    db.add(TelegramUser(telegram_user_id=300, username="bob_tg"))
    db.flush()
    bob = Member(workspace_id=ws_id, user_id="bob", telegram_user_id=300, private_chat_id=300,
                 role=MemberRole.MANAGER, status=MemberStatus.ACTIVE)
    db.add(bob)
    db.commit()
    bobs_team_invite = client.post(f"/api/v1/workspaces/{ws}/invites", json={"maxUses": None},
                                   headers=issuer.headers("bob")).json()
    db.add(TelegramUser(telegram_user_id=301))
    db.flush()
    bob_link = Invite(workspace_id=ws_id, token="bob-link-token", member_id=bob.id, created_by_member_id=bob.id,
                      expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    db.add(bob_link)
    db.commit()
    assert client.delete(f"/api/v1/workspaces/{ws}/members/{bob.id}", headers=h).status_code == 204

    # His link invite and the invites he created are revoked.
    assert redeem_invite(db, ws_id, "bob-link-token", tg(301), 301).outcome == "revoked"
    assert redeem_invite(db, ws_id, bobs_team_invite["token"], tg(302), 302).outcome == "revoked"

    # Rejoining via someone else's team invite makes him a plain member.
    team = create_invite(client, h, ws, maxUses=None)
    result = redeem_invite(db, ws_id, team["token"], tg(300, "bob_tg"), 300)
    assert result.outcome == "reactivated" and result.member.role == MemberRole.MEMBER
    assert client.get(f"/api/v1/workspaces/{ws}", headers=issuer.headers("bob")).status_code == 403


def test_readding_removed_member_by_username_resets_role(env):
    client, h, ws, db, _ = env
    db.add(TelegramUser(telegram_user_id=400, username="ex_mgr"))
    db.flush()
    ex = Member(workspace_id=uuid.UUID(ws), telegram_user_id=400, role=MemberRole.MANAGER, status=MemberStatus.REMOVED)
    db.add(ex)
    db.commit()
    r = client.post(f"/api/v1/workspaces/{ws}/members", json={"telegramUsername": "ex_mgr"}, headers=h)
    assert r.status_code == 201 and r.json()["role"] == "member" and r.json()["status"] == "pending"
