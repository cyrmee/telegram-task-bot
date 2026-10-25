"""B6: the v1 command surface: /done, /help, and the fallback for anything else."""
import pytest

from app import db
from app.models import ChangeSource, Task, TaskStatus

from .fake_telegram import private_message
from .helpers import PERSON, TOKEN_A, add_member, connect, create_task, member_id, wait_for, webhook

BOT = 111111


@pytest.fixture
def workspace(client, issuer):
    r = client.post("/api/v1/workspaces", json={"name": "Gezat", "timezone": "UTC"}, headers=issuer.headers("alice"))
    ws = r.json()["id"]
    assert connect(client, issuer, "alice", ws, TOKEN_A).status_code == 200
    add_member(ws, PERSON)
    return ws


def say(client, tg, text, user=PERSON, n=1):
    assert webhook(client, BOT, tg.by_id(BOT).webhook_secret, private_message(text, user, n)).status_code == 200
    return tg.by_id(BOT).sent[-1]["text"]


def number_of(task_id) -> int:
    with db.SessionLocal() as s:
        return s.get(Task, task_id).number


def test_done_completes_your_own_task_by_number(client, issuer, tg, workspace):
    task_id = create_task(client, issuer, "alice", workspace, member_id(workspace, PERSON), title="Book venue")
    wait_for(lambda: tg.by_id(BOT).sent)  # assignment DM
    n = number_of(task_id)

    assert "Done: <b>#%d Book venue</b>" % n in say(client, tg, f"/done {n}", n=1)
    with db.SessionLocal() as s:
        task = s.get(Task, task_id)
        assert task.status == TaskStatus.COMPLETED and task.completed_via == ChangeSource.TELEGRAM
    assert "already completed" in say(client, tg, f"/done #{n}", n=2)
    # The assignment DM lost its buttons.
    assert wait_for(lambda: any("Completed" in e["text"] for e in tg.by_id(BOT).edits))


def test_done_refuses_other_peoples_tasks_and_bad_input(client, issuer, tg, workspace):
    other = add_member(workspace, 7002)
    task_id = create_task(client, issuer, "alice", workspace, other, title="Not yours")
    n = number_of(task_id)
    assert "isn't assigned to you" in say(client, tg, f"/done {n}", n=1)
    assert "no task #999" in say(client, tg, "/done 999", n=2)
    assert "/done 12" in say(client, tg, "/done", n=3)
    assert "not a member" in say(client, tg, f"/done {n}", user=9999, n=4)
    with db.SessionLocal() as s:
        assert s.get(Task, task_id).status == TaskStatus.PENDING


def test_help_and_fallback(client, issuer, tg, workspace):
    assert "/mytasks" in say(client, tg, "/help", n=1) and "/done 12" in tg.by_id(BOT).sent[-1]["text"]
    for i, text in enumerate(["/add_task buy milk", "/delete_task 3", "hello there"], start=2):
        reply = say(client, tg, text, n=i)
        assert reply.startswith("Sorry, I didn't understand that.") and "/mytasks" in reply
