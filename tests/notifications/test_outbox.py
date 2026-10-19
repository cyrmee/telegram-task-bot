import asyncio
import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import Member, MemberStatus, Notification, NotificationStatus, NotificationType
from app.notifications import (
    BotUnavailable,
    Button,
    OutboxWorker,
    PermanentSendError,
    RateLimited,
    RecipientBlocked,
    RetryPolicy,
    TransientSendError,
    assigned_key,
    cancel_queued,
    enqueue,
    message_payload,
    reminder_key,
    wake_member,
    wake_workspace,
)

NO_JITTER = RetryPolicy(jitter=0.0)


class FakeTelegram:
    """Records delivered messages. `fail` is a list of exceptions raised by the next calls."""

    def __init__(self):
        self.delivered = []
        self.fail: list[Exception] = []
        self.calls = 0

    async def send(self, message):
        self.calls += 1
        if self.fail:
            raise self.fail.pop(0)
        self.delivered.append(message)
        return 500 + len(self.delivered)


def queue(session_factory, world, key=None, text="Hello", **kw):
    with session_factory() as s, s.begin():
        return enqueue(
            s,
            workspace_id=world.workspace_id,
            member_id=world.member_id,
            task_id=world.task_id,
            type=NotificationType.ASSIGNED,
            idempotency_key=key or assigned_key(world.task_id, world.member_id),
            payload=message_payload(text, [[Button("✅ Mark complete", callback_data="done:1")]]),
            **kw,
        )


def get(session_factory, notification_id) -> Notification:
    with session_factory() as s:
        return s.get(Notification, notification_id)


def worker(session_factory, tg, clock, **kw):
    kw.setdefault("policy", NO_JITTER)
    return OutboxWorker(session_factory, tg.send, clock=clock, **kw)


def run(coro):
    return asyncio.run(coro)


# --- enqueue ---


def test_enqueue_is_idempotent(session_factory, make_world):
    w = make_world()
    first = queue(session_factory, w)
    second = queue(session_factory, w, text="different text")
    assert first is not None and second is None
    with session_factory() as s:
        rows = s.scalars(select(Notification)).all()
    assert len(rows) == 1 and rows[0].payload["text"] == "Hello"


def test_enqueue_rolls_back_with_the_change(session_factory, make_world):
    w = make_world()
    with session_factory() as s:
        with pytest.raises(RuntimeError):
            with s.begin():
                enqueue(
                    s,
                    workspace_id=w.workspace_id,
                    member_id=w.member_id,
                    type=NotificationType.ASSIGNED,
                    idempotency_key="k",
                    payload=message_payload("x"),
                )
                raise RuntimeError("task change failed")
    with session_factory() as s:
        assert s.scalars(select(Notification)).all() == []


def test_enqueue_rejects_task_from_another_workspace(session_factory, make_world):
    a, b = make_world(), make_world()
    with session_factory() as s, s.begin():
        with pytest.raises(ValueError):
            enqueue(
                s,
                workspace_id=a.workspace_id,
                member_id=a.member_id,
                task_id=b.task_id,
                type=NotificationType.ASSIGNED,
                idempotency_key="cross",
                payload=message_payload("x"),
            )


# --- worker outcomes ---


def test_sends_and_records_message(session_factory, make_world, clock):
    w = make_world()
    nid = queue(session_factory, w)
    tg = FakeTelegram()
    assert run(worker(session_factory, tg, clock).run_once()) == 1

    (msg,) = tg.delivered
    assert msg.chat_id == w.chat_id and msg.text == "Hello"
    assert msg.buttons[0][0].callback_data == "done:1"
    row = get(session_factory, nid)
    assert row.status == NotificationStatus.SENT
    assert row.telegram_message_id == 501 and row.telegram_chat_id == w.chat_id
    assert row.attempts == 1 and row.sent_at == clock.now
    # Nothing left to do.
    assert run(worker(session_factory, tg, clock).run_once()) == 0


def test_transient_errors_back_off_then_fail(session_factory, make_world, clock):
    w = make_world()
    nid = queue(session_factory, w)
    tg = FakeTelegram()
    policy = RetryPolicy(max_attempts=3, base_delay=10, jitter=0.0)
    wk = worker(session_factory, tg, clock, policy=policy)

    tg.fail = [TransientSendError("502")] * 3
    run(wk.run_once())
    row = get(session_factory, nid)
    assert row.status == NotificationStatus.QUEUED and row.attempts == 1
    assert (row.next_attempt_at - clock.now).total_seconds() == pytest.approx(10)

    assert run(wk.run_once()) == 0  # not due yet
    clock.advance(10)
    run(wk.run_once())
    row = get(session_factory, nid)
    assert row.attempts == 2
    assert (row.next_attempt_at - clock.now).total_seconds() == pytest.approx(20)

    clock.advance(20)
    run(wk.run_once())
    row = get(session_factory, nid)
    assert row.status == NotificationStatus.FAILED and row.attempts == 3
    assert "502" in row.last_error
    assert tg.delivered == []


def test_unexpected_exception_and_timeout_are_retried(session_factory, make_world, clock):
    w = make_world()
    nid = queue(session_factory, w)

    class Hang:
        async def send(self, message):
            await asyncio.sleep(10)

    run(OutboxWorker(session_factory, Hang().send, clock=clock, send_timeout=0.05).run_once())
    row = get(session_factory, nid)
    assert row.status == NotificationStatus.QUEUED and "TimeoutError" in row.last_error


def test_rate_limit_waits_without_using_attempts_and_holds_the_workspace(session_factory, make_world, clock):
    w = make_world()
    first = queue(session_factory, w, key="a")
    second = queue(session_factory, w, key="b")
    tg = FakeTelegram()
    tg.fail = [RateLimited(30)]
    run(worker(session_factory, tg, clock).run_once())

    assert tg.calls == 1  # the second row wasn't tried
    for nid in (first, second):
        row = get(session_factory, nid)
        assert row.status == NotificationStatus.QUEUED and row.attempts == 0
        assert (row.next_attempt_at - clock.now).total_seconds() == pytest.approx(30)

    clock.advance(30)
    run(worker(session_factory, tg, clock).run_once())
    assert len(tg.delivered) == 2


def test_blocked_recipient_fails_and_marks_member_unreachable(session_factory, make_world, clock):
    w = make_world()
    nid = queue(session_factory, w, key="a")
    later = queue(session_factory, w, key="b")
    tg = FakeTelegram()
    tg.fail = [RecipientBlocked("bot was blocked by the user")]
    run(worker(session_factory, tg, clock).run_once())

    assert get(session_factory, nid).status == NotificationStatus.FAILED
    with session_factory() as s:
        assert s.get(Member, w.member_id).status == MemberStatus.UNREACHABLE
    # The next row for that member isn't sent at all.
    assert get(session_factory, later).status == NotificationStatus.FAILED
    assert tg.calls == 1


def test_permanent_error_fails_immediately(session_factory, make_world, clock):
    w = make_world()
    nid = queue(session_factory, w)
    tg = FakeTelegram()
    tg.fail = [PermanentSendError("can't parse entities")]
    run(worker(session_factory, tg, clock).run_once())
    row = get(session_factory, nid)
    assert row.status == NotificationStatus.FAILED and row.attempts == 1


def test_unlinked_member_waits_until_woken(session_factory, make_world, clock):
    w = make_world(linked=False)
    nid = queue(session_factory, w)
    tg = FakeTelegram()
    run(worker(session_factory, tg, clock).run_once())
    row = get(session_factory, nid)
    assert row.status == NotificationStatus.QUEUED and row.attempts == 0
    assert row.next_attempt_at > clock.now

    with session_factory() as s, s.begin():  # B2 links the chat on /start
        s.get(Member, w.member_id).private_chat_id = w.chat_id
        assert wake_member(s, w.member_id) == 1
    clock.advance(1)
    run(worker(session_factory, tg, clock).run_once())
    assert get(session_factory, nid).status == NotificationStatus.SENT


def test_missing_bot_waits_until_workspace_woken(session_factory, make_world, clock):
    w = make_world()
    nid = queue(session_factory, w)
    tg = FakeTelegram()
    tg.fail = [BotUnavailable()]
    run(worker(session_factory, tg, clock).run_once())
    row = get(session_factory, nid)
    assert row.status == NotificationStatus.QUEUED and row.attempts == 0

    with session_factory() as s, s.begin():  # B1 connects a bot
        assert wake_workspace(s, w.workspace_id) == 1
    clock.advance(1)
    run(worker(session_factory, tg, clock).run_once())
    assert get(session_factory, nid).status == NotificationStatus.SENT


def test_wake_leaves_future_reminders_alone(session_factory, make_world, clock):
    w = make_world()
    nid = queue(session_factory, w, key=reminder_key(w.task_id, "pre-24h"), send_at=clock.now.replace(year=2099))
    with session_factory() as s, s.begin():
        assert wake_workspace(s, w.workspace_id) == 0
    assert get(session_factory, nid).next_attempt_at.year == 2099


def test_removed_member_is_cancelled(session_factory, make_world, clock):
    w = make_world()
    nid = queue(session_factory, w)
    with session_factory() as s, s.begin():
        s.get(Member, w.member_id).status = MemberStatus.REMOVED
    tg = FakeTelegram()
    run(worker(session_factory, tg, clock).run_once())
    assert get(session_factory, nid).status == NotificationStatus.CANCELLED
    assert tg.calls == 0


def test_cancel_queued_by_task_and_type(session_factory, make_world, clock):
    w = make_world()
    assigned = queue(session_factory, w)
    reminder = queue(session_factory, w, key=reminder_key(w.task_id, "pre-24h"))
    with session_factory() as s, s.begin():
        s.get(Notification, reminder).type = NotificationType.REMINDER
    with session_factory() as s, s.begin():
        assert cancel_queued(s, task_id=w.task_id, types=[NotificationType.REMINDER]) == 1
    assert get(session_factory, reminder).status == NotificationStatus.CANCELLED
    assert get(session_factory, assigned).status == NotificationStatus.QUEUED


def test_send_at_defers_delivery(session_factory, make_world, clock):
    w = make_world()
    nid = queue(session_factory, w, send_at=clock.now + timedelta(hours=1))
    tg = FakeTelegram()
    assert run(worker(session_factory, tg, clock).run_once()) == 0
    clock.advance(3600)
    run(worker(session_factory, tg, clock).run_once())
    assert get(session_factory, nid).status == NotificationStatus.SENT


# --- concurrency and crashes ---


def test_concurrent_workers_never_claim_the_same_row(session_factory, make_world, clock):
    w = make_world()
    ids = {queue(session_factory, w, key=f"k{i}") for i in range(40)}
    workers = [worker(session_factory, FakeTelegram(), clock, batch_size=7) for _ in range(4)]
    claimed: list[list[uuid.UUID]] = []
    barrier = threading.Barrier(len(workers))

    def claim(wk):
        barrier.wait()
        claimed.append([c.id for c in wk._claim(clock.now)])

    threads = [threading.Thread(target=claim, args=(wk,)) for wk in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    flat = [i for batch in claimed for i in batch]
    assert len(flat) == len(set(flat)) == 28
    assert set(flat) <= ids


def test_row_of_a_crashed_worker_is_retried_after_its_lease(session_factory, make_world, clock):
    w = make_world()
    nid = queue(session_factory, w)
    tg = FakeTelegram()
    crashed = worker(session_factory, tg, clock)
    crashed._claim(clock.now)  # claimed, then the process died before sending

    assert run(worker(session_factory, tg, clock).run_once()) == 0
    clock.advance(crashed.lease.total_seconds())
    run(worker(session_factory, tg, clock).run_once())
    row = get(session_factory, nid)
    assert row.status == NotificationStatus.SENT and row.attempts == 2
    assert len(tg.delivered) == 1


def test_connectivity_cut_mid_batch_delivers_everything_exactly_once(session_factory, make_world, clock):
    """B4 done-when: cut Telegram connectivity mid-send, restore it, and every
    message arrives exactly once."""
    worlds = [make_world() for _ in range(3)]
    expected = set()
    for w in worlds:
        for i in range(10):
            queue(session_factory, w, key=f"{w.workspace_id}:{i}", text=f"{w.workspace_id}:{i}")
            expected.add(f"{w.workspace_id}:{i}")

    tg = FakeTelegram()
    wk = worker(session_factory, tg, clock, batch_size=8)
    run(wk.run_once())  # 8 sent while connected
    tg.fail = [TransientSendError("Network is unreachable")] * 40  # connection drops
    for _ in range(5):
        run(wk.run_once())
        clock.advance(15)
    tg.fail = []  # connection restored
    for _ in range(60):
        clock.advance(60)
        run(wk.run_once())

    texts = [m.text for m in tg.delivered]
    assert len(texts) == len(set(texts)) == 30
    assert set(texts) == expected
    with session_factory() as s:
        statuses = set(s.scalars(select(Notification.status)).all())
    assert statuses == {NotificationStatus.SENT}


def test_run_forever_stops_cleanly(session_factory, make_world):
    w = make_world()
    queue(session_factory, w)
    tg = FakeTelegram()

    async def main():
        stop = asyncio.Event()
        task = asyncio.create_task(OutboxWorker(session_factory, tg.send, poll_interval=0.01).run_forever(stop))
        for _ in range(200):
            if tg.delivered:
                break
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    run(main())
    assert len(tg.delivered) == 1


def test_on_sent_hook_gets_the_delivered_row_and_errors_are_contained(session_factory, make_world, clock):
    w = make_world()
    nid = queue(session_factory, w, key="unassigned:x:1")
    seen = []

    async def hook(sent):
        seen.append(sent)
        raise RuntimeError("hook bug")

    tg = FakeTelegram()
    run(OutboxWorker(session_factory, tg.send, clock=clock, on_sent=hook).run_once())
    (sent,) = seen
    assert sent.notification_id == nid and sent.idempotency_key == "unassigned:x:1"
    assert sent.type == NotificationType.ASSIGNED and sent.chat_id == w.chat_id and sent.message_id == 501
    assert get(session_factory, nid).status == NotificationStatus.SENT
