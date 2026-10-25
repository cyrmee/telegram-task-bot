"""B2: joining through the workspace bot's /start, via the real API, PTB stack and outbox."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import db
from app.bot import onboarding
from app.models import Invite, Member, MemberRole, MemberStatus

from .fake_telegram import buttons, private_message
from .helpers import TOKEN_A, connect, create_task, wait_for, webhook

BOT = 111111
JOINER = 7101
MANAGER_TG = 8001


@pytest.fixture
def events(monkeypatch):
    seen = []

    async def record(ws, event, actor, source="dashboard", **payload):
        seen.append((event, source, payload["member"]))

    monkeypatch.setattr(onboarding, "emit_workspace_event", record)
    return seen


@pytest.fixture
def workspace(client, issuer):
    r = client.post("/api/v1/workspaces", json={"name": "Gezat & Co", "timezone": "Africa/Addis_Ababa"}, headers=issuer.headers("alice"))
    ws = r.json()["id"]
    assert connect(client, issuer, "alice", ws, TOKEN_A).status_code == 200
    return ws


def invite(client, issuer, ws, **body) -> str:
    r = client.post(f"/api/v1/workspaces/{ws}/invites", json=body, headers=issuer.headers("alice"))
    assert r.status_code == 201, r.text
    url = r.json()["url"]
    assert url.startswith("https://t.me/alpha_nest_bot?start=")
    return url.split("start=", 1)[1]


def start(client, tg, text, user, n, username=None):
    secret = tg.by_id(BOT).webhook_secret
    assert webhook(client, BOT, secret, private_message(text, user, n, username=username)).status_code == 200
    return tg.by_id(BOT).sent[-1]["text"]


def member_by_tg(ws, telegram_user_id):
    with db.SessionLocal() as s:
        return s.scalar(select(Member).where(Member.workspace_id == ws, Member.telegram_user_id == telegram_user_id))


def test_join_with_invite_link(client, issuer, tg, workspace, events):
    token = invite(client, issuer, workspace)
    reply = start(client, tg, f"/start {token}", JOINER, 1)
    assert "Welcome to <b>Gezat &amp; Co</b>" in reply

    member = member_by_tg(workspace, JOINER)
    assert member.status == MemberStatus.ACTIVE and member.private_chat_id == JOINER and member.role == MemberRole.MEMBER
    ((event, source, payload),) = events
    assert event == "member.joined" and source == "telegram" and payload["id"] == str(member.id)

    # A repeated /start (e.g. Telegram redelivering) changes nothing.
    assert "already in" in start(client, tg, f"/start {token}", JOINER, 2)
    assert len(events) == 1


def test_invite_failures_have_friendly_messages(client, issuer, tg, workspace, events):
    single = invite(client, issuer, workspace, maxUses=1)
    start(client, tg, f"/start {single}", JOINER, 1)
    assert "already been used" in start(client, tg, f"/start {single}", 7102, 2)

    expired = invite(client, issuer, workspace)
    with db.SessionLocal() as s, s.begin():
        s.scalar(select(Invite).where(Invite.token == expired)).expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert "expired" in start(client, tg, f"/start {expired}", 7103, 3)

    revoked = invite(client, issuer, workspace)
    with db.SessionLocal() as s:
        invite_id = s.scalar(select(Invite.id).where(Invite.token == revoked))
    assert client.delete(f"/api/v1/workspaces/{workspace}/invites/{invite_id}", headers=issuer.headers("alice")).status_code == 204
    assert "turned off" in start(client, tg, f"/start {revoked}", 7104, 4)

    assert "isn't valid" in start(client, tg, "/start not-a-real-token", 7105, 5)
    assert [e[0] for e in events] == ["member.joined"]
    for user in (7102, 7103, 7104, 7105):
        assert member_by_tg(workspace, user) is None


def test_plain_start_explains_or_matches_pending_username(client, issuer, tg, workspace, events):
    assert "open the invite link" in start(client, tg, "/start", 7201, 1)

    r = client.post(f"/api/v1/workspaces/{workspace}/members", json={"telegramUsername": "Sara_K"}, headers=issuer.headers("alice"))
    assert r.status_code == 201, r.text
    reply = start(client, tg, "/start", 7202, 2, username="sara_k")
    assert "Welcome to" in reply
    assert member_by_tg(workspace, 7202).status == MemberStatus.ACTIVE
    assert [e[0] for e in events] == ["member.joined"]
    assert "already in" in start(client, tg, "/start", 7202, 3, username="sara_k")


def test_tasks_assigned_before_joining_arrive_after(client, issuer, tg, workspace):
    r = client.post(f"/api/v1/workspaces/{workspace}/members", json={"telegramUsername": "late_joiner"}, headers=issuer.headers("alice"))
    pending_id = r.json()["id"]
    create_task(client, issuer, "alice", workspace, pending_id, title="Waiting for you")
    start(client, tg, "/start", 7301, 1, username="late_joiner")
    dm = wait_for(lambda: next((m for m in tg.by_id(BOT).sent if "Waiting for you" in m["text"]), None))
    assert int(dm["chat_id"]) == 7301 and buttons(dm)


def test_manager_links_telegram_and_hears_about_new_members(client, issuer, tg, workspace, events):
    r = client.post(f"/api/v1/workspaces/{workspace}/members/me/telegram-link", headers=issuer.headers("alice"))
    assert r.status_code == 201, r.text
    link_token = r.json()["url"].split("start=", 1)[1]
    assert "now linked" in start(client, tg, f"/start {link_token}", MANAGER_TG, 1)
    manager = member_by_tg(workspace, MANAGER_TG)
    assert manager.role == MemberRole.MANAGER and manager.user_id == "alice"
    assert events[-1][0] == "member.updated"

    start(client, tg, f"/start {invite(client, issuer, workspace)}", JOINER, 2)
    note = wait_for(lambda: next((m for m in tg.by_id(BOT).sent if "joined" in m["text"] and int(m["chat_id"]) == MANAGER_TG), None))
    assert "U7101" in note["text"]
