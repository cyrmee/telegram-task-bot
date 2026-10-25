"""A2 done-when: user A can't read or modify workspace B over REST or
sockets, and unauthenticated calls get 401."""
import asyncio
import socket
import threading
import time
import uuid

import jwt
import pytest
import socketio
import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Member, MemberRole, MemberStatus
from app.realtime import emit_workspace_event, sio


@pytest.fixture
def client(db_session, issuer):
    with TestClient(create_app()) as c:
        yield c


def make_workspace(client, issuer, user: str, name: str = "Acme") -> str:
    r = client.post("/api/v1/workspaces", json={"name": name, "timezone": "Africa/Addis_Ababa"}, headers=issuer.headers(user))
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- authentication ------------------------------------------------------------

def test_no_token_is_401(client):
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"


@pytest.mark.parametrize(
    "token_kwargs",
    [
        {"expires_in": -120},  # expired beyond leeway
        {"aud": "someone-else"},
        {"iss": "https://evil.example"},
        {"key": Ed25519PrivateKey.generate()},  # signed by a key not in the JWKS
    ],
    ids=["expired", "wrong-aud", "wrong-iss", "unknown-key"],
)
def test_bad_tokens_are_401(client, issuer, token_kwargs):
    r = client.get("/api/v1/me", headers=issuer.headers("alice", **token_kwargs))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"


def test_hs256_and_none_tokens_rejected(client, issuer):
    claims = {"sub": "alice", "aud": "nest-api", "iss": "http://localhost:3000", "exp": int(time.time()) + 600}
    for token in (
        jwt.encode(claims, "secret", algorithm="HS256", headers={"kid": issuer.kid}),
        jwt.encode(claims, None, algorithm="none", headers={"kid": issuer.kid}),
    ):
        r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


def test_me_lists_only_own_workspaces(client, issuer):
    ws_a = make_workspace(client, issuer, "alice", "A")
    make_workspace(client, issuer, "bob", "B")
    r = client.get("/api/v1/me", headers=issuer.headers("alice"))
    assert r.status_code == 200
    body = r.json()
    assert body["user"] == {"id": "alice", "email": "alice@example.com", "name": "Alice", "image": None}
    assert [w["id"] for w in body["workspaces"]] == [ws_a]
    assert body["workspaces"][0]["role"] == "manager"
    assert body["workspaces"][0]["onboarding"] == {
        "hasBot": False,
        "memberCount": 0,
        "pendingMemberCount": 0,
        "managerTelegramLinked": False,
        "projectCount": 0,
        "taskCount": 0,
        "dismissedAt": None,
        "skippedSteps": [],
    }


# --- tenancy -------------------------------------------------------------------

def test_cross_workspace_read_and_write_are_404(client, issuer):
    ws_b = make_workspace(client, issuer, "bob", "B")
    alice = issuer.headers("alice")
    assert client.get(f"/api/v1/workspaces/{ws_b}", headers=alice).status_code == 404
    r = client.patch(f"/api/v1/workspaces/{ws_b}", json={"name": "pwned"}, headers=alice)
    assert r.status_code == 404
    assert client.get(f"/api/v1/workspaces/{ws_b}", headers=issuer.headers("bob")).json()["name"] == "B"
    # Every other workspace-scoped route goes through the same guard.
    for path in ("members", "invites", "projects", "tasks", "analytics", "bot"):
        assert client.get(f"/api/v1/workspaces/{ws_b}/{path}", headers=alice).status_code == 404, path


def test_unknown_workspace_is_404(client, issuer):
    assert client.get(f"/api/v1/workspaces/{uuid.uuid4()}", headers=issuer.headers("alice")).status_code == 404


def test_member_role_is_403(client, issuer, db_session):
    ws = make_workspace(client, issuer, "bob")
    db_session.add(Member(workspace_id=uuid.UUID(ws), user_id="carol", role=MemberRole.MEMBER, status=MemberStatus.ACTIVE))
    db_session.commit()
    r = client.get(f"/api/v1/workspaces/{ws}", headers=issuer.headers("carol"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_removed_manager_loses_access(client, issuer, db_session):
    ws = make_workspace(client, issuer, "bob")
    member = db_session.query(Member).filter_by(user_id="bob").one()
    member.status = MemberStatus.REMOVED
    db_session.commit()
    assert client.get(f"/api/v1/workspaces/{ws}", headers=issuer.headers("bob")).status_code == 404


def test_patch_workspace(client, issuer):
    ws = make_workspace(client, issuer, "alice")
    h = issuer.headers("alice")
    r = client.patch(f"/api/v1/workspaces/{ws}", json={"reminderLeadHours": 6, "name": " Acme Ltd "}, headers=h)
    assert r.status_code == 200
    assert r.json()["reminderLeadHours"] == 6 and r.json()["name"] == "Acme Ltd"
    assert client.patch(f"/api/v1/workspaces/{ws}", json={"timezone": "Mars/Olympus"}, headers=h).json()["error"]["code"] == "invalid_timezone"
    assert client.patch(f"/api/v1/workspaces/{ws}", json={"name": None}, headers=h).status_code == 422


def test_cors_allows_only_configured_origins(client):
    ok = client.options("/api/v1/me", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    bad = client.options("/api/v1/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


# --- sockets -------------------------------------------------------------------

@pytest.fixture
def live_server(db_session, issuer, monkeypatch):
    """The real ASGI app on a free port, so Socket.IO runs end to end."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # Long-polling requests otherwise hang for the default 25 s ping interval.
    monkeypatch.setattr(sio.eio, "ping_interval", 1)
    monkeypatch.setattr(sio.eio, "ping_timeout", 1)
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_until_complete, args=(server.serve(),), daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline, "server didn't start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", loop
    server.should_exit = server.force_exit = True  # don't wait on lingering polling connections
    thread.join(timeout=10)
    if not loop.is_running():
        loop.close()


def connect(url, token):
    received = []
    c = socketio.Client(reconnection=False)
    c.on("*", lambda event, data: received.append((event, data)), namespace="/v1")
    c.connect(url, namespaces=["/v1"], transports=["polling"], auth={"token": token}, wait_timeout=5)
    return c, received


def test_socket_requires_valid_token(live_server, issuer):
    url, _ = live_server
    for auth in (None, {"token": "garbage"}, {"token": issuer.token("alice", expires_in=-120)}):
        c = socketio.Client(reconnection=False)
        with pytest.raises(socketio.exceptions.ConnectionError):
            c.connect(url, namespaces=["/v1"], transports=["polling"], auth=auth, wait_timeout=3)
        c.disconnect()


def test_socket_only_receives_own_workspace_events(live_server, issuer):
    url, loop = live_server
    with TestClient(create_app()) as rest:
        ws_a = make_workspace(rest, issuer, "alice", "A")
        ws_b = make_workspace(rest, issuer, "bob", "B")
    alice, alice_got = connect(url, issuer.token("alice"))
    bob, bob_got = connect(url, issuer.token("bob"))
    try:
        for ws in (ws_a, ws_b):
            asyncio.run_coroutine_threadsafe(
                emit_workspace_event(uuid.UUID(ws), "workspace.updated", workspace={"id": ws}), loop
            ).result(timeout=5)
        deadline = time.time() + 5
        while (not alice_got or not bob_got) and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)  # give any leaked event time to arrive
        assert [d["workspaceId"] for _, d in alice_got] == [ws_a]
        assert [d["workspaceId"] for _, d in bob_got] == [ws_b]
        assert alice_got[0][0] == "workspace.updated"
        assert alice_got[0][1]["actor"] == {"source": "dashboard", "memberId": None}
    finally:
        alice.disconnect()
        bob.disconnect()



def test_onboarding_dismiss_and_skip(client, issuer):
    ws = make_workspace(client, issuer, "alice")
    h = issuer.headers("alice")
    r = client.patch(
        f"/api/v1/workspaces/{ws}",
        json={"onboarding": {"dismissed": True, "skippedSteps": ["invite_team", "invite_team"]}},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["onboarding"]["skippedSteps"] == ["invite_team"]
    assert r.json()["onboarding"]["dismissedAt"].endswith("Z")
    me = client.get("/api/v1/me", headers=h).json()["workspaces"][0]["onboarding"]
    assert me["skippedSteps"] == ["invite_team"] and me["dismissedAt"] is not None
    r = client.patch(f"/api/v1/workspaces/{ws}", json={"onboarding": {"dismissed": False}}, headers=h)
    assert r.json()["onboarding"]["dismissedAt"] is None
    bad = client.patch(f"/api/v1/workspaces/{ws}", json={"onboarding": {"skippedSteps": ["nope"]}}, headers=h)
    assert bad.status_code == 422


def test_manager_telegram_link(client, issuer, db_session):
    from app.models import Invite, WorkspaceBot

    ws = make_workspace(client, issuer, "alice")
    h = issuer.headers("alice")
    url = f"/api/v1/workspaces/{ws}/members/me/telegram-link"
    assert client.post(url, headers=h).json()["error"]["code"] == "bot_not_connected"

    db_session.add(WorkspaceBot(
        workspace_id=uuid.UUID(ws), telegram_bot_id=42, username="acme_nest_bot",
        token_encrypted="x", webhook_secret="y",
    ))
    db_session.commit()
    r = client.post(url, headers=h)
    assert r.status_code == 201, r.text
    token = r.json()["url"].removeprefix("https://t.me/acme_nest_bot?start=")
    invite = db_session.query(Invite).filter_by(token=token).one()
    manager = db_session.query(Member).filter_by(user_id="alice").one()
    assert invite.member_id == manager.id and invite.max_uses == 1

    from app.models import TelegramUser

    db_session.add(TelegramUser(telegram_user_id=777))
    db_session.flush()
    manager.telegram_user_id = 777  # as if the link was redeemed
    db_session.commit()
    assert client.post(url, headers=h).json()["error"]["code"] == "telegram_already_linked"
    # Another workspace's manager can't mint links here.
    assert client.post(url, headers=issuer.headers("bob")).status_code == 404
