from fastapi import APIRouter, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, Db, Manager, WorkspaceId
from app.api.errors import not_implemented
from app.api.v1.schemas import Bot, BotConnect, ErrorResponse, Workspace, WorkspaceCreate, WorkspaceUpdate
from app.models import Workspace as WorkspaceRow
from app.realtime import emit_workspace_event, join_user_to_workspace
from app.services import workspaces as service

router = APIRouter(tags=["workspaces"])


@router.post(
    "/workspaces",
    response_model=Workspace,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace; the caller becomes its manager",
)
async def create_workspace(body: WorkspaceCreate, user: CurrentUser, db: Db):
    workspace = await run_in_threadpool(service.create_workspace, db, user, body.name, body.timezone)
    await join_user_to_workspace(user.id, workspace.id)
    return workspace


@router.get("/workspaces/{workspaceId}", response_model=Workspace)
def get_workspace(workspace_id: WorkspaceId, member: Manager, db: Db):
    return db.get(WorkspaceRow, workspace_id)


@router.patch("/workspaces/{workspaceId}", response_model=Workspace)
async def update_workspace(workspace_id: WorkspaceId, body: WorkspaceUpdate, member: Manager, db: Db):
    workspace = await run_in_threadpool(service.update_workspace, db, workspace_id, body.model_dump(exclude_unset=True))
    data = Workspace.model_validate(workspace)
    await emit_workspace_event(
        workspace_id, "workspace.updated", member, workspace=data.model_dump(mode="json", by_alias=True)
    )
    return data


@router.get("/workspaces/{workspaceId}/bot", response_model=Bot, tags=["bot"], summary="Bot status and webhook health")
async def get_bot(workspace_id: WorkspaceId, member: Manager):
    raise not_implemented()


@router.put(
    "/workspaces/{workspaceId}/bot",
    response_model=Bot,
    tags=["bot"],
    summary="Connect or replace the workspace's BotFather bot",
    responses={
        409: {"model": ErrorResponse, "description": "`bot_in_use`: bot is live in another workspace"},
        422: {"model": ErrorResponse, "description": "`invalid_bot_token`: Telegram rejected the token"},
        502: {"model": ErrorResponse, "description": "`telegram_unavailable`"},
    },
)
async def connect_bot(workspace_id: WorkspaceId, body: BotConnect, member: Manager):
    raise not_implemented()


@router.delete(
    "/workspaces/{workspaceId}/bot",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["bot"],
    summary="Disconnect the bot (deleteWebhook; members stop receiving DMs)",
)
async def disconnect_bot(workspace_id: WorkspaceId, member: Manager):
    raise not_implemented()
