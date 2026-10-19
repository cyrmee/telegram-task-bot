import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Path, Query, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import Db, Manager, WorkspaceId
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
from app.realtime import emit_workspace_event, join_user_to_workspace, remove_user_from_workspace
from app.services import invites as invite_service
from app.services import members as service

router = APIRouter()

MemberId = Annotated[uuid.UUID, Path(alias="memberId")]
InviteId = Annotated[uuid.UUID, Path(alias="inviteId")]


async def _emit_member(workspace_id, event: str, actor, data: dict) -> Member:
    member = Member.model_validate(data)
    await emit_workspace_event(workspace_id, event, actor, member=member.model_dump(mode="json", by_alias=True))
    return member


@router.get("/workspaces/{workspaceId}/members", response_model=ListResponse[Member], tags=["members"])
def list_members(
    workspace_id: WorkspaceId,
    member: Manager,
    db: Db,
    status_: Annotated[Optional[list[MemberStatus]], Query(alias="status", description="Default: all but removed")] = None,
    role: Optional[MemberRole] = None,
):
    return {"items": service.list_members(db, workspace_id, status_, role)}


@router.post(
    "/workspaces/{workspaceId}/members",
    response_model=Member,
    status_code=status.HTTP_201_CREATED,
    tags=["members"],
    summary="Add a pending member by @username (matched when they /start the bot)",
    responses={409: {"model": ErrorResponse, "description": "`member_exists`"}},
)
async def add_member_by_username(workspace_id: WorkspaceId, body: MemberInviteByUsername, member: Manager, db: Db):
    def work():
        return service.member_out(db, service.add_by_username(db, member, body.telegram_username))

    return await _emit_member(workspace_id, "member.updated", member, await run_in_threadpool(work))


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
async def update_member(workspace_id: WorkspaceId, member_id: MemberId, body: MemberUpdate, member: Manager, db: Db):
    def work():
        updated = service.update_role(db, member, member_id, body.role)
        return updated.user_id, service.member_out(db, updated)

    user_id, data = await run_in_threadpool(work)
    if user_id:  # dashboard rooms follow the role
        if body.role == MemberRole.MANAGER:
            await join_user_to_workspace(user_id, workspace_id)
        else:
            await remove_user_from_workspace(user_id, workspace_id)
    return await _emit_member(workspace_id, "member.updated", member, data)


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
    db: Db,
    reassign_to: Annotated[
        Optional[uuid.UUID], Query(alias="reassignTo", description="Required when the member has pending tasks")
    ] = None,
):
    removed = await run_in_threadpool(service.remove_member, db, member, member_id, reassign_to)
    if removed.user_id:
        await remove_user_from_workspace(removed.user_id, workspace_id)
    await emit_workspace_event(workspace_id, "member.removed", member, memberId=str(member_id))


@router.get("/workspaces/{workspaceId}/invites", response_model=ListResponse[Invite], tags=["invites"])
def list_invites(
    workspace_id: WorkspaceId,
    member: Manager,
    db: Db,
    state: Annotated[Optional[list[InviteState]], Query(description="Default: active")] = None,
):
    return {"items": invite_service.list_invites(db, workspace_id, [s.value for s in state] if state else None)}


@router.post(
    "/workspaces/{workspaceId}/invites",
    response_model=Invite,
    status_code=status.HTTP_201_CREATED,
    tags=["invites"],
    summary="Create a t.me/<bot>?start=<token> invite link",
    responses={409: {"model": ErrorResponse, "description": "`bot_not_connected`"}},
)
def create_invite(workspace_id: WorkspaceId, body: InviteCreate, member: Manager, db: Db):
    return invite_service.create_invite(db, member, body.expires_in_hours, body.max_uses)


@router.delete(
    "/workspaces/{workspaceId}/invites/{inviteId}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["invites"],
    summary="Revoke an invite link (idempotent)",
)
def revoke_invite(workspace_id: WorkspaceId, invite_id: InviteId, member: Manager, db: Db):
    invite_service.revoke_invite(db, member, invite_id)
