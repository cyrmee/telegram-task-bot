import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at_col, str_enum, updated_at_col, uuid_pk
from .enums import BotStatus


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint("reminder_lead_hours BETWEEN 0 AND 720", name="reminder_lead_hours_range"),
        CheckConstraint("next_task_number > 0", name="next_task_number_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # better-auth "user".id. No FK: better-auth owns that table and it may not
    # exist when migrations run (e.g. a fresh CI database).
    owner_user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    reminder_lead_hours: Mapped[int] = mapped_column(Integer, nullable=False, server_default="24")
    # Counter for the short per-workspace task number used by `/done <n>`.
    next_task_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # Dashboard onboarding checklist (C3): dismissed, and steps the manager skipped (OnboardingStep values).
    onboarding_dismissed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    onboarding_skipped_steps: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    bot: Mapped[Optional["WorkspaceBot"]] = relationship(back_populates="workspace", uselist=False)
    members: Mapped[list["Member"]] = relationship(back_populates="workspace")  # noqa: F821
    projects: Mapped[list["Project"]] = relationship(back_populates="workspace")  # noqa: F821


class WorkspaceBot(Base):
    """The customer's own BotFather bot. One per workspace."""

    __tablename__ = "workspace_bots"
    __table_args__ = (
        # A bot can only serve one workspace at a time; a disconnected row
        # doesn't block another workspace from connecting the same bot.
        Index(
            "uq_workspace_bots_telegram_bot_id_live",
            "telegram_bot_id",
            unique=True,
            postgresql_where=text("status <> 'disconnected'"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    telegram_bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    # Fernet ciphertext of the BotFather token (key: TOKEN_ENCRYPTION_KEY). Never log it.
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Sent by Telegram in X-Telegram-Bot-Api-Secret-Token on every webhook call.
    webhook_secret: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[BotStatus] = mapped_column(
        str_enum(BotStatus, "bot_status"), nullable=False, server_default=BotStatus.ACTIVE.value
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = updated_at_col()

    workspace: Mapped[Workspace] = relationship(back_populates="bot")
