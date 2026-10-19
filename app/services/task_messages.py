"""Telegram DM text for task notifications, rendered when the change happens.

Stream B (B3) owns the wording and buttons; this is the first version so A4
can queue real messages. Callback data: `task:complete:{task_id}` and
`tasks:mine` (handled by B3).
"""
from html import escape
from zoneinfo import ZoneInfo

from app.models import Task, Workspace
from app.notifications import Button, message_payload


def _due(task: Task, workspace: Workspace) -> str:
    local = task.due_at.astimezone(ZoneInfo(workspace.timezone))
    return local.strftime("%a %d %b, %H:%M")


def _card(task: Task, workspace: Workspace) -> str:
    lines = [f"<b>#{task.number} {escape(task.title)}</b>"]
    if task.description:
        lines.append(escape(task.description))
    lines.append(f"📁 {escape(task.project.name)}")
    lines.append(f"📅 Due {_due(task, workspace)}")
    return "\n".join(lines)


def _actions(task: Task) -> list[list[Button]]:
    return [[
        Button("✅ Mark complete", callback_data=f"task:complete:{task.id}"),
        Button("📋 My tasks", callback_data="tasks:mine"),
    ]]


def render_assigned(task: Task, workspace: Workspace) -> dict:
    return message_payload(f"🆕 New task for you\n\n{_card(task, workspace)}", _actions(task))


def render_unassigned(task: Task, workspace: Workspace) -> dict:
    return message_payload(f"↪️ This task was reassigned to someone else:\n<b>#{task.number} {escape(task.title)}</b>")


def render_updated(task: Task, workspace: Workspace, changed: list[str]) -> dict:
    what = "due date" if changed == ["dueAt"] else "details"
    return message_payload(f"✏️ The {what} of your task changed\n\n{_card(task, workspace)}", _actions(task))


def render_completed_for_assignee(task: Task, workspace: Workspace) -> dict:
    return message_payload(f"✅ Your manager marked this task completed:\n<b>#{task.number} {escape(task.title)}</b>")


def render_completed_for_manager(task: Task, workspace: Workspace, completer_name: str) -> dict:
    return message_payload(
        f"✅ {escape(completer_name)} completed <b>#{task.number} {escape(task.title)}</b>\n📁 {escape(task.project.name)}"
    )


def render_deleted(task: Task, workspace: Workspace) -> dict:
    return message_payload(f"🗑 This task was cancelled:\n<b>#{task.number} {escape(task.title)}</b>")
