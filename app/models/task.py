import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at_col, str_enum, updated_at_col, uuid_pk
from .enums import ChangeSource, TaskEventType, TaskStatus


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("workspace_id", "number"),
        CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name="completed_at_matches_status",
        ),
        Index(None, "workspace_id", "status", "due_at"),
        Index(None, "workspace_id", "assignee_member_id", "status"),
        Index(None, "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # Short per-workspace number shown in Telegram ("#12", `/done 12`).
    # Allocated from workspaces.next_task_number.
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Default NO ACTION: delete a project only when it has no tasks; a
    # workspace delete still cascades through in one statement.
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    assignee_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        str_enum(TaskStatus, "task_status"), nullable=False, server_default=TaskStatus.PENDING.value
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_via: Mapped[Optional[ChangeSource]] = mapped_column(str_enum(ChangeSource, "completed_via"))
    completed_by_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL")
    )
    created_by_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    project: Mapped["Project"] = relationship(back_populates="tasks")  # noqa: F821
    assignee: Mapped["Member"] = relationship(foreign_keys=[assignee_member_id])  # noqa: F821
    reminders: Mapped[list["TaskReminder"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)


class TaskReminder(Base):
    """Per-task override of the workspace's reminder_lead_hours."""

    __tablename__ = "task_reminders"
    __table_args__ = (CheckConstraint("offset_minutes > 0", name="offset_minutes_positive"),)

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    offset_minutes: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


class TaskEvent(Base):
    """Activity log for the task detail view (created, reassigned, completed via Telegram, ...)."""

    __tablename__ = "task_events"
    __table_args__ = (Index(None, "task_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[TaskEventType] = mapped_column(str_enum(TaskEventType, "task_event_type"), nullable=False)
    source: Mapped[ChangeSource] = mapped_column(str_enum(ChangeSource, "task_event_source"), nullable=False)
    actor_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL")
    )
    # Before/after values, e.g. {"from": "<member id>", "to": "<member id>"}.
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = created_at_col()
