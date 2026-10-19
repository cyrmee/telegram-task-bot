"""Helpers shared by the bot tests."""
import time
import uuid
from datetime import datetime, timedelta, timezone

from app import db
from app.models import Member, MemberRole, Project, Task, TelegramUser

TOKEN_A = "111111:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
TOKEN_B = "222222:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
PERSON = 7001  # one Telegram user who is a member of both workspaces

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

def wait_for(predicate, timeout: float = 10.0):
    """Poll until predicate() is truthy (the outbox worker runs in the app's loop)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


def add_member(ws: uuid.UUID, telegram_user_id: int, role: MemberRole = MemberRole.MEMBER) -> uuid.UUID:
    with db.SessionLocal() as s, s.begin():
        if s.get(TelegramUser, telegram_user_id) is None:
            s.add(TelegramUser(telegram_user_id=telegram_user_id, first_name=f"U{telegram_user_id}"))
            s.flush()
        member = Member(workspace_id=ws, role=role, telegram_user_id=telegram_user_id, private_chat_id=telegram_user_id)
        s.add(member)
        s.flush()
        return member.id


def member_id(ws: uuid.UUID, telegram_user_id: int) -> uuid.UUID:
    from sqlalchemy import select

    with db.SessionLocal() as s:
        return s.scalar(select(Member.id).where(Member.workspace_id == ws, Member.telegram_user_id == telegram_user_id))


def create_task(client, issuer, owner, ws, assignee_id, title="Write launch copy", due_in=timedelta(days=1)) -> str:
    headers = issuer.headers(owner)
    r = client.post(f"/api/v1/workspaces/{ws}/projects", json={"name": "Launch"}, headers=headers)
    assert r.status_code == 201, r.text
    body = {
        "title": title,
        "projectId": r.json()["id"],
        "assigneeId": str(assignee_id),
        "dueAt": (datetime.now(timezone.utc) + due_in).isoformat(),
    }
    r = client.post(f"/api/v1/workspaces/{ws}/tasks", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]
