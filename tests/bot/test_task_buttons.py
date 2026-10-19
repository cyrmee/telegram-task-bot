"""B3: task DMs with a Complete button, /mytasks, and retiring stale DMs.
Runs the real API, outbox worker and PTB stack against the fake Telegram."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import db
from app.bot import tasks as bot_tasks
from app.bot.tasks import TaskLine, render_task_list
from app.models import ChangeSource, Task, TaskStatus

from .fake_telegram import button_press, buttons, private_message
from .helpers import PERSON, TOKEN_A, add_member, connect, create_task, member_id, wait_for, webhook

BOT = 111111


@pytest.fixture
def published(monkeypatch):
    """Record realtime task events from the bot."""
    events = []
    original = bot_tasks.publish_task_event

    async def record(session, ws, task_id, event, actor, source="dashboard", **kw):
        events.append((event, str(task_id), source))
        await original(session, ws, task_id, event, actor, source=source, **kw)

    monkeypatch.setattr(bot_tasks, "publish_task_event", record)
    return events


@pytest.fixture
def workspace(client, issuer):
    r = client.post("/api/v1/workspaces", json={"name": "Gezat", "timezone": "Africa/Addis_Ababa"}, headers=issuer.headers("alice"))
    ws = r.json()["id"]
    assert connect(client, issuer, "alice", ws, TOKEN_A).status_code == 200
    add_member(ws, PERSON)
    return ws


def assignment_dm(tg, title):
    bot = tg.by_id(BOT)
    return wait_for(lambda: next((m for m in bot.sent if title in m["text"] and buttons(m)), None))


def press(client, tg, data, user, message, n=50):
    secret = tg.by_id(BOT).webhook_secret
    assert webhook(client, BOT, secret, button_press(data, user, message, n)).status_code == 200


def task_row(task_id) -> Task:
    with db.SessionLocal() as s:
        return s.get(Task, task_id)


def test_one_tap_complete(client, issuer, tg, workspace, published):
    task_id = create_task(client, issuer, "alice", workspace, member_id(workspace, PERSON), title="Print <banners>")
    dm = assignment_dm(tg, "Print &lt;banners&gt;")
    assert buttons(dm) == [f"task:complete:{task_id}", "tasks:mine"]

    press(client, tg, f"task:complete:{task_id}", PERSON, dm)

    row = task_row(task_id)
    assert row.status == TaskStatus.COMPLETED and row.completed_via == ChangeSource.TELEGRAM
    bot = tg.by_id(BOT)
    assert bot.answers[-1]["text"] == "Marked complete ✅"
    edit = bot.edits[-1]
    assert edit["message_id"] == dm["message_id"] and "Completed" in edit["text"]
    assert not edit.get("reply_markup")  # buttons gone
    assert published == [("task.completed", task_id, "telegram")]

    # Pressing again is harmless.
    press(client, tg, f"task:complete:{task_id}", PERSON, dm, 51)
    assert bot.answers[-1]["text"] == "Already completed"
    assert len(published) == 1


def test_only_the_assignee_can_complete(client, issuer, tg, workspace, published):
    task_id = create_task(client, issuer, "alice", workspace, member_id(workspace, PERSON))
    dm = assignment_dm(tg, "Write launch copy")
    add_member(workspace, 7002)

    press(client, tg, f"task:complete:{task_id}", 7002, dm)
    answer = tg.by_id(BOT).answers[-1]
    assert answer["show_alert"] is True and "assigned to" in answer["text"]
    press(client, tg, f"task:complete:{task_id}", 9999, dm, 51)  # not in the workspace
    assert "not a member" in tg.by_id(BOT).answers[-1]["text"]

    assert task_row(task_id).status == TaskStatus.PENDING
    assert published == []


def test_button_from_another_workspace_is_rejected(client, issuer, tg, workspace):
    """A task id from workspace A pressed on workspace B's bot finds nothing."""
    task_id = create_task(client, issuer, "alice", workspace, member_id(workspace, PERSON))
    dm = assignment_dm(tg, "Write launch copy")
    r = client.post("/api/v1/workspaces", json={"name": "Other", "timezone": "UTC"}, headers=issuer.headers("bob"))
    other = r.json()["id"]
    from .helpers import TOKEN_B

    connect(client, issuer, "bob", other, TOKEN_B)
    add_member(other, PERSON)
    secret_b = tg.by_id(222222).webhook_secret
    webhook(client, 222222, secret_b, button_press(f"task:complete:{task_id}", PERSON, dm))
    assert "no longer exists" in tg.by_id(222222).answers[-1]["text"]
    assert task_row(task_id).status == TaskStatus.PENDING


def test_mytasks_lists_open_tasks_with_buttons_and_updates_in_place(client, issuer, tg, workspace, published):
    me = member_id(workspace, PERSON)
    first = create_task(client, issuer, "alice", workspace, me, title="Tomorrow thing", due_in=timedelta(days=1, hours=1))
    second = create_task(client, issuer, "alice", workspace, me, title="Next week thing", due_in=timedelta(days=6))
    dm = assignment_dm(tg, "Tomorrow thing")
    assignment_dm(tg, "Next week thing")
    webhook(client, BOT, tg.by_id(BOT).webhook_secret, private_message("/mytasks", PERSON, 7))

    listing = wait_for(lambda: next((m for m in tg.by_id(BOT).sent if "Your open tasks" in m["text"]), None))
    assert listing["text"].index("Tomorrow thing") < listing["text"].index("Next week thing")
    assert buttons(listing) == [f"list:complete:{first}", f"list:complete:{second}"]

    press(client, tg, f"list:complete:{first}", PERSON, listing)
    edit = next(e for e in tg.by_id(BOT).edits if e["message_id"] == listing["message_id"])
    assert "Tomorrow thing" not in edit["text"] and "Next week thing" in edit["text"]
    assert task_row(first).status == TaskStatus.COMPLETED
    # The original assignment DM was retired too.
    assert any(e["message_id"] == dm["message_id"] and "Completed" in e["text"] for e in tg.by_id(BOT).edits)


def test_my_tasks_button_sends_the_list(client, issuer, tg, workspace):
    create_task(client, issuer, "alice", workspace, member_id(workspace, PERSON))
    dm = assignment_dm(tg, "Write launch copy")
    press(client, tg, "tasks:mine", PERSON, dm)
    assert wait_for(lambda: any("Your open tasks" in m["text"] for m in tg.by_id(BOT).sent))


def test_reassign_retires_the_old_dm(client, issuer, tg, workspace):
    task_id = create_task(client, issuer, "alice", workspace, member_id(workspace, PERSON))
    dm = assignment_dm(tg, "Write launch copy")
    other = add_member(workspace, 7002)
    r = client.patch(f"/api/v1/workspaces/{workspace}/tasks/{task_id}", json={"assigneeId": str(other)}, headers=issuer.headers("alice"))
    assert r.status_code == 200, r.text

    edit = wait_for(lambda: next((e for e in tg.by_id(BOT).edits if e["message_id"] == dm["message_id"]), None))
    assert "Reassigned" in edit["text"] and not edit.get("reply_markup")
    # The new assignee got their own DM with a button.
    assert wait_for(lambda: any(int(m["chat_id"]) == 7002 and buttons(m) for m in tg.by_id(BOT).sent))


def test_delete_and_dashboard_complete_retire_dms(client, issuer, tg, workspace):
    me = member_id(workspace, PERSON)
    doomed = create_task(client, issuer, "alice", workspace, me, title="Doomed task")
    done = create_task(client, issuer, "alice", workspace, me, title="Done elsewhere")
    doomed_dm, done_dm = assignment_dm(tg, "Doomed task"), assignment_dm(tg, "Done elsewhere")
    headers = issuer.headers("alice")
    assert client.delete(f"/api/v1/workspaces/{workspace}/tasks/{doomed}", headers=headers).status_code == 204
    assert client.post(f"/api/v1/workspaces/{workspace}/tasks/{done}/complete", headers=headers).status_code == 200

    def edited(message_id, word):
        return any(e["message_id"] == message_id and word in e["text"] for e in tg.by_id(BOT).edits)

    wait_for(lambda: edited(doomed_dm["message_id"], "cancelled"))
    wait_for(lambda: edited(done_dm["message_id"], "Completed"))


def test_render_task_list_groups_by_day_and_escapes():
    tz = ZoneInfo("Africa/Addis_Ababa")
    now = datetime(2026, 10, 20, 9, 0, tzinfo=tz)
    rows = [
        TaskLine("00000000-0000-4000-8000-000000000001", 1, "Late <one>", "Ops & Co", now - timedelta(hours=3)),
        TaskLine("00000000-0000-4000-8000-000000000002", 2, "Later today", "Ops", now + timedelta(hours=2)),
        TaskLine("00000000-0000-4000-8000-000000000003", 3, "Tomorrow", "Ops", now + timedelta(days=1)),
        TaskLine("00000000-0000-4000-8000-000000000004", 4, "Friday", "Ops", now + timedelta(days=4)),
    ]
    text, markup = render_task_list(rows, tz, now=now.astimezone(timezone.utc))
    for heading in ("⚠️ Overdue", "Today", "Tomorrow", "Sat 24 Oct"):
        assert f"<b>{heading}</b>" in text
    assert "Late &lt;one&gt;" in text and "Ops &amp; Co" in text
    assert len(markup.inline_keyboard) == 4
    assert render_task_list([], tz) == ("You have no open tasks. 🎉", None)
