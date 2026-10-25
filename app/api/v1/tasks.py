import uuid
from datetime import datetime
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Path, Query, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import Db, Manager, WorkspaceId
from app.api.v1.schemas import ActivityItem, ErrorResponse, ListResponse, Page, Task, TaskCreate, TaskSort, TaskUpdate
from app.models import ChangeSource, TaskStatus
from app.realtime import emit_workspace_event
from app.services import tasks as service

router = APIRouter(tags=["tasks"])

TaskId = Annotated[uuid.UUID, Path(alias="taskId")]


async def _emit(workspace_id, event: str, member, data: dict, **extra) -> Task:
    task = Task.model_validate(data)
    await emit_workspace_event(workspace_id, event, member, task=task.model_dump(mode="json", by_alias=True), **extra)
    return task


INVALID_REFS: dict[int | str, dict[str, Any]] = {
    422: {
        "model": ErrorResponse,
        "description": "`validation_error`, `project_archived`, `assignee_not_active` (removed member)",
    }
}


@router.get("/workspaces/{workspaceId}/tasks", response_model=Page[Task])
def list_tasks(
    workspace_id: WorkspaceId,
    member: Manager,
    db: Db,
    project_id: Annotated[Optional[list[uuid.UUID]], Query(alias="projectId")] = None,
    assignee_id: Annotated[Optional[list[uuid.UUID]], Query(alias="assigneeId")] = None,
    status_: Annotated[Optional[TaskStatus], Query(alias="status")] = None,
    overdue: Annotated[Optional[bool], Query(description="true: pending and past due")] = None,
    due_after: Annotated[Optional[datetime], Query(alias="dueAfter", description="Inclusive")] = None,
    due_before: Annotated[Optional[datetime], Query(alias="dueBefore", description="Exclusive")] = None,
    q: Annotated[Optional[str], Query(max_length=200, description="Search title and description")] = None,
    include_archived: Annotated[bool, Query(alias="includeArchived", description="Include tasks of archived projects")] = False,
    sort: TaskSort = TaskSort.DUE_AT,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=200)] = 50,
):
    filters = service.TaskFilters(
        project_ids=project_id, assignee_ids=assignee_id, status=status_, overdue=overdue,
        due_after=due_after, due_before=due_before, q=q, include_archived=include_archived,
    )
    items, total = service.list_tasks(db, workspace_id, filters, sort.value, page, page_size)
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@router.post(
    "/workspaces/{workspaceId}/tasks",
    response_model=Task,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task; the assignee gets a Telegram DM",
    responses=INVALID_REFS,
)
async def create_task(workspace_id: WorkspaceId, body: TaskCreate, member: Manager, db: Db):
    def work():
        change = service.create_task(db, member, body.model_dump())
        return service.get_task_out(db, workspace_id, change.task_id)

    return await _emit(workspace_id, "task.created", member, await run_in_threadpool(work))


@router.get("/workspaces/{workspaceId}/tasks/{taskId}", response_model=Task)
def get_task(workspace_id: WorkspaceId, task_id: TaskId, member: Manager, db: Db):
    return service.get_task_out(db, workspace_id, task_id)


@router.patch(
    "/workspaces/{workspaceId}/tasks/{taskId}",
    response_model=Task,
    summary="Edit, reassign (assigneeId) or reschedule (dueAt)",
    responses=INVALID_REFS,
)
async def update_task(workspace_id: WorkspaceId, task_id: TaskId, body: TaskUpdate, member: Manager, db: Db):
    def work():
        change = service.update_task(db, member, task_id, body.model_dump(exclude_unset=True))
        return service.get_task_out(db, workspace_id, change.task_id), change.changed

    data, changed = await run_in_threadpool(work)
    if not changed:
        return data
    return await _emit(workspace_id, "task.updated", member, data, changed=changed)


@router.post(
    "/workspaces/{workspaceId}/tasks/{taskId}/complete",
    response_model=Task,
    summary="Mark completed (idempotent)",
)
async def complete_task(workspace_id: WorkspaceId, task_id: TaskId, member: Manager, db: Db):
    def work():
        change = service.complete_task(db, workspace_id, task_id, member, ChangeSource.DASHBOARD)
        return service.get_task_out(db, workspace_id, task_id), change.changed

    data, changed = await run_in_threadpool(work)
    return await _emit(workspace_id, "task.completed", member, data) if changed else data


@router.post(
    "/workspaces/{workspaceId}/tasks/{taskId}/reopen",
    response_model=Task,
    summary="Back to pending (idempotent)",
)
async def reopen_task(workspace_id: WorkspaceId, task_id: TaskId, member: Manager, db: Db):
    def work():
        change = service.reopen_task(db, workspace_id, task_id, member, ChangeSource.DASHBOARD)
        return service.get_task_out(db, workspace_id, task_id), change.changed

    data, changed = await run_in_threadpool(work)
    return await _emit(workspace_id, "task.reopened", member, data) if changed else data


@router.delete("/workspaces/{workspaceId}/tasks/{taskId}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(workspace_id: WorkspaceId, task_id: TaskId, member: Manager, db: Db):
    deleted_id, project_id = await run_in_threadpool(service.delete_task, db, member, task_id)
    await emit_workspace_event(workspace_id, "task.deleted", member, taskId=str(deleted_id), projectId=str(project_id))


@router.get(
    "/workspaces/{workspaceId}/tasks/{taskId}/activity",
    response_model=ListResponse[ActivityItem],
    summary="Timeline of task events and Telegram deliveries, oldest first",
)
def get_task_activity(workspace_id: WorkspaceId, task_id: TaskId, member: Manager, db: Db):
    return {"items": service.activity(db, workspace_id, task_id)}
