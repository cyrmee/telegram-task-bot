import logging

from fastapi import APIRouter, Request, Response, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, Db, Manager, WorkspaceId
from app.api.errors import ApiError
from app.bot.crypto import TokenKeyMissing
from app.bot.errors import BotInUse, InvalidBotToken, TelegramUnavailable
from app.api.v1.schemas import (
    Bot,
    BotConnect,
    ErrorResponse,
    Workspace,
    WorkspaceCreate,
    WorkspaceDelete,
    WorkspaceExport,
    WorkspaceUpdate,
)
from app.models import Workspace as WorkspaceRow
from app.realtime import close_workspace_room, emit_workspace_event, join_user_to_workspace
from app.services import workspace_data
from app.services import workspaces as service

router = APIRouter(tags=["workspaces"])
logger = logging.getLogger(__name__)


@router.post(
    "/workspaces",
    response_model=Workspace,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace; the caller becomes its manager",
)
async def create_workspace(body: WorkspaceCreate, user: CurrentUser, db: Db):
    workspace = await run_in_threadpool(service.create_workspace, db, user, body.name, body.timezone)
    await join_user_to_workspace(user.id, workspace.id)
    return service.workspace_out(workspace)


@router.get("/workspaces/{workspaceId}", response_model=Workspace)
def get_workspace(workspace_id: WorkspaceId, member: Manager, db: Db):
    return service.workspace_out(db.get(WorkspaceRow, workspace_id))


@router.patch("/workspaces/{workspaceId}", response_model=Workspace)
async def update_workspace(workspace_id: WorkspaceId, body: WorkspaceUpdate, member: Manager, db: Db):
    workspace = await run_in_threadpool(service.update_workspace, db, workspace_id, body.model_dump(exclude_unset=True))
    data = Workspace.model_validate(service.workspace_out(workspace))
    await emit_workspace_event(
        workspace_id, "workspace.updated", member, workspace=data.model_dump(mode="json", by_alias=True)
    )
    return data


@router.get(
    "/workspaces/{workspaceId}/export",
    response_model=WorkspaceExport,
    summary="Download everything in the workspace as JSON (no bot token, secrets or invite tokens)",
)
def export_workspace(workspace_id: WorkspaceId, member: Manager, db: Db, response: Response):
    response.headers["Content-Disposition"] = f'attachment; filename="nest-workspace-{workspace_id}.json"'
    return workspace_data.export_workspace(db, workspace_id)


@router.delete(
    "/workspaces/{workspaceId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently delete the workspace and everything in it",
    responses={422: {"model": ErrorResponse, "description": "`confirmation_mismatch`: confirmName isn't the workspace name"}},
)
async def delete_workspace(
    workspace_id: WorkspaceId, body: WorkspaceDelete, member: Manager, user: CurrentUser, db: Db, request: Request
):
    await run_in_threadpool(workspace_data.check_delete_confirmation, db, workspace_id, body.confirm_name)
    manager = getattr(request.app.state, "bot_manager", None)
    if manager is not None:
        try:  # deleteWebhook + stop the bot; the token row goes with the cascade either way
            await manager.disconnect(workspace_id, actor=member)
        except Exception:
            logger.exception("Bot disconnect failed during workspace deletion", extra={"workspace_id": str(workspace_id)})
    await emit_workspace_event(workspace_id, "workspace.deleted", member)
    await run_in_threadpool(workspace_data.delete_workspace, db, workspace_id, user)
    await close_workspace_room(workspace_id)


def _bot_manager(request: Request):
    manager = getattr(request.app.state, "bot_manager", None)
    if manager is None:
        raise ApiError(502, "telegram_unavailable", "The bot runtime isn't running on this server.")
    return manager


@router.get("/workspaces/{workspaceId}/bot", response_model=Bot, tags=["bot"], summary="Bot status and webhook health")
async def get_bot(workspace_id: WorkspaceId, member: Manager, request: Request):
    return await _bot_manager(request).status(workspace_id)


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
async def connect_bot(workspace_id: WorkspaceId, body: BotConnect, member: Manager, request: Request):
    try:
        return await _bot_manager(request).connect(workspace_id, body.token, actor=member)
    except BotInUse as e:
        raise ApiError(409, "bot_in_use", str(e)) from e
    except InvalidBotToken as e:
        raise ApiError(422, "invalid_bot_token", str(e)) from e
    except TelegramUnavailable as e:
        raise ApiError(502, "telegram_unavailable", "Telegram didn't respond. Try again in a minute.") from e
    except TokenKeyMissing as e:
        raise ApiError(500, "internal_error", "Bot tokens can't be stored: TOKEN_ENCRYPTION_KEY isn't set on the server.") from e


@router.delete(
    "/workspaces/{workspaceId}/bot",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["bot"],
    summary="Disconnect the bot (deleteWebhook; members stop receiving DMs)",
)
async def disconnect_bot(workspace_id: WorkspaceId, member: Manager, request: Request):
    await _bot_manager(request).disconnect(workspace_id, actor=member)
