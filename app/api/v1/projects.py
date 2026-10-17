import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, status

from app.api.deps import Manager, WorkspaceId
from app.api.errors import not_implemented
from app.api.v1.schemas import ErrorResponse, ListResponse, Project, ProjectCreate, ProjectUpdate

router = APIRouter(tags=["projects"])

ProjectId = Annotated[uuid.UUID, Path(alias="projectId")]


@router.get("/workspaces/{workspaceId}/projects", response_model=ListResponse[Project], summary="Projects with task counts")
async def list_projects(
    workspace_id: WorkspaceId,
    member: Manager,
    status_: Annotated[Literal["active", "archived", "all"], Query(alias="status")] = "active",
):
    raise not_implemented()


@router.post("/workspaces/{workspaceId}/projects", response_model=Project, status_code=status.HTTP_201_CREATED)
async def create_project(workspace_id: WorkspaceId, body: ProjectCreate, member: Manager):
    raise not_implemented()


@router.get("/workspaces/{workspaceId}/projects/{projectId}", response_model=Project)
async def get_project(workspace_id: WorkspaceId, project_id: ProjectId, member: Manager):
    raise not_implemented()


@router.patch("/workspaces/{workspaceId}/projects/{projectId}", response_model=Project)
async def update_project(workspace_id: WorkspaceId, project_id: ProjectId, body: ProjectUpdate, member: Manager):
    raise not_implemented()


@router.post("/workspaces/{workspaceId}/projects/{projectId}/archive", response_model=Project, summary="Archive (idempotent)")
async def archive_project(workspace_id: WorkspaceId, project_id: ProjectId, member: Manager):
    raise not_implemented()


@router.post(
    "/workspaces/{workspaceId}/projects/{projectId}/unarchive", response_model=Project, summary="Unarchive (idempotent)"
)
async def unarchive_project(workspace_id: WorkspaceId, project_id: ProjectId, member: Manager):
    raise not_implemented()


@router.delete(
    "/workspaces/{workspaceId}/projects/{projectId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an empty project",
    responses={409: {"model": ErrorResponse, "description": "`project_not_empty`: archive it instead"}},
)
async def delete_project(workspace_id: WorkspaceId, project_id: ProjectId, member: Manager):
    raise not_implemented()
