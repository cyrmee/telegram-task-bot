"""Emit task events to the dashboard after a committed change.

For callers outside the API, e.g. B3's Complete button:

    change = await asyncio.to_thread(tasks.complete_task, db, ws_id, task_id, member, ChangeSource.TELEGRAM)
    if change.changed:
        await publish_task_event(db, ws_id, change.task_id, "task.completed", member, source="telegram")
"""
import asyncio
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.api.v1.schemas import Task
from app.models import Member
from app.realtime import emit_workspace_event
from app.services.tasks import get_task_out


async def publish_task_event(
    db: Session,
    workspace_id: uuid.UUID,
    task_id: uuid.UUID,
    event: str,
    actor: Optional[Member],
    source: str = "dashboard",
    changed: Optional[list[str]] = None,
) -> None:
    data = await asyncio.to_thread(get_task_out, db, workspace_id, task_id)
    extra = {"changed": changed} if changed is not None else {}
    await emit_workspace_event(
        workspace_id, event, actor, source, task=Task.model_validate(data).model_dump(mode="json", by_alias=True), **extra
    )
