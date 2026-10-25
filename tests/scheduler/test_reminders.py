"""B5: reminder sweeps against a real DB, with an explicit clock."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import ChangeSource, Notification, NotificationStatus, NotificationType, TaskReminder
from app.scheduler import sweep
from app.scheduler.reminders import overdue_slots
from app.services import tasks as task_service
from tests.factories import make_member, make_project, make_task, make_workspace, manager_of

NOW = datetime.now(timezone.utc)


def reminders(db, type_=None):
    q = select(Notification).order_by(Notification.created_at)
    if type_ is not None:
        q = q.where(Notification.type == type_)
    return db.scalars(q).all()


def old_task(db, project, assignee, due_in, **kw):
    """A task created well before its reminders come due."""
    task = make_task(db, project, assignee, due_in=due_in, **kw)
    task.created_at = NOW - timedelta(days=10)
    db.commit()
    return task


def setup(db, tz="UTC", lead=24):
    ws = make_workspace(db, tz=tz)
    ws.reminder_lead_hours = lead
    db.commit()
    return ws, make_project(db, ws), make_member(db, ws)


def test_pre_due_reminder_once_and_catches_up_after_outage(db_session):
    db = db_session
    ws, project, hana = setup(db, lead=3)
    task = old_task(db, project, hana, due_in=timedelta(hours=4))

    assert sweep(db, NOW) == 0  # not due yet (fires at due - 3h)
    # The backend was down for 2 hours across the reminder time: the next sweep sends it.
    later = NOW + timedelta(hours=3)
    assert sweep(db, later) == 1
    assert sweep(db, later + timedelta(minutes=1)) == 0  # no duplicate
    (row,) = reminders(db, NotificationType.REMINDER)
    assert row.member_id == hana.id and row.task_id == task.id
    assert "Reminder: due in 1 hour" in row.payload["text"]
    assert row.payload["buttons"][0][0]["callback_data"] == f"task:complete:{task.id}"


def test_per_task_offsets_send_only_the_nearest_elapsed(db_session):
    db = db_session
    ws, project, hana = setup(db)
    task = old_task(db, project, hana, due_in=timedelta(hours=5))
    task.reminders = [TaskReminder(offset_minutes=m) for m in (60, 180, 600)]
    db.commit()

    sweep(db, NOW)  # 600 elapsed already
    sweep(db, NOW + timedelta(hours=4, minutes=30))  # after a gap: 180 and 60 elapsed, only 60 is sent
    keys = [r.idempotency_key.rsplit(":", 1)[1] for r in reminders(db, NotificationType.REMINDER)]
    assert keys == ["pre600", "pre60"]


def test_offsets_that_passed_before_creation_are_skipped(db_session):
    db = db_session
    ws, project, hana = setup(db, lead=24)
    make_task(db, project, hana, due_in=timedelta(hours=2))  # created now, inside the 24h window
    assert sweep(db, NOW + timedelta(minutes=1)) == 0


def test_overdue_at_due_then_daily_at_nine_max_three(db_session):
    db = db_session
    ws, project, hana = setup(db, tz="Africa/Addis_Ababa", lead=0)
    task = old_task(db, project, hana, due_in=timedelta(hours=1))
    tz = ZoneInfo("Africa/Addis_Ababa")
    slots = overdue_slots(task, tz)
    assert slots[0] == task.due_at and [s.astimezone(tz).hour for s in slots[1:]] == [9, 9]

    sweep(db, task.due_at + timedelta(minutes=1))
    sweep(db, slots[1] + timedelta(minutes=1))
    sweep(db, slots[2] + timedelta(days=5))  # long after the last slot
    sweep(db, slots[2] + timedelta(days=6))
    numbers = [r.idempotency_key.rsplit(":", 1)[1] for r in reminders(db, NotificationType.OVERDUE)]
    assert numbers == ["1", "2", "3"]
    assert "overdue" in reminders(db, NotificationType.OVERDUE)[0].payload["text"]


def test_outage_past_several_slots_sends_only_the_latest(db_session):
    db = db_session
    ws, project, hana = setup(db, lead=0)
    task = old_task(db, project, hana, due_in=timedelta(hours=1))
    sweep(db, overdue_slots(task, ZoneInfo("UTC"))[2] + timedelta(hours=1))
    assert [r.idempotency_key.rsplit(":", 1)[1] for r in reminders(db)] == ["3"]


def test_due_date_change_and_reassign_reschedule(db_session):
    db = db_session
    ws, project, hana = setup(db, lead=2)
    boss = manager_of(db, ws)
    task = old_task(db, project, hana, due_in=timedelta(hours=1))
    sweep(db, NOW)
    first = reminders(db, NotificationType.REMINDER)[0]

    # Moving the due date cancels the queued reminder (A4); the sweep queues a new one.
    task_service.update_task(db, boss, task.id, {"due_at": NOW + timedelta(hours=1, minutes=30)}, ChangeSource.DASHBOARD)
    db.expire_all()
    assert db.get(Notification, first.id).status == NotificationStatus.CANCELLED
    assert sweep(db, NOW) == 1

    sara = make_member(db, ws, "Sara")
    task_service.update_task(db, boss, task.id, {"assignee_id": sara.id}, ChangeSource.DASHBOARD)
    assert sweep(db, NOW) == 1
    queued = [r for r in reminders(db, NotificationType.REMINDER) if r.status == NotificationStatus.QUEUED]
    assert [r.member_id for r in queued] == [sara.id]


def test_completed_tasks_and_removed_assignees_get_nothing(db_session):
    db = db_session
    ws, project, hana = setup(db, lead=0)
    old_task(db, project, hana, due_in=timedelta(hours=-2), completed=True)
    gone = make_member(db, ws, "Gone")
    old_task(db, project, gone, due_in=timedelta(hours=-2))
    gone.status = "removed"
    db.commit()
    assert sweep(db, NOW) == 0


def test_pending_member_reminder_is_queued_for_when_they_join(db_session):
    db = db_session
    ws, project, hana = setup(db, lead=0)
    hana.status, hana.private_chat_id = "pending", None
    db.commit()
    old_task(db, project, hana, due_in=timedelta(hours=-1))
    assert sweep(db, NOW) == 1


def test_manager_digest_daily_at_nine_when_something_is_overdue(db_session):
    db = db_session
    ws, project, hana = setup(db, lead=0)
    boss = manager_of(db, ws)
    boss.private_chat_id = 5555
    db.commit()
    day = datetime(2030, 3, 4, tzinfo=timezone.utc)

    def at(hours, days=0):
        return day + timedelta(days=days, hours=hours)

    sweep(db, at(9.5))
    assert reminders(db, NotificationType.DIGEST) == []  # nothing overdue yet
    task = old_task(db, project, hana, due_in=timedelta(hours=1), title="Late <thing>")
    task.due_at = day - timedelta(days=2)
    db.commit()

    sweep(db, at(8))
    assert reminders(db, NotificationType.DIGEST) == []  # before 09:00
    sweep(db, at(9.5))
    sweep(db, at(14))  # same day: no second digest
    sweep(db, at(9.1, days=1))  # next morning
    digests = reminders(db, NotificationType.DIGEST)
    assert [d.member_id for d in digests] == [boss.id, boss.id]
    assert "Late &lt;thing&gt;" in digests[0].payload["text"] and "2 days late" in digests[0].payload["text"]
