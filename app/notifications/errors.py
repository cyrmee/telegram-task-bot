"""Send outcomes the worker acts on. A sender translates its transport's errors
(python-telegram-bot, see telegram.py) into these, so the worker never imports PTB."""


class SendError(Exception):
    """Base class. Unknown exceptions from a sender are treated as TransientSendError."""


class TransientSendError(SendError):
    """Network failure, timeout or 5xx. Retried with exponential backoff."""


class RateLimited(SendError):
    """Telegram 429. Retried after `retry_after` seconds without using up an attempt."""

    def __init__(self, retry_after: float, message: str = ""):
        super().__init__(message or f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


class BotUnavailable(SendError):
    """The workspace has no live bot (none connected, disconnected, or token revoked).
    The row waits without using up attempts; wake_workspace() releases it on reconnect."""


class RecipientBlocked(SendError):
    """Telegram 403: the member blocked the bot or never started it. The member is
    marked unreachable and the row fails."""


class PermanentSendError(SendError):
    """Telegram 400 and other errors a retry can't fix. The row fails immediately."""
