import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Path, Query, status

from app.api.deps import Db, Manager, WorkspaceId
from app.api.errors import not_implemented
from app.api.v1.schemas import (
    ErrorResponse,
    Invite,
    InviteCreate,
    InviteState,
    ListResponse,
    Member,
    MemberInviteByUsername,
    MemberUpdate,
    TelegramLink,
)
from app.models import MemberRole, MemberStatus
from app.services import invites as invite_service

router = APIRouter()

MemberId = Annotated[uuid.UUID, Path(alias="memberId")]
InviteId = Annotated[uuid.UUID, Path(alias="inviteId")]


@router.get("/workspaces/{workspaceId}/members", response_model=ListResponse[Member], tags=["members"])
async def list_members(
    workspace_id: WorkspaceId,
    member: Manager,
    status_: Annotated[Optional[list[MemberStatus]], Query(alias="status", description="Default: all but removed")] = None,
    role: Optional[MemberRole] = None,
):
    raise not_implemented()


@router.post(
    "/workspaces/{workspaceId}/members",
    response_model=Member,
    status_code=status.HTTP_201_CREATED,
    tags=["members"],
    summary="Add a pending member by @username (matched when they /start the bot)",
    responses={409: {"model": ErrorResponse, "description": "`member_exists`"}},
)
async def add_member_by_username(workspace_id: WorkspaceId, body: MemberInviteByUsername, member: Manager):
    raise not_implemented()


@router.post(
    "/workspaces/{workspaceId}/members/me/telegram-link",
    response_model=TelegramLink,
    status_code=status.HTTP_201_CREATED,
    tags=["members"],
    summary="Deep link that attaches the caller's Telegram account to their own membership",
    responses={
        409: {"model": ErrorResponse, "description": "`bot_not_connected` or `telegram_already_linked`"},
    },
)
def create_my_telegram_link(workspace_id: WorkspaceId, member: Manager, db: Db):
    return invite_service.create_telegram_link(db, member)


@router.patch(
    "/workspaces/{workspaceId}/members/{memberId}",
    response_model=Member,
    tags=["members"],
    summary="Change a member's role",
    responses={409: {"model": ErrorResponse, "description": "`last_manager`"}},
)
async def update_member(workspace_id: WorkspaceId, member_id: MemberId, body: MemberUpdate, member: Manager):
    raise not_implemented()


@router.delete(
    "/workspaces/{workspaceId}/members/{memberId}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["members"],
    summary="Remove a member, reassigning their pending tasks",
    responses={409: {"model": ErrorResponse, "description": "`member_has_open_tasks` (pass reassignTo) or `last_manager`"}},
)
async def remove_member(
    workspace_id: WorkspaceId,
    member_id: MemberId,
    member: Manager,
    reassign_to: Annotated[
        Optional[uuid.UUID], Query(alias="reassignTo", description="Required when the member has pending tasks")
    ] = None,
):
    raise not_implemented()


@router.get("/workspaces/{workspaceId}/invites", response_model=ListResponse[Invite], tags=["invites"])
async def list_invites(
    workspace_id: WorkspaceId,
    member: Manager,
    state: Annotated[Optional[list[InviteState]], Query(description="Default: active")] = None,
):
    raise not_implemented()


@router.post(
    "/workspaces/{workspaceId}/invites",
    response_model=Invite,
    status_code=status.HTTP_201_CREATED,
    tags=["invites"],
    summary="Create a t.me/<bot>?start=<token> invite link",
    responses={409: {"model": ErrorResponse, "description": "`bot_not_connected`"}},
)
async def create_invite(workspace_id: WorkspaceId, body: InviteCreate, member: Manager):
    raise not_implemented()


@router.delete(
    "/workspaces/{workspaceId}/invites/{inviteId}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["invites"],
    summary="Revoke an invite link (idempotent)",
)
async def revoke_invite(workspace_id: WorkspaceId, invite_id: InviteId, member: Manager):
    raise not_implemented()
