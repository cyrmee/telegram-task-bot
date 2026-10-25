"""In-process rate limiting (D4) as an outer ASGI wrapper, so it covers the
REST API, the Telegram webhooks and Socket.IO alike.

Fixed-window counters per (rule, client IP). Good enough for the single
API instance D2 runs; a multi-instance deploy would move this to Redis.
The client IP comes from scope["client"], which uvicorn fills from
X-Forwarded-For when started with --proxy-headers (render.yaml does).
"""
import json
import math
import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Rule:
    name: str
    prefix: str
    limit: int  # requests per window per client
    window: int = 60  # seconds


# Ordered: first matching prefix wins.
DEFAULT_RULES = (
    Rule("socket", "/socket.io", limit=60),  # handshakes + long-polls
    Rule("telegram", "/telegram/", limit=1200),  # Telegram's servers share few IPs
    Rule("api", "/api/", limit=300),
)
WORKSPACE_CREATE = Rule("workspace_create_post", "/api/v1/workspaces", limit=10)


class RateLimiter:
    def __init__(self, app, rules=DEFAULT_RULES, enabled: bool = True, clock=time.monotonic):
        self.app = app
        self.rules = rules
        self.enabled = enabled
        self.clock = clock
        self._counts: dict[tuple[str, str, int], int] = {}
        self._last_sweep = 0

    def __getattr__(self, name):
        # Transparent wrapper: app.other_asgi_app etc. still resolve to the wrapped app.
        return getattr(self.app, name)

    def _rule_for(self, scope) -> Optional[Rule]:
        path = scope.get("path", "")
        if scope.get("method") == "POST" and path.rstrip("/") == "/api/v1/workspaces":
            return WORKSPACE_CREATE  # creating workspaces is the one cheap-to-abuse write
        return next((r for r in self.rules if path.startswith(r.prefix)), None)

    def hit(self, rule: Rule, client: str) -> Optional[int]:
        """Count a request; return seconds to wait if over the limit, else None."""
        now = self.clock()
        bucket = int(now // rule.window)
        if bucket != self._last_sweep:  # drop old windows so memory stays bounded
            self._counts = {k: v for k, v in self._counts.items() if k[2] >= bucket}
            self._last_sweep = bucket
        key = (rule.name, client, bucket)
        self._counts[key] = self._counts.get(key, 0) + 1
        if self._counts[key] > rule.limit:
            return max(1, math.ceil((bucket + 1) * rule.window - now))
        return None

    async def __call__(self, scope, receive, send):
        if not self.enabled or scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        rule = self._rule_for(scope)
        client = (scope.get("client") or ("unknown", 0))[0]
        retry_after = self.hit(rule, client) if rule else None
        if retry_after is None:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps(
            {"error": {"code": "rate_limited", "message": "Too many requests. Try again shortly.", "requestId": None}}
        ).encode()
        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [(b"content-type", b"application/json"), (b"retry-after", str(retry_after).encode())],
        })
        await send({"type": "http.response.body", "body": body})
