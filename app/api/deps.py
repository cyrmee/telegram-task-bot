"""Auth and tenancy dependencies (docs/api-v1.md §2-3).

Sync on purpose: FastAPI runs them in its threadpool, so the blocking JWKS
fetch and the sync SQLAlchemy session don't stall the event loop.
"""
import uuid
from typing import Annotated, Optional

from fastapi import Depends, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.auth import AuthUser, InvalidToken, verify_token
from app.db import get_db
from app.models import Member, MemberRole, MemberStatus

__all__ = ["AuthUser", "CurrentUser", "Db", "Manager", "WorkspaceId", "current_user", "require_manager", "workspace_member"]

bearer = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    description="better-auth JWT from the dashboard (`authClient.token()`), sent as `Authorization: Bearer <jwt>`.",
)

Db = Annotated[Session, Depends(get_db)]
WorkspaceId = Annotated[uuid.UUID, Path(alias="workspaceId")]


def current_user(credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer)]) -> AuthUser:
    if credentials is None:
        raise ApiError(401, "unauthenticated", "Missing bearer token.")
    try:
        return verify_token(credentials.credentials)
    except InvalidToken:
        raise ApiError(401, "unauthenticated", "Invalid or expired token.") from None


CurrentUser = Annotated[AuthUser, Depends(current_user)]


def workspace_member(role: Optional[MemberRole] = MemberRole.MANAGER):
    """The caller's active members row in {workspaceId}. 404 if none (don't
    reveal which workspaces exist), 403 if the role is too low."""

    def dependency(workspace_id: WorkspaceId, user: CurrentUser, db: Db) -> Member:
        member = db.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id,
                Member.user_id == user.id,
                Member.status == MemberStatus.ACTIVE,
            )
        )
        if member is None:
            raise ApiError(404, "not_found", "Workspace not found.")
        if role is MemberRole.MANAGER and member.role is not MemberRole.MANAGER:
            raise ApiError(403, "forbidden", "Only managers can do this.")
        if user.name and member.display_name != user.name:
            # Keep the dashboard name current (activity logs, member lists). Writes only on change.
            member.display_name = user.name
            db.commit()
        return member

    return dependency


# v1: the dashboard is for managers; members use Telegram only.
# Module-level so tests can use app.dependency_overrides[require_manager].
require_manager = workspace_member(MemberRole.MANAGER)
Manager = Annotated[Member, Depends(require_manager)]
