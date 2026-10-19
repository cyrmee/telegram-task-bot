"""Telegram DM delivery (Stream B, B4): a transactional outbox plus a retrying worker.

Producers call outbox.enqueue() in the same transaction as their change. The
OutboxWorker sends due rows through a SendFn. In production that's
telegram.telegram_sender(BotManager.get_bot), and tests use a fake.
"""
from .errors import (
    BotUnavailable,
    PermanentSendError,
    RateLimited,
    RecipientBlocked,
    SendError,
    TransientSendError,
)
from .messages import Button, OutgoingMessage, SendFn, message_payload
from .outbox import assigned_key, cancel_queued, enqueue, reminder_key, wake_member, wake_workspace
from .worker import OutboxWorker, RetryPolicy, Sent

__all__ = [
    "BotUnavailable",
    "Button",
    "OutboxWorker",
    "OutgoingMessage",
    "PermanentSendError",
    "RateLimited",
    "RecipientBlocked",
    "RetryPolicy",
    "SendError",
    "SendFn",
    "Sent",
    "TransientSendError",
    "assigned_key",
    "cancel_queued",
    "enqueue",
    "message_payload",
    "reminder_key",
    "wake_member",
    "wake_workspace",
]
