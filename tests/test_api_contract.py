"""The /api/v1 contract: committed OpenAPI is current, errors use the agreed shape."""
import importlib.util
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_manager
from app.api.errors import register_error_handlers
from app.api.v1 import api_router

ROOT = Path(__file__).resolve().parents[1]


def load_export_script():
    spec = importlib.util.spec_from_file_location("export_openapi", ROOT / "scripts" / "export_openapi.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client():
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(api_router)
    return TestClient(app)


def test_committed_openapi_is_current():
    export = load_export_script()
    assert (ROOT / "docs" / "openapi.json").read_text() == export.render(), (
        "docs/openapi.json is stale: run python scripts/export_openapi.py"
    )


def test_every_workspace_route_is_scoped_and_authenticated():
    spec = load_export_script().build_openapi()
    for path, ops in spec["paths"].items():
        assert path.startswith("/api/v1/")
        for method, op in ops.items():
            assert op.get("security") == [{"HTTPBearer": []}], f"{method.upper()} {path} has no auth"
            if path not in ("/api/v1/me", "/api/v1/workspaces"):
                assert path.startswith("/api/v1/workspaces/{workspaceId}/") or path == "/api/v1/workspaces/{workspaceId}"


def test_missing_token_is_401_with_error_shape(client):
    r = client.get("/api/v1/me", headers={"X-Request-ID": "req-1"})
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "unauthenticated", "message": "Missing bearer token.", "requestId": "req-1"}}


def test_validation_error_shape(client):
    client.app.dependency_overrides[require_manager] = lambda: None
    r = client.post(
        f"/api/v1/workspaces/{uuid.uuid4()}/tasks",
        json={"title": ""},
        headers={"Authorization": "Bearer x"},
    )
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error"
    fields = {f["field"] for f in body["details"]["fields"]}
    assert {"title", "projectId", "assigneeId", "dueAt"} <= fields


def test_unknown_route_uses_error_shape(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
