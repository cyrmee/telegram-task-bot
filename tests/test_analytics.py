"""A6: analytics numbers, range semantics in the workspace timezone, speed."""
import importlib.util
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Member, MemberRole, MemberStatus, Project, Task, TaskStatus, TelegramUser, Workspace
from app.services.analytics import get_analytics

ADDIS = ZoneInfo("Africa/Addis_Ababa")  # UTC+3


@pytest.fixture
def ws(db_session):
    w = Workspace(name="Acme", owner_user_id="alice", timezone="Africa/Addis_Ababa")
    db_session.add(w)
    db_session.flush()
    manager = Member(workspace_id=w.id, user_id="alice", display_name="Alice", role=MemberRole.MANAGER, status=MemberStatus.ACTIVE)
    db_session.add_all([TelegramUser(telegram_user_id=1, first_name="Hana"), TelegramUser(telegram_user_id=2, first_name="Dawit")])
    db_session.flush()
    hana = Member(workspace_id=w.id, telegram_user_id=1, status=MemberStatus.ACTIVE)
    dawit = Member(workspace_id=w.id, telegram_user_id=2, status=MemberStatus.ACTIVE)
    a, b = Project(workspace_id=w.id, name="A"), Project(workspace_id=w.id, name="B")
    db_session.add_all([manager, hana, dawit, a, b])
    db_session.commit()
    return db_session, w, {"alice": manager, "hana": hana, "dawit": dawit}, {"a": a, "b": b}


def add_task(db, w, project, member, due: datetime, done=False):
    n = db.query(Task).filter_by(workspace_id=w.id).count() + 1
    t = Task(
        workspace_id=w.id, number=n, project_id=project.id, assignee_member_id=member.id, title=f"t{n}", due_at=due,
        status=TaskStatus.COMPLETED if done else TaskStatus.PENDING,
        completed_at=due if done else None,
    )
    db.add(t)
    db.commit()
    return t


def test_numbers(ws):
    db, w, m, p = ws
    now = datetime.now(timezone.utc)
    add_task(db, w, p["a"], m["hana"], now - timedelta(days=2), done=True)
    add_task(db, w, p["a"], m["hana"], now - timedelta(days=1))  # overdue
    add_task(db, w, p["a"], m["dawit"], now - timedelta(days=3))  # overdue
    add_task(db, w, p["b"], m["dawit"], now - timedelta(days=5), done=True)
    add_task(db, w, p["b"], m["hana"], now - timedelta(days=60))  # out of range, still overdue/open

    r = get_analytics(db, w.id)
    assert r["totals"] == {"total": 4, "completed": 2, "pending": 2, "overdue": 2, "completion_rate": 0.5}
    by_project = {x["name"]: x for x in r["by_project"]}
    assert by_project["A"]["total"] == 3 and by_project["A"]["completion_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert by_project["B"]["completion_rate"] == 1.0
    by_member = {x["display_name"]: x for x in r["by_member"]}
    assert by_member["Hana"]["total"] == 2 and by_member["Hana"]["open_tasks"] == 2  # includes the out-of-range one
    assert by_member["Dawit"]["overdue"] == 1 and by_member["Dawit"]["open_tasks"] == 1
    assert by_member["Alice"]["total"] == 0 and by_member["Alice"]["completion_rate"] is None
    assert [t["title"] for t in r["overdue_tasks"]] == ["t5", "t3", "t2"]  # all overdue, oldest first
    assert r["range"]["to"] == datetime.now(ADDIS).date()
    assert (r["range"]["to"] - r["range"]["from"]).days == 29


def test_range_boundaries_use_workspace_timezone(ws):
    db, w, m, p = ws
    # 23:30 Addis on Nov 30 is 20:30 UTC Nov 30; 00:30 Addis on Dec 1 is 21:30 UTC Nov 30.
    add_task(db, w, p["a"], m["hana"], datetime(2026, 11, 30, 23, 30, tzinfo=ADDIS), done=True)
    add_task(db, w, p["a"], m["hana"], datetime(2026, 12, 1, 0, 30, tzinfo=ADDIS), done=True)
    nov = get_analytics(db, w.id, date(2026, 11, 1), date(2026, 11, 30))
    dec = get_analytics(db, w.id, date(2026, 12, 1), date(2026, 12, 31))
    assert nov["totals"]["total"] == 1 and dec["totals"]["total"] == 1


def test_archived_project_shown_only_with_tasks(ws):
    db, w, m, p = ws
    from app.models import ProjectStatus

    p["b"].status = ProjectStatus.ARCHIVED
    db.commit()
    assert {x["name"] for x in get_analytics(db, w.id)["by_project"]} == {"A"}
    add_task(db, w, p["b"], m["hana"], datetime.now(timezone.utc) - timedelta(days=1))
    assert {x["name"] for x in get_analytics(db, w.id)["by_project"]} == {"A", "B"}


def test_endpoint_validation_and_tenancy(db_session, issuer):
    with TestClient(create_app()) as client:
        h = issuer.headers("alice")
        ws_id = client.post("/api/v1/workspaces", json={"name": "A", "timezone": "UTC"}, headers=h).json()["id"]
        r = client.get(f"/api/v1/workspaces/{ws_id}/analytics?from=2026-10-01&to=2026-10-31", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["range"] == {"from": "2026-10-01", "to": "2026-10-31"}
        assert body["totals"]["completionRate"] is None and body["generatedAt"].endswith("Z")
        bad = client.get(f"/api/v1/workspaces/{ws_id}/analytics?from=2026-11-01&to=2026-10-01", headers=h)
        assert bad.status_code == 422
        too_long = client.get(f"/api/v1/workspaces/{ws_id}/analytics?from=2024-01-01&to=2026-01-01", headers=h)
        assert too_long.status_code == 422
        assert client.get(f"/api/v1/workspaces/{ws_id}/analytics", headers=issuer.headers("bob")).status_code == 404


def test_fast_on_seed_data(db_session):
    spec = importlib.util.spec_from_file_location("seed", Path(__file__).resolve().parents[1] / "scripts" / "seed.py")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    seed.seed(None)
    ws_id = seed.SEED_WORKSPACE_ID
    get_analytics(db_session, ws_id)  # warm up
    started = time.perf_counter()
    result = get_analytics(db_session, ws_id)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert result["totals"]["total"] > 0
    assert elapsed_ms < 200, f"analytics took {elapsed_ms:.0f} ms"
