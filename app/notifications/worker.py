"""Outbox delivery worker.

Each round claims a batch of due rows with SELECT ... FOR UPDATE SKIP LOCKED and
leases them: `next_attempt_at` moves forward by the lease and `attempts` goes up,
in the same short transaction. Then the worker sends without holding locks and
records each outcome in its own transaction. Several workers can run at once. A
row whose worker dies mid-send becomes due again when its lease expires.

Delivery is exactly once except in one case Telegram's API can't disambiguate:
a request that reached Telegram whose response was lost (read timeout). That
row is retried and the member may get the DM twice.
"""
import asyncio
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.models import Member, MemberStatus, Notification, NotificationStatus, NotificationType

from app.observability import alert, workspace_context

from .errors import BotUnavailable, PermanentSendError, RateLimited, RecipientBlocked
from .messages import SendFn, message_from_payload

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 12
    base_delay: float = 10.0  # seconds before the 2nd attempt, doubling after that
    max_delay: float = 900.0
    jitter: float = 0.2  # +/- fraction of the delay
    # Recheck interval for rows that can't be sent yet (no bot, or the member has
    # no chat). These don't use up attempts.
    defer_delay: float = 300.0

    def backoff(self, attempts: int, rand: Callable[[], float] = random.random) -> timedelta:
        delay = min(self.max_delay, self.base_delay * 2 ** max(0, attempts - 1))
        delay *= 1 + self.jitter * (2 * rand() - 1)
        return timedelta(seconds=delay)


_BLOCKED = "blocked"


@dataclass(frozen=True)
class _Claimed:
    id: uuid.UUID
    workspace_id: uuid.UUID
    member_id: uuid.UUID
    type: NotificationType
    idempotency_key: str
    attempts: int  # including the current one
    payload: dict
    chat_id: Optional[int]
    member_status: MemberStatus


@dataclass(frozen=True)
class Sent:
    """Passed to the worker's on_sent hook after a DM went out."""

    notification_id: uuid.UUID
    workspace_id: uuid.UUID
    member_id: uuid.UUID
    type: NotificationType
    idempotency_key: str
    chat_id: int
    message_id: int


class OutboxWorker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        send: SendFn,
        *,
        policy: RetryPolicy = RetryPolicy(),
        batch_size: int = 20,
        send_timeout: float = 20.0,
        poll_interval: float = 1.0,
        clock: Callable[[], datetime] = utcnow,
        on_sent: Optional[Callable[[Sent], Awaitable[None]]] = None,
    ):
        self._session_factory = session_factory
        self._send = send
        self.policy = policy
        self.batch_size = batch_size
        self.send_timeout = send_timeout
        self.poll_interval = poll_interval
        self._clock = clock
        # Best-effort follow-up after a send, e.g. retiring older task DMs (app/bot/tasks.py).
        self._on_sent = on_sent
        # Long enough that a full batch of timed-out sends finishes inside the lease.
        self.lease = timedelta(seconds=batch_size * send_timeout + 60)
        self.last_round_at: Optional[datetime] = None  # heartbeat for /health

    async def run_forever(self, stop: asyncio.Event) -> None:
        idle = self.poll_interval
        while not stop.is_set():
            try:
                processed = await self.run_once()
                idle = self.poll_interval
            except Exception:
                logger.exception("Outbox round failed")
                processed = 0
                idle = min(idle * 2, 60.0)  # e.g. database down
            if processed < self.batch_size:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=idle)
                except asyncio.TimeoutError:
                    pass

    async def run_once(self) -> int:
        """Claim one batch and attempt each row. Returns how many rows were claimed."""
        claimed = await asyncio.to_thread(self._claim, self._clock())
        self.last_round_at = utcnow()
        # After a 429 or a missing bot, the rest of that workspace's rows wait without sending.
        held: dict[uuid.UUID, tuple[datetime, str]] = {}
        # Members who blocked the bot during this batch (their status was read at claim time).
        blocked: set[uuid.UUID] = set()
        for row in claimed:
            if row.workspace_id in held:
                until, reason = held[row.workspace_id]
                await asyncio.to_thread(self._defer, row, until, reason)
                continue
            if row.member_id in blocked:
                await asyncio.to_thread(self._finish, row, NotificationStatus.FAILED, "member unreachable")
                continue
            with workspace_context(row.workspace_id):
                outcome = await self._attempt(row)
            if isinstance(outcome, tuple):
                held[row.workspace_id] = outcome
            elif outcome == _BLOCKED:
                blocked.add(row.member_id)
        return len(claimed)

    async def _attempt(self, row: _Claimed) -> Optional[tuple[datetime, str] | str]:
        """Returns (until, reason) to hold the workspace, _BLOCKED, or None."""
        if row.member_status == MemberStatus.REMOVED:
            await asyncio.to_thread(self._finish, row, NotificationStatus.CANCELLED, "member removed")
            return None
        if row.member_status == MemberStatus.UNREACHABLE:
            await asyncio.to_thread(self._finish, row, NotificationStatus.FAILED, "member unreachable")
            return None
        if row.chat_id is None:
            until = self._clock() + timedelta(seconds=self.policy.defer_delay)
            await asyncio.to_thread(self._defer, row, until, "member has no Telegram chat yet")
            return None

        message = message_from_payload(row.workspace_id, row.chat_id, row.payload)
        try:
            message_id = await asyncio.wait_for(self._send(message), timeout=self.send_timeout)
        except RateLimited as e:
            until = self._clock() + timedelta(seconds=e.retry_after)
            await asyncio.to_thread(self._defer, row, until, str(e))
            return until, str(e)
        except BotUnavailable as e:
            until = self._clock() + timedelta(seconds=self.policy.defer_delay)
            reason = str(e) or "no live bot for workspace"
            await asyncio.to_thread(self._defer, row, until, reason)
            return until, reason
        except RecipientBlocked as e:
            await asyncio.to_thread(self._blocked, row, str(e) or "blocked by recipient")
            return _BLOCKED
        except PermanentSendError as e:
            await asyncio.to_thread(self._finish, row, NotificationStatus.FAILED, str(e))
            return None
        except Exception as e:  # TransientSendError, timeouts, anything unexpected
            reason = f"{type(e).__name__}: {e}"
            if row.attempts >= self.policy.max_attempts:
                logger.warning("Notification %s failed after %s attempts: %s", row.id, row.attempts, reason)
                await asyncio.to_thread(self._finish, row, NotificationStatus.FAILED, reason)
            else:
                until = self._clock() + self.policy.backoff(row.attempts)
                await asyncio.to_thread(self._retry, row, until, reason)
            return None

        await asyncio.to_thread(self._sent, row, message_id)
        if self._on_sent is not None:
            sent = Sent(row.id, row.workspace_id, row.member_id, row.type, row.idempotency_key, row.chat_id, message_id)
            try:
                await self._on_sent(sent)
            except Exception:
                logger.exception("on_sent hook failed for notification %s", row.id)
        return None

    # --- DB steps (sync, run in a thread) ---

    def _claim(self, now: datetime) -> list[_Claimed]:
        with self._session_factory() as session, session.begin():
            rows = session.execute(
                select(
                    Notification.id,
                    Notification.workspace_id,
                    Notification.member_id,
                    Notification.type,
                    Notification.idempotency_key,
                    Notification.attempts,
                    Notification.payload,
                    Member.private_chat_id,
                    Member.status,
                )
                .join(Member, Member.id == Notification.member_id)
                .where(Notification.status == NotificationStatus.QUEUED, Notification.next_attempt_at <= now)
                .order_by(Notification.next_attempt_at, Notification.created_at)
                .limit(self.batch_size)
                .with_for_update(skip_locked=True, of=Notification)
            ).all()
            if not rows:
                return []
            session.execute(
                update(Notification)
                .where(Notification.id.in_([r.id for r in rows]))
                .values(attempts=Notification.attempts + 1, next_attempt_at=now + self.lease)
            )
        return [
            _Claimed(
                r.id, r.workspace_id, r.member_id, r.type, r.idempotency_key,
                r.attempts + 1, r.payload, r.private_chat_id, r.status,
            )
            for r in rows
        ]

    def _sent(self, row: _Claimed, message_id: int) -> None:
        # Recorded even if the row was cancelled meanwhile: the DM did go out.
        with self._session_factory() as session, session.begin():
            session.execute(
                update(Notification)
                .where(Notification.id == row.id)
                .values(
                    status=NotificationStatus.SENT,
                    sent_at=self._clock(),
                    telegram_chat_id=row.chat_id,
                    telegram_message_id=message_id,
                    last_error=None,
                )
            )

    def _retry(self, row: _Claimed, until: datetime, reason: str) -> None:
        self._update_queued(row, next_attempt_at=until, last_error=reason)

    def _defer(self, row: _Claimed, until: datetime, reason: str) -> None:
        # Not a real attempt: give the claimed attempt back.
        self._update_queued(row, next_attempt_at=until, last_error=reason, attempts=Notification.attempts - 1)

    def _finish(self, row: _Claimed, status: NotificationStatus, reason: str) -> None:
        self._update_queued(row, status=status, last_error=reason)
        if status == NotificationStatus.FAILED:
            alert(
                "notification_failed", f"Notification {row.id} failed: {reason}",
                workspace_id=str(row.workspace_id), notification_id=str(row.id), notification_type=row.type.value,
            )

    def _blocked(self, row: _Claimed, reason: str) -> None:
        with self._session_factory() as session, session.begin():
            self._update_queued(row, session=session, status=NotificationStatus.FAILED, last_error=reason)
            session.execute(
                update(Member)
                .where(Member.id == row.member_id, Member.status == MemberStatus.ACTIVE)
                .values(status=MemberStatus.UNREACHABLE)
            )

    def _update_queued(self, row: _Claimed, session: Optional[Session] = None, **values) -> None:
        # Guarded on QUEUED so a concurrent cancel isn't overwritten.
        stmt = (
            update(Notification)
            .where(Notification.id == row.id, Notification.status == NotificationStatus.QUEUED)
            .values(**values)
        )
        if session is not None:
            session.execute(stmt)
            return
        with self._session_factory() as session, session.begin():
            session.execute(stmt)
