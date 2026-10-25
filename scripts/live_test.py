"""Guided live test of Stream B (B1–B6) with two real dev bots and a phone.

    make live-test            # full run (~15 min, needs a person with Telegram)
    make live-test ARGS=--check   # preflight only: tokens, ngrok, docker

Everything server-side is automated: a throwaway Postgres container, the API
(uvicorn on the host, from a temp dir so no repo .env is read), the host's
ngrok tunnel, two workspaces with one dev bot each, invites, tasks, a backend
outage across a reminder. Steps that need a human print a short line starting
with "📱" and then poll until the result shows up in the API or database.

Tokens: ~/.config/nest/dev-bots.env with DEV_BOT_1_TOKEN and DEV_BOT_2_TOKEN.
The production bot (@gezat_tasks_bot) is refused. On exit, both dev bots'
webhooks are deleted and everything the script started is stopped.
"""
import argparse
import base64
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
TOKENS_FILE = Path.home() / ".config/nest/dev-bots.env"
FORBIDDEN_BOTS = {"gezat_tasks_bot"}
PG_CONTAINER = "nest_live_pg"
PG_PORT = 5439
API_PORT = int(os.environ.get("LIVE_API_PORT", "8010"))
JWKS_PORT = int(os.environ.get("LIVE_JWKS_PORT", "8019"))
ISSUER = "nest-live-test"
DB_URL = f"postgresql://nest:nest@127.0.0.1:{PG_PORT}/nest_live"

results: list[tuple[str, bool, str]] = []
procs: dict[str, subprocess.Popen] = {}


# --- output ---


def say(msg: str) -> None:
    print(msg, flush=True)


def phone(msg: str) -> None:
    say(f"📱 {msg}")


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    say(f"[{'PASS' if ok else 'FAIL'}] {name}{' — ' + detail if detail else ''}")
    return ok


def wait_for(what: str, fn: Callable[[], object], timeout: float, every: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = fn()
        except Exception:
            value = None
        if value:
            return value
        time.sleep(every)
    say(f"   …gave up waiting for {what} after {int(timeout)}s")
    return None


# --- tokens and Telegram ---


def load_tokens() -> tuple[str, str]:
    if not TOKENS_FILE.exists():
        sys.exit(f"Missing {TOKENS_FILE} (DEV_BOT_1_TOKEN=..., DEV_BOT_2_TOKEN=...)")
    env = {}
    for line in TOKENS_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'\"")
    try:
        return env["DEV_BOT_1_TOKEN"], env["DEV_BOT_2_TOKEN"]
    except KeyError as e:
        sys.exit(f"{TOKENS_FILE} needs {e.args[0]}")


def tg(token: str, method: str, **params) -> dict:
    import httpx

    r = httpx.post(f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=15)
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"{method}: {body.get('description')}")
    return body["result"]


def preflight(token1: str, token2: str) -> tuple[dict, dict]:
    me1, me2 = tg(token1, "getMe"), tg(token2, "getMe")
    for me in (me1, me2):
        if me["username"].lower() in FORBIDDEN_BOTS:
            sys.exit(f"@{me['username']} is the production bot. Use personal dev bots only.")
    if me1["id"] == me2["id"]:
        sys.exit("DEV_BOT_1_TOKEN and DEV_BOT_2_TOKEN are the same bot")
    check("preflight: two distinct dev bots", True, f"@{me1['username']}, @{me2['username']}")
    for name in ("docker", "ngrok"):
        if subprocess.run(["which", name], capture_output=True).returncode != 0:
            sys.exit(f"{name} is not installed")
    return me1, me2


# --- infrastructure ---


class Issuer:
    """Stands in for the dashboard: an Ed25519 JWKS served locally, JWTs minted per user."""

    def __init__(self):
        import jwt
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        self._jwt = jwt
        self.key = Ed25519PrivateKey.generate()
        public = json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(self.key.public_key()))
        jwks = json.dumps({"keys": [{**public, "kid": "live", "alg": "EdDSA", "use": "sig"}]}).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(jwks)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", JWKS_PORT), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def headers(self, sub: str) -> dict:
        now = int(time.time())
        claims = {"sub": sub, "iss": ISSUER, "aud": "nest-api", "iat": now, "exp": now + 3600,
                  "email": f"{sub}@live.test", "name": sub.title()}
        return {"Authorization": "Bearer " + self._jwt.encode(claims, self.key, algorithm="EdDSA", headers={"kid": "live"})}


def start_postgres() -> None:
    subprocess.run(["docker", "rm", "-f", PG_CONTAINER], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", PG_CONTAINER, "-e", "POSTGRES_USER=nest", "-e", "POSTGRES_PASSWORD=nest",
         "-e", "POSTGRES_DB=nest_live", "-p", f"127.0.0.1:{PG_PORT}:5432", "postgres:16-alpine"],
        check=True, capture_output=True,
    )
    ok = wait_for("postgres", lambda: subprocess.run(
        ["docker", "exec", PG_CONTAINER, "pg_isready", "-U", "nest", "-d", "nest_live"], capture_output=True).returncode == 0, 60, 1)
    if not ok:
        sys.exit("Postgres didn't start")
    time.sleep(1)


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", DB_URL + "?sslmode=disable")
    command.upgrade(cfg, "head")


def start_ngrok() -> str:
    proc = subprocess.Popen(["ngrok", "http", str(API_PORT), "--log", "stdout", "--log-format", "json"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    procs["ngrok"] = proc
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        m = re.search(r'"url":"(https://[^"]+)"', line)
        if m:
            threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()  # keep draining
            return m.group(1)
        if '"lvl":"eror"' in line or "ERR_NGROK" in line:
            sys.exit(f"ngrok failed (another tunnel already running on this account?): {line.strip()[:300]}")
    sys.exit("ngrok didn't report a tunnel URL")


def api_env(public_url: str, key: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TELEGRAM_", "DATABASE_", "PUBLIC_BASE", "AUTH_", "TOKEN_"))}
    env.update(
        PYTHONPATH=str(ROOT),
        DATABASE_URL=DB_URL + "?sslmode=disable",
        PUBLIC_BASE_URL=public_url,
        TOKEN_ENCRYPTION_KEY=key,
        AUTH_JWKS_URL=f"http://127.0.0.1:{JWKS_PORT}/jwks",
        AUTH_JWT_ISSUER=ISSUER,
        AUTH_JWT_AUDIENCE="nest-api",
        CORS_ORIGINS="http://localhost:3000",
        BOT_RUNTIME_ENABLED="true",
        LEGACY_BOT_ENABLED="false",
        LOG_JSON="true",
    )
    return env


def start_api(env: dict, log: Path) -> None:
    out = log.open("a")
    procs["api"] = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(API_PORT)],
        env=env, cwd=tempfile.gettempdir(), stdout=out, stderr=subprocess.STDOUT,
    )
    if not wait_for("API /health", lambda: api("GET", "/health").status_code == 200, 60, 1):
        sys.exit(f"API didn't come up; see {log}")


def stop_api() -> None:
    proc = procs.pop("api", None)
    if proc and proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()


def api(method: str, path: str, user: Optional[str] = None, issuer: Optional[Issuer] = None, **kw):
    import httpx

    headers = issuer.headers(user) if issuer and user else {}
    return httpx.request(method, f"http://127.0.0.1:{API_PORT}{path}", headers=headers, timeout=30, **kw)


def db_rows(sql: str, **params) -> list:
    from sqlalchemy import create_engine, text

    engine = create_engine(DB_URL.replace("postgresql://", "postgresql+psycopg2://"))
    try:
        with engine.connect() as conn:
            return list(conn.execute(text(sql), params))
    finally:
        engine.dispose()


def sent(key_prefix: str) -> list:
    return db_rows("SELECT idempotency_key, sent_at FROM notifications WHERE status = 'sent' AND idempotency_key LIKE :p",
                   p=key_prefix + "%")


# --- the run ---


def run(token1: str, token2: str, me1: dict, me2: dict, phone_timeout: float, log: Path) -> None:
    issuer = Issuer()
    say("Starting Postgres, migrations, ngrok and the API…")
    start_postgres()
    migrate()
    public_url = start_ngrok()
    env = api_env(public_url, base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    start_api(env, log)
    say(f"   API is up behind {public_url}")
    bot1, bot2 = f"@{me1['username']}", f"@{me2['username']}"

    # B1: one bot per workspace
    ws = {}
    for owner, token in (("zak-a", token1), ("zak-b", token2)):
        r = api("POST", "/api/v1/workspaces", owner, issuer, json={"name": f"Live test {owner[-1].upper()}", "timezone": "Africa/Addis_Ababa"})
        ws[owner] = r.json()["id"]
        r = api("PUT", f"/api/v1/workspaces/{ws[owner]}/bot", owner, issuer, json={"token": token})
        check(f"B1 connect bot to workspace {owner[-1].upper()}", r.status_code == 200 and r.json()["status"] == "active", f"{r.status_code}")
    for owner, me in (("zak-a", me1), ("zak-b", me2)):
        body = api("GET", f"/api/v1/workspaces/{ws[owner]}/bot", owner, issuer).json()
        check(f"B1 Telegram reports @{me['username']}'s webhook pointing here", bool(body.get("webhook") and body["webhook"]["urlOk"]))
    r = api("PUT", f"/api/v1/workspaces/{ws['zak-b']}/bot", "zak-b", issuer, json={"token": token1})
    check("B1 the same bot can't join a second workspace", r.status_code == 409)

    # B2: joining through invite links
    member = {}
    for owner, bot in (("zak-a", bot1), ("zak-b", bot2)):
        url = api("POST", f"/api/v1/workspaces/{ws[owner]}/invites", owner, issuer, json={}).json()["url"]
        phone(f"Open {url} and tap Start ({bot})")

        def joined(owner=owner):
            items = api("GET", f"/api/v1/workspaces/{ws[owner]}/members", owner, issuer).json()["items"]
            return next((m for m in items if m.get("telegram") and m["status"] == "active"), None)

        m = wait_for(f"you to join via {bot}", joined, phone_timeout)
        if not check(f"B2 joined workspace {owner[-1].upper()} via {bot}", m is not None):
            raise SystemExit("Can't continue without a joined member")
        member[owner] = m["id"]

    # B3/B4: assignment DMs, each through its own bot
    def new_task(owner, title, due_in, **extra):
        project = api("POST", f"/api/v1/workspaces/{ws[owner]}/projects", owner, issuer, json={"name": "Live test"}).json()["id"]
        body = {"title": title, "projectId": project, "assigneeId": member[owner],
                "dueAt": (datetime.now(timezone.utc) + due_in).isoformat(), **extra}
        return api("POST", f"/api/v1/workspaces/{ws[owner]}/tasks", owner, issuer, json=body).json()

    task_a = new_task("zak-a", "🧪 Live A: water the plants", timedelta(days=1))
    task_b = new_task("zak-b", "🧪 Live B: call the printer", timedelta(days=1))
    for t, bot in ((task_a, bot1), (task_b, bot2)):
        ok = wait_for("assignment DM", lambda t=t: sent(f"assigned:{t['id']}:"), 60)
        check(f"B4 assignment DM for '{t['title']}' delivered by {bot}", bool(ok))
    phone(f"You got one task from each bot. Send /mytasks to {bot1}: it should list only the 'Live A' task.")

    # B3: one-tap complete
    phone(f"In {bot1}, tap ✅ Mark complete on 'Live A: water the plants'")

    def completed(owner, t):
        body = api("GET", f"/api/v1/workspaces/{ws[owner]}/tasks/{t['id']}", owner, issuer).json()
        return body if body["status"] == "completed" else None

    done = wait_for("the Complete tap", lambda: completed("zak-a", task_a), phone_timeout)
    check("B3 Complete button completed the task", bool(done) and done.get("completedVia") == "telegram",
          f"completedVia={done.get('completedVia') if done else None}")
    b_body = api("GET", f"/api/v1/workspaces/{ws['zak-b']}/tasks/{task_b['id']}", "zak-b", issuer).json()
    check("B1 workspace B's task is untouched", b_body["status"] == "pending")

    # B6: /done fallback
    phone(f"Send /done {task_b['number']} to {bot2}")
    done = wait_for("/done", lambda: completed("zak-b", task_b), phone_timeout)
    check("B6 /done completed the task", bool(done) and done.get("completedVia") == "telegram")

    # B5: reminder that comes due while the backend is down, then overdue
    task_r = new_task("zak-a", "🧪 Live A2: reminder test", timedelta(minutes=4), reminderOffsetsMinutes=[2])
    wait_for("its assignment DM", lambda: sent(f"assigned:{task_r['id']}:"), 60)
    due_at = datetime.fromisoformat(task_r["dueAt"].replace("Z", "+00:00"))
    reminder_at = due_at - timedelta(minutes=2)
    say("   Stopping the API across the reminder time (simulated outage)…")
    stop_api()
    while datetime.now(timezone.utc) < reminder_at + timedelta(seconds=30):
        time.sleep(5)
    restarted = datetime.now(timezone.utc)
    start_api(env, log)
    say("   API restarted.")
    body = api("GET", f"/api/v1/workspaces/{ws['zak-a']}/bot", "zak-a", issuer).json()
    check("B1 webhook still healthy after restart", bool(body.get("webhook") and body["webhook"]["urlOk"]))
    rows = wait_for("the catch-up reminder", lambda: sent(f"reminder:{task_r['id']}:"), 150)
    check("B5 reminder missed during the outage was sent after restart", bool(rows) and rows[0].sent_at >= restarted)
    rows = wait_for("the overdue reminder", lambda: sent(f"overdue:{task_r['id']}:"), 240)
    check("B5 overdue reminder sent after the due time", bool(rows))
    check("B5 no duplicate reminders", len(sent(f"reminder:{task_r['id']}:")) == 1)
    phone(f"{bot1} should have sent ⏰ Reminder and then ⚠️ overdue for 'Live A2'. Tap ✅ on it to finish.")
    wait_for("the A2 Complete tap", lambda: completed("zak-a", task_r), phone_timeout)

    # B3: deleting a task retires its DM
    task_d = new_task("zak-b", "🧪 Live B2: this one gets cancelled", timedelta(days=2))
    wait_for("its assignment DM", lambda: sent(f"assigned:{task_d['id']}:"), 60)
    api("DELETE", f"/api/v1/workspaces/{ws['zak-b']}/tasks/{task_d['id']}", "zak-b", issuer)
    rows = wait_for("the cancellation DM", lambda: sent(f"deleted:{task_d['id']}:"), 60)
    check("B3 cancellation DM sent when a task is deleted", bool(rows))
    phone(f"In {bot2}, the 'Live B2' message should now say it was cancelled, without buttons.")

    for owner in ws:
        r = api("DELETE", f"/api/v1/workspaces/{ws[owner]}/bot", owner, issuer)
        check(f"B1 disconnect workspace {owner[-1].upper()}'s bot", r.status_code == 204)


def cleanup(tokens: tuple[str, ...]) -> None:
    say("Cleaning up…")
    for token in tokens:
        try:
            tg(token, "deleteWebhook")
        except Exception as e:
            say(f"   deleteWebhook failed: {e}")
    stop_api()
    proc = procs.pop("ngrok", None)
    if proc and proc.poll() is None:
        proc.terminate()
    subprocess.run(["docker", "rm", "-f", PG_CONTAINER], capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="preflight only (tokens, docker, ngrok)")
    parser.add_argument("--phone-timeout", type=float, default=600, help="seconds to wait for each phone step")
    args = parser.parse_args()

    os.chdir(tempfile.mkdtemp(prefix="nest-live-"))  # never read a repo .env
    sys.path.insert(0, str(ROOT))
    token1, token2 = load_tokens()
    me1, me2 = preflight(token1, token2)
    if args.check:
        return 0
    log = Path.cwd() / "api.log"
    say(f"API log: {log}")
    try:
        run(token1, token2, me1, me2, args.phone_timeout, log)
    except SystemExit as e:
        if e.code not in (None, 0):
            check("run aborted", False, str(e.code))
    finally:
        cleanup((token1, token2))
    failed = [r for r in results if not r[1]]
    say(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    for name, _, detail in failed:
        say(f"  FAIL {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
