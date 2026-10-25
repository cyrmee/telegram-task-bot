import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at_col, str_enum, updated_at_col, uuid_pk
from .enums import ProjectStatus

if TYPE_CHECKING:
    from .task import Task
    from .workspace import Workspace


class Project(Base):
    """A project ("Group" in the UX flow map)."""

    __tablename__ = "projects"
    __table_args__ = (
        Index(None, "workspace_id", "status"),
        UniqueConstraint("workspace_id", "id"),  # target of tenant-scoped FKs
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[ProjectStatus] = mapped_column(
        str_enum(ProjectStatus, "project_status"), nullable=False, server_default=ProjectStatus.ACTIVE.value
    )
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_by_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    workspace: Mapped["Workspace"] = relationship(back_populates="projects")
    tasks: Mapped[list["Task"]] = relationship(back_populates="project", overlaps="assignee")
