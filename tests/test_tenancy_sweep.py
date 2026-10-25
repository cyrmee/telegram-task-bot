"""Every workspace-scoped route, called by a manager of another workspace, is a 404.

Driven by the OpenAPI spec, so a new route is covered automatically.
"""
import re

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from scripts.export_openapi import build_openapi
from tests.factories import make_member, make_project, make_task, make_workspace

SPEC = build_openapi()
WORKSPACE_ROUTES = [
    (method.upper(), path)
    for path, ops in SPEC["paths"].items()
    if path.startswith("/api/v1/workspaces/{workspaceId}")
    for method in ops
]
# Minimal valid bodies so validation can't mask the guard.
BODIES = {
    ("PATCH", "/api/v1/workspaces/{workspaceId}"): {"name": "x"},
    ("PUT", "/api/v1/workspaces/{workspaceId}/bot"): {"token": "123456:" + "a" * 35},
    ("POST", "/api/v1/workspaces/{workspaceId}/members"): {"telegramUsername": "someone"},
    ("PATCH", "/api/v1/workspaces/{workspaceId}/members/{memberId}"): {"role": "member"},
    ("POST", "/api/v1/workspaces/{workspaceId}/invites"): {},
    ("POST", "/api/v1/workspaces/{workspaceId}/projects"): {"name": "x"},
    ("PATCH", "/api/v1/workspaces/{workspaceId}/projects/{projectId}"): {"name": "x"},
    ("PATCH", "/api/v1/workspaces/{workspaceId}/tasks/{taskId}"): {"title": "x"},
}


def test_sweep_covers_every_workspace_route():
    assert len(WORKSPACE_ROUTES) >= 25


@pytest.mark.parametrize("method,path", WORKSPACE_ROUTES, ids=[f"{m} {p}" for m, p in WORKSPACE_ROUTES])
def test_other_workspace_manager_gets_404(db_session, issuer, method, path):
    victim = make_workspace(db_session, owner="bob", name="Victim")
    member = make_member(db_session, victim)
    project = make_project(db_session, victim)
    task = make_task(db_session, project, member)
    ids = {"workspaceId": victim.id, "memberId": member.id, "projectId": project.id, "taskId": task.id,
           "inviteId": member.id}
    make_workspace(db_session, owner="alice", name="Attacker")  # alice is a manager, just not here
    url = re.sub(r"\{(\w+)\}", lambda m: str(ids[m.group(1)]), path)
    body = BODIES.get((method, path))
    with TestClient(create_app()) as client:
        r = client.request(method, url, json=body, headers=issuer.headers("alice"))
    assert r.status_code == 404, f"{method} {url}: {r.status_code} {r.text}"
    assert r.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("method,path", WORKSPACE_ROUTES, ids=[f"{m} {p}" for m, p in WORKSPACE_ROUTES])
def test_unauthenticated_gets_401(db_session, method, path):
    url = re.sub(r"\{\w+\}", "00000000-0000-4000-8000-000000000000", path)
    with TestClient(create_app()) as client:
        r = client.request(method, url, json=BODIES.get((method, path)))
    assert r.status_code == 401
