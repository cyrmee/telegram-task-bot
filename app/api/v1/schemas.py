"""Request/response models for /api/v1. This module *is* the wire contract:
docs/openapi.json is generated from it, and the dashboard's TypeScript types
are generated from that. Change docs/api-v1.md in the same commit.

JSON is camelCase; Python attributes are snake_case.
"""
import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated, Generic, Literal, Optional, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer
from pydantic.alias_generators import to_camel

from app.models.enums import (
    ChangeSource,
    MemberRole,
    MemberStatus,
    NotificationStatus,
    ProjectStatus,
    TaskEventType,
    TaskStatus,
)

T = TypeVar("T")


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


def _utc_iso(v: datetime) -> str:
    return v.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# Every timestamp in a response: ISO-8601 in UTC with a trailing Z.
UtcDatetime = Annotated[datetime, PlainSerializer(_utc_iso, return_type=str, when_used="json")]


# --- common -----------------------------------------------------------------

class ErrorBody(ApiModel):
    code: str = Field(description="Stable machine-readable code, e.g. `member_has_open_tasks`. See docs/api-v1.md §4.")
    message: str = Field(description="Human-readable, safe to show to a manager.")
    details: Optional[dict] = Field(default=None, description="Extra context, e.g. `{\"fields\": [...]}` for validation_error.")
    request_id: Optional[str] = None


class ErrorResponse(ApiModel):
    error: ErrorBody


class ListResponse(ApiModel, Generic[T]):
    items: list[T]


class Page(ApiModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int


class MemberSummary(ApiModel):
    id: uuid.UUID
    display_name: str
    telegram_username: Optional[str] = None
    status: MemberStatus


# --- me / workspaces ----------------------------------------------------------

class User(ApiModel):
    id: str = Field(description="better-auth user id")
    email: str
    name: str
    image: Optional[str] = None


class OnboardingState(ApiModel):
    has_bot: bool
    member_count: int = Field(description="Active members excluding managers")
    project_count: int
    task_count: int


class WorkspaceSummary(ApiModel):
    id: uuid.UUID
    name: str
    role: MemberRole
    onboarding: OnboardingState


class Me(ApiModel):
    user: User
    workspaces: list[WorkspaceSummary]


TimezoneName = Annotated[str, Field(min_length=1, max_length=64, examples=["Africa/Addis_Ababa"], description="IANA timezone name")]


class WorkspaceCreate(ApiModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    timezone: TimezoneName


class WorkspaceUpdate(ApiModel):
    name: Optional[Annotated[str, Field(min_length=1, max_length=120)]] = None
    timezone: Optional[TimezoneName] = None
    reminder_lead_hours: Optional[Annotated[int, Field(ge=0, le=720)]] = None


class Workspace(ApiModel):
    id: uuid.UUID
    name: str
    timezone: str
    reminder_lead_hours: int
    created_at: UtcDatetime
    updated_at: UtcDatetime


# --- bot ----------------------------------------------------------------------

class BotState(str, Enum):
    NOT_CONNECTED = "not_connected"
    ACTIVE = "active"
    INVALID = "invalid"
    DISCONNECTED = "disconnected"


class WebhookHealth(ApiModel):
    url_ok: bool = Field(description="Telegram's webhook URL matches ours")
    pending_update_count: int
    last_error_at: Optional[UtcDatetime] = None
    last_error_message: Optional[str] = None


class Bot(ApiModel):
    status: BotState
    username: Optional[str] = Field(default=None, description="Without the @")
    telegram_bot_id: Optional[int] = None
    connected_at: Optional[UtcDatetime] = None
    last_error: Optional[str] = None
    webhook: Optional[WebhookHealth] = Field(default=None, description="Live from getWebhookInfo; null when not active")


class BotConnect(ApiModel):
    token: Annotated[str, Field(min_length=20, max_length=128, description="BotFather token. Never echoed back.")]


# --- members ------------------------------------------------------------------

class TelegramProfile(ApiModel):
    user_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class Member(ApiModel):
    id: uuid.UUID
    role: MemberRole
    status: MemberStatus
    display_name: str = Field(description="Telegram name, else dashboard name, else @invitedUsername")
    user_id: Optional[str] = Field(default=None, description="better-auth user id if they have a dashboard account")
    email: Optional[str] = None
    telegram: Optional[TelegramProfile] = None
    invited_username: Optional[str] = None
    open_task_count: int
    joined_at: Optional[UtcDatetime] = None
    created_at: UtcDatetime


class MemberInviteByUsername(ApiModel):
    telegram_username: Annotated[str, Field(min_length=5, max_length=33, pattern=r"^@?[A-Za-z0-9_]{5,32}$")]


class MemberUpdate(ApiModel):
    role: MemberRole


# --- invites ------------------------------------------------------------------

class InviteState(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    USED_UP = "used_up"


class InviteCreate(ApiModel):
    expires_in_hours: Annotated[int, Field(ge=1, le=720)] = 168
    max_uses: Optional[Annotated[int, Field(ge=1, le=1000)]] = Field(default=1, description="null = unlimited")


class Invite(ApiModel):
    id: uuid.UUID
    token: str
    url: str = Field(examples=["https://t.me/acme_nest_bot?start=Xy3k9..."])
    state: InviteState
    expires_at: UtcDatetime
    max_uses: Optional[int] = None
    use_count: int
    revoked_at: Optional[UtcDatetime] = None
    created_by: Optional[MemberSummary] = None
    created_at: UtcDatetime


# --- projects -----------------------------------------------------------------

class TaskCounts(ApiModel):
    total: int
    pending: int
    completed: int
    overdue: int


class ProjectCreate(ApiModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Optional[Annotated[str, Field(max_length=5000)]] = None


class ProjectUpdate(ApiModel):
    name: Optional[Annotated[str, Field(min_length=1, max_length=200)]] = None
    description: Optional[Annotated[str, Field(max_length=5000)]] = None


class Project(ApiModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    status: ProjectStatus
    archived_at: Optional[UtcDatetime] = None
    task_counts: TaskCounts
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ProjectSummary(ApiModel):
    id: uuid.UUID
    name: str


# --- tasks --------------------------------------------------------------------

class DeliveryState(str, Enum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    UNREACHABLE = "unreachable"  # member blocked the bot
    NOT_LINKED = "not_linked"  # assignee hasn't started the bot yet


class Delivery(ApiModel):
    state: DeliveryState
    sent_at: Optional[UtcDatetime] = None
    last_error: Optional[str] = None


ReminderOffsets = Annotated[
    list[Annotated[int, Field(ge=1, le=43200)]],
    Field(max_length=5, description="Minutes before dueAt. Replaces the workspace default for this task; [] = use the default."),
]


class TaskCreate(ApiModel):
    title: Annotated[str, Field(min_length=1, max_length=300)]
    description: Optional[Annotated[str, Field(max_length=5000)]] = None
    project_id: uuid.UUID
    assignee_id: uuid.UUID
    due_at: AwareDatetime = Field(description="Must include an offset; stored as UTC")
    reminder_offsets_minutes: Optional[ReminderOffsets] = None


class TaskUpdate(ApiModel):
    """Changing assigneeId is a reassign; changing dueAt reschedules reminders."""

    title: Optional[Annotated[str, Field(min_length=1, max_length=300)]] = None
    description: Optional[Annotated[str, Field(max_length=5000)]] = None
    project_id: Optional[uuid.UUID] = None
    assignee_id: Optional[uuid.UUID] = None
    due_at: Optional[AwareDatetime] = None
    reminder_offsets_minutes: Optional[ReminderOffsets] = None


class Task(ApiModel):
    id: uuid.UUID
    number: int = Field(description="Short per-workspace number, shown as #12 and used by /done 12")
    title: str
    description: Optional[str] = None
    project: ProjectSummary
    assignee: MemberSummary
    due_at: UtcDatetime
    status: TaskStatus
    is_overdue: bool
    completed_at: Optional[UtcDatetime] = None
    completed_via: Optional[ChangeSource] = None
    completed_by: Optional[MemberSummary] = None
    created_by: Optional[MemberSummary] = None
    reminder_offsets_minutes: list[int] = Field(description="Effective offsets (per-task override or workspace default)")
    delivery: Optional[Delivery] = Field(default=None, description="State of the assignment DM to the current assignee")
    created_at: UtcDatetime
    updated_at: UtcDatetime


class TaskSort(str, Enum):
    DUE_AT = "dueAt"
    DUE_AT_DESC = "-dueAt"
    CREATED_AT = "createdAt"
    CREATED_AT_DESC = "-createdAt"
    NUMBER = "number"
    NUMBER_DESC = "-number"


class ActivityKind(str, Enum):
    EVENT = "event"
    NOTIFICATION = "notification"


class ActivityItem(ApiModel):
    kind: ActivityKind
    at: UtcDatetime
    event_type: Optional[TaskEventType] = Field(default=None, description="kind=event")
    source: Optional[ChangeSource] = Field(default=None, description="kind=event")
    actor: Optional[MemberSummary] = None
    notification_type: Optional[str] = Field(default=None, description="kind=notification, e.g. assigned, reminder")
    notification_status: Optional[NotificationStatus] = None
    recipient: Optional[MemberSummary] = None
    data: dict = Field(default_factory=dict)


# --- analytics ----------------------------------------------------------------

class Rates(ApiModel):
    total: int
    completed: int
    pending: int
    overdue: int
    completion_rate: Optional[float] = Field(description="completed / total in [0, 1]; null when total = 0")


class ProjectStats(Rates):
    project_id: uuid.UUID
    name: str


class MemberStats(Rates):
    member_id: uuid.UUID
    display_name: str
    open_tasks: int = Field(description="Pending now, regardless of the date range (workload)")


class DateRange(ApiModel):
    from_: date = Field(alias="from")
    to: date


class Analytics(ApiModel):
    range: DateRange
    totals: Rates
    by_project: list[ProjectStats]
    by_member: list[MemberStats]
    overdue_tasks: list[Task] = Field(description="Oldest first, at most 20")
    generated_at: UtcDatetime


# --- realtime (Socket.IO) -------------------------------------------------------

class EventActor(ApiModel):
    source: ChangeSource
    member_id: Optional[uuid.UUID] = None


class RealtimeEvent(ApiModel):
    """Envelope for every Socket.IO event in namespace /v1."""

    event: str
    workspace_id: uuid.UUID
    at: UtcDatetime
    actor: EventActor


class TaskEventPayload(RealtimeEvent):
    event: Literal["task.created", "task.updated", "task.completed", "task.reopened"]
    task: Task
    changed: list[str] = Field(default_factory=list, description="task.updated: camelCase field names that changed")


class TaskDeletedPayload(RealtimeEvent):
    event: Literal["task.deleted"]
    task_id: uuid.UUID
    project_id: uuid.UUID


class TaskDeliveryPayload(RealtimeEvent):
    event: Literal["task.delivery"]
    task_id: uuid.UUID
    delivery: Delivery


class ProjectEventPayload(RealtimeEvent):
    event: Literal["project.created", "project.updated"]
    project: Project


class ProjectDeletedPayload(RealtimeEvent):
    event: Literal["project.deleted"]
    project_id: uuid.UUID


class MemberEventPayload(RealtimeEvent):
    event: Literal["member.joined", "member.updated"]
    member: Member


class MemberRemovedPayload(RealtimeEvent):
    event: Literal["member.removed"]
    member_id: uuid.UUID


class BotEventPayload(RealtimeEvent):
    event: Literal["bot.updated"]
    bot: Bot


class WorkspaceEventPayload(RealtimeEvent):
    event: Literal["workspace.updated"]
    workspace: Workspace


REALTIME_PAYLOADS = [
    TaskEventPayload,
    TaskDeletedPayload,
    TaskDeliveryPayload,
    ProjectEventPayload,
    ProjectDeletedPayload,
    MemberEventPayload,
    MemberRemovedPayload,
    BotEventPayload,
    WorkspaceEventPayload,
]
