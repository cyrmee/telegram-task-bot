"""A4: projects and tasks API, scoped, with outbox notifications and realtime events."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import (
    ChangeSource,
    Member,
    MemberRole,
    MemberStatus,
    Notification,
    NotificationStatus,
    NotificationType,
    TaskEvent,
    TelegramUser,
)
from app.services import tasks as task_service


class Ctx:
    def __init__(self, client, issuer, db):
        self.client, self.issuer, self.db = client, issuer, db
        self.events: list[tuple[str, dict]] = []

    def h(self, user="alice"):
        return self.issuer.headers(user)

    def url(self, path="", ws=None):
        return f"/api/v1/workspaces/{ws or self.ws}{path}"

    def add_member(self, tg_id: int, name: str, role=MemberRole.MEMBER, status=MemberStatus.ACTIVE, ws=None) -> Member:
        if self.db.get(TelegramUser, tg_id) is None:
            self.db.add(TelegramUser(telegram_user_id=tg_id, username=name.lower(), first_name=name))
            self.db.flush()
        m = Member(workspace_id=uuid.UUID(ws or self.ws), telegram_user_id=tg_id, private_chat_id=tg_id, role=role, status=status)
        self.db.add(m)
        self.db.commit()
        return m

    def project(self, name="Launch", ws=None, user="alice"):
        r = self.client.post(self.url("/projects", ws), json={"name": name}, headers=self.h(user))
        assert r.status_code == 201, r.text
        return r.json()

    def task(self, project_id, assignee_id, title="Write copy", due_in=timedelta(days=1), **extra):
        due = (datetime.now(timezone.utc) + due_in).isoformat()
        body = {"title": title, "projectId": project_id, "assigneeId": str(assignee_id), "dueAt": due, **extra}
        r = self.client.post(self.url("/tasks"), json=body, headers=self.h())
        assert r.status_code == 201, r.text
        return r.json()

    def notifications(self, **filters):
        self.db.expire_all()
        return self.db.query(Notification).filter_by(**filters).order_by(Notification.created_at).all()


@pytest.fixture
def ctx(db_session, issuer, monkeypatch):
    with TestClient(create_app()) as client:
        c = Ctx(client, issuer, db_session)

        async def record(workspace_id, event, actor=None, source="dashboard", **payload):
            c.events.append((event, payload))

        for module in ("app.api.v1.tasks", "app.api.v1.projects"):
            monkeypatch.setattr(f"{module}.emit_workspace_event", record)
        r = client.post("/api/v1/workspaces", json={"name": "Acme", "timezone": "Africa/Addis_Ababa"}, headers=c.h())
        c.ws = r.json()["id"]
        c.manager = db_session.query(Member).filter_by(user_id="alice").one()
        c.hana = c.add_member(1001, "Hana")
        c.dawit = c.add_member(1002, "Dawit")
        yield c


# --- projects ----------------------------------------------------------------------

def test_project_crud_counts_archive_delete(ctx):
    p = ctx.project("Launch")
    assert p["taskCounts"] == {"total": 0, "pending": 0, "completed": 0, "overdue": 0}
    assert ctx.events[-1][0] == "project.created"
    ctx.task(p["id"], ctx.hana.id, due_in=timedelta(hours=-2))  # overdue
    t2 = ctx.task(p["id"], ctx.dawit.id)
    ctx.client.post(ctx.url(f"/tasks/{t2['id']}/complete"), headers=ctx.h())

    listed = ctx.client.get(ctx.url("/projects"), headers=ctx.h()).json()["items"]
    assert listed[0]["taskCounts"] == {"total": 2, "pending": 1, "completed": 1, "overdue": 1}

    r = ctx.client.patch(ctx.url(f"/projects/{p['id']}"), json={"description": "Q4"}, headers=ctx.h())
    assert r.json()["description"] == "Q4"
    assert ctx.client.delete(ctx.url(f"/projects/{p['id']}"), headers=ctx.h()).json()["error"]["code"] == "project_not_empty"

    for _ in range(2):  # idempotent
        r = ctx.client.post(ctx.url(f"/projects/{p['id']}/archive"), headers=ctx.h())
        assert r.json()["status"] == "archived" and r.json()["archivedAt"]
    assert ctx.client.get(ctx.url("/projects"), headers=ctx.h()).json()["items"] == []
    assert len(ctx.client.get(ctx.url("/projects?status=archived"), headers=ctx.h()).json()["items"]) == 1
    r = ctx.client.post(ctx.url("/tasks"), headers=ctx.h(), json={
        "title": "x", "projectId": p["id"], "assigneeId": str(ctx.hana.id), "dueAt": "2026-12-01T09:00:00Z"})
    assert r.json()["error"]["code"] == "project_archived"

    empty = ctx.project("Empty")
    assert ctx.client.delete(ctx.url(f"/projects/{empty['id']}"), headers=ctx.h()).status_code == 204
    assert ctx.events[-1] == ("project.deleted", {"projectId": empty["id"]})


# --- tasks ---------------------------------------------------------------------------

def test_create_task_queues_escaped_dm_and_emits(ctx):
    p = ctx.project()
    t = ctx.task(p["id"], ctx.hana.id, title="<b>Logo</b> & _v2_", description="Use *brand* colours")
    assert t["number"] == 1 and t["status"] == "pending" and t["isOverdue"] is False
    assert t["assignee"]["displayName"] == "Hana" and t["assignee"]["telegramUsername"] == "hana"
    assert t["project"] == {"id": p["id"], "name": "Launch"}
    assert t["delivery"]["state"] == "queued"
    assert t["reminderOffsetsMinutes"] == [1440]
    assert ctx.task(p["id"], ctx.hana.id)["number"] == 2

    (n, *_) = ctx.notifications(type=NotificationType.ASSIGNED)
    assert n.member_id == ctx.hana.id and n.idempotency_key == f"assigned:{t['id']}:{ctx.hana.id}"
    assert "&lt;b&gt;Logo&lt;/b&gt; &amp; _v2_" in n.payload["text"]
    assert n.payload["buttons"][0][0]["callback_data"] == f"task:complete:{t['id']}"
    assert ctx.events[-2][0] == "task.created" and ctx.events[-2][1]["task"]["id"] == t["id"]


def test_delivery_not_linked_for_pending_member(ctx):
    pending = Member(workspace_id=uuid.UUID(ctx.ws), invited_username="meron_new", status=MemberStatus.PENDING)
    ctx.db.add(pending)
    ctx.db.commit()
    t = ctx.task(ctx.project()["id"], pending.id)
    assert t["delivery"]["state"] == "not_linked"
    assert t["assignee"]["displayName"] == "@meron_new"


def test_list_filters_sort_and_pagination(ctx):
    a, b = ctx.project("A"), ctx.project("B")
    t1 = ctx.task(a["id"], ctx.hana.id, title="Alpha brief", due_in=timedelta(days=-1))
    ctx.task(a["id"], ctx.dawit.id, title="Beta", due_in=timedelta(days=2))
    t3 = ctx.task(b["id"], ctx.hana.id, title="Gamma 100%", due_in=timedelta(days=3))
    ctx.client.post(ctx.url(f"/tasks/{t3['id']}/complete"), headers=ctx.h())

    def numbers(qs=""):
        r = ctx.client.get(ctx.url(f"/tasks{qs}"), headers=ctx.h())
        assert r.status_code == 200, r.text
        return [t["number"] for t in r.json()["items"]], r.json()["total"]

    assert numbers() == ([1, 2, 3], 3)
    assert numbers("?sort=-dueAt") == ([3, 2, 1], 3)
    assert numbers(f"?projectId={a['id']}") == ([1, 2], 2)
    assert numbers(f"?assigneeId={ctx.hana.id}&status=pending") == ([1], 1)
    assert numbers("?overdue=true") == ([t1["number"]], 1)
    assert numbers("?q=100%25") == ([3], 1)  # % is literal, not a wildcard
    assert numbers("?q=brief") == ([1], 1)
    assert numbers("?pageSize=2&page=2") == ([3], 3)
    ctx.client.post(ctx.url(f"/projects/{b['id']}/archive"), headers=ctx.h())
    assert numbers() == ([1, 2], 2)
    assert numbers("?includeArchived=true") == ([1, 2, 3], 3)


def test_reassign_notifies_both_and_logs_activity(ctx):
    t = ctx.task(ctx.project()["id"], ctx.hana.id)
    r = ctx.client.patch(ctx.url(f"/tasks/{t['id']}"), json={"assigneeId": str(ctx.dawit.id)}, headers=ctx.h())
    assert r.status_code == 200 and r.json()["assignee"]["displayName"] == "Dawit"
    assert ctx.events[-1][0] == "task.updated" and ctx.events[-1][1]["changed"] == ["assigneeId"]

    by_type = {n.type: n for n in ctx.notifications(task_id=uuid.UUID(t["id"]))}
    assert by_type[NotificationType.UNASSIGNED].member_id == ctx.hana.id
    assigned = [n for n in ctx.notifications(type=NotificationType.ASSIGNED)]
    assert [n.member_id for n in assigned] == [ctx.hana.id, ctx.dawit.id]
    assert assigned[0].status == NotificationStatus.CANCELLED  # Hana's DM hadn't gone out yet

    # Reassigning back still DMs Hana (event-scoped key).
    ctx.client.patch(ctx.url(f"/tasks/{t['id']}"), json={"assigneeId": str(ctx.hana.id)}, headers=ctx.h())
    assert len(ctx.notifications(type=NotificationType.ASSIGNED)) == 3

    items = ctx.client.get(ctx.url(f"/tasks/{t['id']}/activity"), headers=ctx.h()).json()["items"]
    reassigned = [i for i in items if i.get("eventType") == "reassigned"]
    assert reassigned[0]["reassignedFrom"]["displayName"] == "Hana"
    assert reassigned[0]["reassignedTo"]["displayName"] == "Dawit"
    assert any(i["kind"] == "notification" and i["notificationType"] == "unassigned" for i in items)


def test_due_change_cancels_reminders_and_tells_assignee(ctx):
    t = ctx.task(ctx.project()["id"], ctx.hana.id)
    task_id = uuid.UUID(t["id"])
    from app.notifications import enqueue, message_payload

    enqueue(ctx.db, workspace_id=uuid.UUID(ctx.ws), member_id=ctx.hana.id, task_id=task_id,
            type=NotificationType.REMINDER, idempotency_key=f"reminder:{task_id}:x", payload=message_payload("r"))
    ctx.db.commit()
    new_due = "2026-12-24T09:00:00+03:00"
    r = ctx.client.patch(ctx.url(f"/tasks/{t['id']}"), json={"dueAt": new_due}, headers=ctx.h())
    assert r.json()["dueAt"] == "2026-12-24T06:00:00.000Z"
    assert ctx.notifications(type=NotificationType.REMINDER)[0].status == NotificationStatus.CANCELLED
    updated = ctx.notifications(type=NotificationType.TASK_UPDATED)
    assert len(updated) == 1 and "due date" in updated[0].payload["text"]
    items = ctx.client.get(ctx.url(f"/tasks/{t['id']}/activity"), headers=ctx.h()).json()["items"]
    due = next(i for i in items if i.get("eventType") == "due_changed")
    assert due["newDueAt"] == "2026-12-24T06:00:00.000Z"
    # No-op patch: nothing emitted, nothing queued.
    n_events = len(ctx.events)
    ctx.client.patch(ctx.url(f"/tasks/{t['id']}"), json={"dueAt": new_due}, headers=ctx.h())
    assert len(ctx.events) == n_events


def test_complete_and_reopen(ctx):
    t = ctx.task(ctx.project()["id"], ctx.hana.id)
    for _ in range(2):  # idempotent
        r = ctx.client.post(ctx.url(f"/tasks/{t['id']}/complete"), headers=ctx.h())
        assert r.json()["status"] == "completed" and r.json()["completedVia"] == "dashboard"
    assert [e for e, _ in ctx.events].count("task.completed") == 1
    assert len(ctx.notifications(type=NotificationType.TASK_UPDATED)) == 1  # assignee told once

    r = ctx.client.post(ctx.url(f"/tasks/{t['id']}/reopen"), headers=ctx.h())
    assert r.json()["status"] == "pending" and r.json()["completedAt"] is None


def test_telegram_completion_notifies_linked_managers_only_assignee_or_manager(ctx):
    t = ctx.task(ctx.project()["id"], ctx.hana.id)
    task_id = uuid.UUID(t["id"])
    with pytest.raises(Exception) as denied:
        task_service.complete_task(ctx.db, uuid.UUID(ctx.ws), task_id, ctx.dawit, ChangeSource.TELEGRAM)
    assert getattr(denied.value, "code", None) == "forbidden"
    ctx.db.rollback()

    ctx.db.add(TelegramUser(telegram_user_id=5000, first_name="Alice"))
    ctx.db.flush()
    ctx.manager.telegram_user_id = 5000
    ctx.manager.private_chat_id = 5000
    ctx.db.commit()
    task_service.complete_task(ctx.db, uuid.UUID(ctx.ws), task_id, ctx.hana, ChangeSource.TELEGRAM)
    (n,) = ctx.notifications(type=NotificationType.COMPLETED_MANAGER)
    assert n.member_id == ctx.manager.id and "Hana completed" in n.payload["text"]
    out = task_service.get_task_out(ctx.db, uuid.UUID(ctx.ws), task_id)
    assert out["completed_via"] == ChangeSource.TELEGRAM and out["completed_by"]["display_name"] == "Hana"


def test_delete_keeps_cancellation_dm(ctx):
    t = ctx.task(ctx.project()["id"], ctx.hana.id)
    assert ctx.client.delete(ctx.url(f"/tasks/{t['id']}"), headers=ctx.h()).status_code == 204
    assert ctx.client.get(ctx.url(f"/tasks/{t['id']}"), headers=ctx.h()).status_code == 404
    (n,) = ctx.notifications(type=NotificationType.TASK_DELETED)
    assert n.task_id is None and n.status == NotificationStatus.QUEUED
    assert ctx.events[-1][0] == "task.deleted"
    assert ctx.db.query(TaskEvent).count() == 0


def test_tenancy_and_invalid_references(ctx):
    ws_b = ctx.client.post("/api/v1/workspaces", json={"name": "B", "timezone": "UTC"}, headers=ctx.h("bob")).json()["id"]
    p_b = ctx.project("B's", ws=ws_b, user="bob")
    bob_member = ctx.add_member(2001, "Bobby", ws=ws_b)
    p_a = ctx.project()

    r = ctx.client.post(ctx.url("/tasks"), headers=ctx.h(), json={
        "title": "x", "projectId": p_b["id"], "assigneeId": str(ctx.hana.id), "dueAt": "2026-12-01T09:00:00Z"})
    assert r.status_code == 422 and r.json()["error"]["details"]["fields"][0]["field"] == "projectId"
    r = ctx.client.post(ctx.url("/tasks"), headers=ctx.h(), json={
        "title": "x", "projectId": p_a["id"], "assigneeId": str(bob_member.id), "dueAt": "2026-12-01T09:00:00Z"})
    assert r.status_code == 422 and r.json()["error"]["details"]["fields"][0]["field"] == "assigneeId"

    t = ctx.task(p_a["id"], ctx.hana.id)
    bob = ctx.issuer.headers("bob")
    assert ctx.client.get(f"/api/v1/workspaces/{ws_b}/tasks/{t['id']}", headers=bob).status_code == 404
    assert ctx.client.post(f"/api/v1/workspaces/{ws_b}/tasks/{t['id']}/complete", headers=bob).status_code == 404
    assert ctx.client.get(f"/api/v1/workspaces/{ws_b}/projects/{p_a['id']}", headers=bob).status_code == 404

    ctx.dawit.status = MemberStatus.REMOVED
    ctx.db.commit()
    r = ctx.client.post(ctx.url("/tasks"), headers=ctx.h(), json={
        "title": "x", "projectId": p_a["id"], "assigneeId": str(ctx.dawit.id), "dueAt": "2026-12-01T09:00:00Z"})
    assert r.json()["error"]["code"] == "assignee_not_active"
    r = ctx.client.post(ctx.url("/tasks"), headers=ctx.h(), json={
        "title": "x", "projectId": p_a["id"], "assigneeId": str(ctx.hana.id), "dueAt": "2026-12-01T09:00:00"})
    assert r.status_code == 422  # naive datetime


def test_per_task_reminder_offsets(ctx):
    t = ctx.task(ctx.project()["id"], ctx.hana.id, reminderOffsetsMinutes=[60, 15, 60])
    assert t["reminderOffsetsMinutes"] == [60, 15]
    r = ctx.client.patch(ctx.url(f"/tasks/{t['id']}"), json={"reminderOffsetsMinutes": []}, headers=ctx.h())
    assert r.json()["reminderOffsetsMinutes"] == [1440]


def test_publish_task_event_for_bot_callers(ctx, monkeypatch):
    import asyncio

    from app.services import task_realtime

    sent = []

    async def record(workspace_id, event, actor=None, source="dashboard", **payload):
        sent.append((event, source, payload))

    monkeypatch.setattr(task_realtime, "emit_workspace_event", record)
    t = ctx.task(ctx.project()["id"], ctx.hana.id)
    change = task_service.complete_task(ctx.db, uuid.UUID(ctx.ws), uuid.UUID(t["id"]), ctx.hana, ChangeSource.TELEGRAM)
    asyncio.run(task_realtime.publish_task_event(ctx.db, uuid.UUID(ctx.ws), change.task_id, "task.completed", ctx.hana, "telegram"))
    ((event, source, payload),) = sent
    assert event == "task.completed" and source == "telegram"
    assert payload["task"]["status"] == "completed" and payload["task"]["completedVia"] == "telegram"
