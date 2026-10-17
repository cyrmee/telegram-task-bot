import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at_col, str_enum, updated_at_col, uuid_pk
from .enums import NotificationStatus, NotificationType


class Notification(Base):
    """Transactional outbox for Telegram DMs. Rows are inserted in the same
    transaction as the change that caused them and drained by Stream B's
    delivery worker (SELECT ... FOR UPDATE SKIP LOCKED)."""

    __tablename__ = "notifications"
    __table_args__ = (
        # The worker's claim query: due rows that are still queued.
        Index(
            "ix_notifications_due",
            "next_attempt_at",
            postgresql_where=text("status = 'queued'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL keeps the history (and telegram_message_id) after a task is deleted.
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[NotificationType] = mapped_column(str_enum(NotificationType, "notification_type"), nullable=False)
    # e.g. "assigned:{task_id}:{member_id}", "reminder:{task_id}:{offset}". Unique => no duplicate sends.
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[NotificationStatus] = mapped_column(
        str_enum(NotificationStatus, "notification_status"),
        nullable=False,
        server_default=NotificationStatus.QUEUED.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    telegram_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
