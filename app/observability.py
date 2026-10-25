"""Observability (D3): request/workspace context on every log line, Sentry, and alerts.

- RequestContextMiddleware gives each HTTP request a request_id (the client's
  X-Request-ID, or a new one), echoes it back, and records the workspace from
  /api/v1/workspaces/{id}/... The bot and the outbox worker set the workspace
  with `workspace_context()`. ContextFilter copies both onto every log record,
  so the JSON logs carry them without passing extra= everywhere.
- init_sentry() is a no-op unless SENTRY_DSN is set. Events are tagged with the
  release and the same request/workspace ids, and scrubbed of bot tokens,
  auth headers and cookies before they leave the process.
- alert() logs an ERROR with an `alert` field. Sentry's logging integration
  turns ERROR logs into events, so alerts reach Sentry once a DSN is set, and
  a log-based alert rule can key on `alert` until then.
"""
import contextvars
import logging
import os
import re
import uuid
from contextlib import contextmanager
from typing import Any, Optional

from app.config import get_settings
from app.logging_config import redact

logger = logging.getLogger("nest.alerts")

request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)
workspace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("workspace_id", default=None)

WORKSPACE_PATH = re.compile(r"^/api/v1/workspaces/([0-9a-fA-F-]{36})(?:/|$)")
REQUEST_ID_OK = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


# --- context ---


class ContextFilter(logging.Filter):
    """Adds request_id / workspace_id from the current context to every record
    (an explicit extra={"workspace_id": ...} wins)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "request_id", None) is None and (rid := request_id_var.get()):
            record.request_id = rid
        if getattr(record, "workspace_id", None) is None and (ws := workspace_id_var.get()):
            record.workspace_id = ws
        return True


@contextmanager
def workspace_context(workspace_id: Any):
    token = workspace_id_var.set(str(workspace_id) if workspace_id else None)
    try:
        _sentry_tag("workspace_id", workspace_id_var.get())
        yield
    finally:
        workspace_id_var.reset(token)


class RequestContextMiddleware:
    """Pure ASGI, so it also covers Socket.IO and streaming responses."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if REQUEST_ID_OK.match(incoming) else uuid.uuid4().hex
        if rid != incoming:
            # Downstream (e.g. error bodies' requestId) reads the header.
            scope = {**scope, "headers": [(k, v) for k, v in scope["headers"] if k != b"x-request-id"] + [(b"x-request-id", rid.encode())]}
        match = WORKSPACE_PATH.match(scope.get("path", ""))
        rid_token = request_id_var.set(rid)
        ws_token = workspace_id_var.set(match.group(1).lower() if match else None)
        _sentry_tag("request_id", rid)
        if match:
            _sentry_tag("workspace_id", workspace_id_var.get())

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), (b"x-request-id", rid.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(rid_token)
            workspace_id_var.reset(ws_token)


def install_log_context() -> None:
    """Attach ContextFilter to the root handlers (after configure_logging)."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, ContextFilter) for f in handler.filters):
            # Before the redaction filter isn't required: ids carry no secrets.
            handler.addFilter(ContextFilter())


# --- alerts ---


def alert(name: str, message: str, **fields: Any) -> None:
    """An operational problem someone should look at (D3). Logged at ERROR with
    `alert=<name>`; reaches Sentry when it's configured."""
    logger.error(message, extra={"alert": name, **fields})


# --- Sentry ---

_SECRET_KEYS = {"authorization", "cookie", "cookies", "set-cookie", "x-telegram-bot-api-secret-token", "token", "token_encrypted", "webhook_secret"}


def release() -> Optional[str]:
    return get_settings().sentry_release or os.environ.get("RENDER_GIT_COMMIT") or None


def init_sentry() -> bool:
    settings = get_settings()
    if not settings.sentry_dsn:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        release=release(),
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
    )
    return True


def _scrub(value: Any, key: str = "") -> Any:
    if key.lower() in _SECRET_KEYS:
        return "[Filtered]"
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _scrub(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        # Header lists come as [name, value] pairs.
        if len(value) == 2 and isinstance(value[0], str) and value[0].lower() in _SECRET_KEYS:
            return [value[0], "[Filtered]"]
        return [_scrub(v) for v in value]
    return value


def scrub_event(event: dict, hint: Optional[dict] = None) -> dict:
    """Mask bot tokens anywhere in the event and drop auth headers, cookies and secrets."""
    return _scrub(event)


def scrub_breadcrumb(crumb: dict, hint: Optional[dict] = None) -> dict:
    return _scrub(crumb)


def _sentry_tag(key: str, value: Optional[str]) -> None:
    if value is None or not get_settings().sentry_dsn:
        return
    import sentry_sdk

    sentry_sdk.set_tag(key, value)
