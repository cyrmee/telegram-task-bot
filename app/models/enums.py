import enum


class BotStatus(str, enum.Enum):
    ACTIVE = "active"
    INVALID = "invalid"  # Telegram rejected the token (revoked / 401)
    DISCONNECTED = "disconnected"  # manager disconnected it; frees the bot for reuse


class MemberRole(str, enum.Enum):
    MANAGER = "manager"
    MEMBER = "member"


class MemberStatus(str, enum.Enum):
    PENDING = "pending"  # invited by @username, hasn't sent /start yet
    ACTIVE = "active"
    UNREACHABLE = "unreachable"  # blocked the bot (Telegram 403)
    REMOVED = "removed"


class OnboardingStep(str, enum.Enum):
    CONNECT_BOT = "connect_bot"
    INVITE_TEAM = "invite_team"
    FIRST_PROJECT = "first_project"
    FIRST_TASK = "first_task"


class ProjectStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"


class ChangeSource(str, enum.Enum):
    TELEGRAM = "telegram"
    DASHBOARD = "dashboard"
    SYSTEM = "system"


class TaskEventType(str, enum.Enum):
    CREATED = "created"
    UPDATED = "updated"
    REASSIGNED = "reassigned"
    DUE_CHANGED = "due_changed"
    COMPLETED = "completed"
    REOPENED = "reopened"


class NotificationType(str, enum.Enum):
    ASSIGNED = "assigned"
    UNASSIGNED = "unassigned"  # sent to the previous assignee on reassign
    TASK_UPDATED = "task_updated"
    TASK_DELETED = "task_deleted"
    REMINDER = "reminder"
    OVERDUE = "overdue"
    COMPLETED_MANAGER = "completed_manager"
    DIGEST = "digest"
    INVITE = "invite"
    WELCOME = "welcome"


class NotificationStatus(str, enum.Enum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"  # gave up after max attempts, or permanent error
    CANCELLED = "cancelled"  # superseded, e.g. reminder after a due-date change
