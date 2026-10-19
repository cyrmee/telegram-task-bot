import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import Db, Manager, WorkspaceId
from app.api.v1.schemas import ErrorResponse, ListResponse, Project, ProjectCreate, ProjectUpdate
from app.realtime import emit_workspace_event
from app.services import projects as service

router = APIRouter(tags=["projects"])

ProjectId = Annotated[uuid.UUID, Path(alias="projectId")]


async def _emit(workspace_id, event: str, member, data: dict) -> dict:
    project = Project.model_validate(data)
    await emit_workspace_event(workspace_id, event, member, project=project.model_dump(mode="json", by_alias=True))
    return project


@router.get("/workspaces/{workspaceId}/projects", response_model=ListResponse[Project], summary="Projects with task counts")
def list_projects(
    workspace_id: WorkspaceId,
    member: Manager,
    db: Db,
    status_: Annotated[Literal["active", "archived", "all"], Query(alias="status")] = "active",
):
    return {"items": service.list_projects(db, workspace_id, status_)}


@router.post("/workspaces/{workspaceId}/projects", response_model=Project, status_code=status.HTTP_201_CREATED)
async def create_project(workspace_id: WorkspaceId, body: ProjectCreate, member: Manager, db: Db):
    def work():
        project = service.create_project(db, member, body.name, body.description)
        return service.out(db, project)

    return await _emit(workspace_id, "project.created", member, await run_in_threadpool(work))


@router.get("/workspaces/{workspaceId}/projects/{projectId}", response_model=Project)
def get_project(workspace_id: WorkspaceId, project_id: ProjectId, member: Manager, db: Db):
    return service.out(db, service.get_project(db, workspace_id, project_id))


@router.patch("/workspaces/{workspaceId}/projects/{projectId}", response_model=Project)
async def update_project(workspace_id: WorkspaceId, project_id: ProjectId, body: ProjectUpdate, member: Manager, db: Db):
    def work():
        project = service.get_project(db, workspace_id, project_id)
        return service.out(db, service.update_project(db, project, body.model_dump(exclude_unset=True)))

    return await _emit(workspace_id, "project.updated", member, await run_in_threadpool(work))


async def _set_archived(workspace_id, project_id, member, db, archived: bool):
    def work():
        project = service.get_project(db, workspace_id, project_id)
        before = project.status
        project = service.set_archived(db, project, archived)
        return service.out(db, project), before != project.status

    data, changed = await run_in_threadpool(work)
    if changed:
        return await _emit(workspace_id, "project.updated", member, data)
    return data


@router.post("/workspaces/{workspaceId}/projects/{projectId}/archive", response_model=Project, summary="Archive (idempotent)")
async def archive_project(workspace_id: WorkspaceId, project_id: ProjectId, member: Manager, db: Db):
    return await _set_archived(workspace_id, project_id, member, db, True)


@router.post(
    "/workspaces/{workspaceId}/projects/{projectId}/unarchive", response_model=Project, summary="Unarchive (idempotent)"
)
async def unarchive_project(workspace_id: WorkspaceId, project_id: ProjectId, member: Manager, db: Db):
    return await _set_archived(workspace_id, project_id, member, db, False)


@router.delete(
    "/workspaces/{workspaceId}/projects/{projectId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an empty project",
    responses={409: {"model": ErrorResponse, "description": "`project_not_empty`: archive it instead"}},
)
async def delete_project(workspace_id: WorkspaceId, project_id: ProjectId, member: Manager, db: Db):
    def work():
        service.delete_project(db, service.get_project(db, workspace_id, project_id))

    await run_in_threadpool(work)
    await emit_workspace_event(workspace_id, "project.deleted", member, projectId=str(project_id))
