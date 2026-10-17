import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at_col, str_enum, updated_at_col, uuid_pk
from .enums import MemberRole, MemberStatus


class TelegramUser(Base):
    """A Telegram account, global across workspaces (profile cache)."""

    __tablename__ = "telegram_users"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128))
    last_name: Mapped[Optional[str]] = mapped_column(String(128))
    language_code: Mapped[Optional[str]] = mapped_column(String(16))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class Member(Base):
    """A person in a workspace. Managers usually have a dashboard account
    (user_id); members usually only have Telegram (telegram_user_id). Either
    may be linked later."""

    __tablename__ = "members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "telegram_user_id"),
        UniqueConstraint("workspace_id", "user_id"),
        CheckConstraint(
            "user_id IS NOT NULL OR telegram_user_id IS NOT NULL OR invited_username IS NOT NULL",
            name="has_identity",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # better-auth "user".id (no FK, see Workspace.owner_user_id).
    user_id: Mapped[Optional[str]] = mapped_column(Text, index=True)
    telegram_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("telegram_users.telegram_user_id"), index=True
    )
    # Set when invited by @username before they've started the bot; matched on /start.
    invited_username: Mapped[Optional[str]] = mapped_column(String(64))
    role: Mapped[MemberRole] = mapped_column(
        str_enum(MemberRole, "member_role"), nullable=False, server_default=MemberRole.MEMBER.value
    )
    status: Mapped[MemberStatus] = mapped_column(
        str_enum(MemberStatus, "member_status"), nullable=False, server_default=MemberStatus.ACTIVE.value
    )
    # DM chat with the workspace bot. Only known after the user sends /start.
    private_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    invite_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invites.id", ondelete="SET NULL", use_alter=True)
    )
    joined_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    workspace: Mapped["Workspace"] = relationship(back_populates="members")  # noqa: F821
    telegram_user: Mapped[Optional[TelegramUser]] = relationship()


class Invite(Base):
    """Join link: https://t.me/<bot>?start=<token>."""

    __tablename__ = "invites"
    __table_args__ = (
        CheckConstraint("max_uses IS NULL OR max_uses > 0", name="max_uses_positive"),
        CheckConstraint("use_count >= 0", name="use_count_non_negative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Telegram limits the start parameter to 64 chars of [A-Za-z0-9_-].
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    role: Mapped[MemberRole] = mapped_column(
        str_enum(MemberRole, "invite_role"), nullable=False, server_default=MemberRole.MEMBER.value
    )
    created_by_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_uses: Mapped[Optional[int]] = mapped_column(Integer, server_default="1")  # NULL = unlimited
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
