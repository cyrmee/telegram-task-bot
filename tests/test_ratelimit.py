"""D4: per-IP rate limits return 429 with Retry-After, per rule and window."""
import asyncio

from fastapi.testclient import TestClient

from app.ratelimit import RateLimiter, Rule


async def ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_limits_per_rule_and_resets_next_window():
    clock = Clock()
    limiter = RateLimiter(ok_app, rules=(Rule("api", "/api/", limit=3),), clock=clock)
    client = TestClient(limiter)
    assert [client.get("/api/x").status_code for _ in range(3)] == [200, 200, 200]
    r = client.get("/api/x")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(r.headers["retry-after"]) <= 60
    assert client.get("/other").status_code == 200  # no rule, no limit
    clock.t += 60
    assert client.get("/api/x").status_code == 200


def test_workspace_creation_has_its_own_tight_limit():
    limiter = RateLimiter(ok_app, clock=Clock())
    client = TestClient(limiter)
    codes = [client.post("/api/v1/workspaces").status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    assert client.get("/api/v1/workspaces/abc").status_code == 200  # reads use the general API rule


def test_clients_are_counted_separately():
    limiter = RateLimiter(ok_app, rules=(Rule("api", "/api/", limit=1),), clock=Clock())

    async def call(ip):
        sent = []

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "path": "/api/x", "method": "GET", "client": (ip, 1)}
        await limiter(scope, None, send)
        return sent[0]["status"]

    async def run():
        return [await call("1.1.1.1"), await call("2.2.2.2"), await call("1.1.1.1")]

    assert asyncio.run(run()) == [200, 200, 429]


def test_app_factory_wraps_with_limiter(db_session, monkeypatch):
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    get_settings.cache_clear()
    try:
        app = create_app()
        assert isinstance(app, RateLimiter) and app.enabled
        with TestClient(app) as client:
            codes = [client.post("/api/v1/workspaces", json={}).status_code for _ in range(11)]
        assert codes[-1] == 429 and 401 in codes  # limited even before auth
    finally:
        get_settings.cache_clear()
