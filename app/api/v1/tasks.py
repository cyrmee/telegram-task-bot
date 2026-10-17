import uuid
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Path, Query, status

from app.api.deps import Manager, WorkspaceId
from app.api.errors import not_implemented
from app.api.v1.schemas import ActivityItem, ErrorResponse, ListResponse, Page, Task, TaskCreate, TaskSort, TaskUpdate
from app.models import TaskStatus

router = APIRouter(tags=["tasks"])

TaskId = Annotated[uuid.UUID, Path(alias="taskId")]
INVALID_REFS = {
    422: {
        "model": ErrorResponse,
        "description": "`validation_error`, `project_archived`, `assignee_not_active` (removed member)",
    }
}


@router.get("/workspaces/{workspaceId}/tasks", response_model=Page[Task])
async def list_tasks(
    workspace_id: WorkspaceId,
    member: Manager,
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
    raise not_implemented()


@router.post(
    "/workspaces/{workspaceId}/tasks",
    response_model=Task,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task; the assignee gets a Telegram DM",
    responses=INVALID_REFS,
)
async def create_task(workspace_id: WorkspaceId, body: TaskCreate, member: Manager):
    raise not_implemented()


@router.get("/workspaces/{workspaceId}/tasks/{taskId}", response_model=Task)
async def get_task(workspace_id: WorkspaceId, task_id: TaskId, member: Manager):
    raise not_implemented()


@router.patch(
    "/workspaces/{workspaceId}/tasks/{taskId}",
    response_model=Task,
    summary="Edit, reassign (assigneeId) or reschedule (dueAt)",
    responses=INVALID_REFS,
)
async def update_task(workspace_id: WorkspaceId, task_id: TaskId, body: TaskUpdate, member: Manager):
    raise not_implemented()


@router.post(
    "/workspaces/{workspaceId}/tasks/{taskId}/complete",
    response_model=Task,
    summary="Mark completed (idempotent)",
)
async def complete_task(workspace_id: WorkspaceId, task_id: TaskId, member: Manager):
    raise not_implemented()


@router.post(
    "/workspaces/{workspaceId}/tasks/{taskId}/reopen",
    response_model=Task,
    summary="Back to pending (idempotent)",
)
async def reopen_task(workspace_id: WorkspaceId, task_id: TaskId, member: Manager):
    raise not_implemented()


@router.delete("/workspaces/{workspaceId}/tasks/{taskId}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(workspace_id: WorkspaceId, task_id: TaskId, member: Manager):
    raise not_implemented()


@router.get(
    "/workspaces/{workspaceId}/tasks/{taskId}/activity",
    response_model=ListResponse[ActivityItem],
    summary="Timeline of task events and Telegram deliveries, oldest first",
)
async def get_task_activity(workspace_id: WorkspaceId, task_id: TaskId, member: Manager):
    raise not_implemented()
