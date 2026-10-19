"""Structured (JSON-lines) logging with secret redaction.

Telegram bot tokens end up in log lines more often than you'd think: httpx
logs every request URL (https://api.telegram.org/bot<token>/getMe) at INFO,
and exception messages can carry them too. The filter masks them on every
record, whatever logger produced it.
"""
import json
import logging
import re
import sys
from datetime import datetime, timezone

# <bot id>:<35-char secret>, as issued by BotFather.
BOT_TOKEN_RE = re.compile(r"\d{5,16}:[A-Za-z0-9_-]{30,}")
REDACTED = "[REDACTED_BOT_TOKEN]"

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


def redact(text: str) -> str:
    return BOT_TOKEN_RE.sub(REDACTED, text)


class RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Anything passed as logger.info(..., extra={...}), e.g. workspace_id, request_id.
        entry.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS and not k.startswith("_")})
        if record.exc_text:
            entry["exc"] = record.exc_text
        return redact(json.dumps(entry, default=str))


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactSecretsFilter())
    handler.setFormatter(JsonFormatter() if json_logs else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx's per-request INFO lines are noise (and the main token leak).
    logging.getLogger("httpx").setLevel(logging.WARNING)
