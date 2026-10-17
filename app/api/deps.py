"""Auth and tenancy dependencies (signatures fixed by 0.3, implemented in A2).

current_user: verifies `Authorization: Bearer <jwt>` issued by the dashboard's
better-auth JWT plugin against its JWKS (cached), checking exp, iss and aud.

workspace_member(role): resolves the caller's members row for the
{workspaceId} in the path. 404 if they aren't an active member (don't leak
which workspaces exist), 403 if their role is too low.
"""
import uuid
from dataclasses import dataclass
from typing import Annotated, Optional

from fastapi import Depends, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.errors import ApiError
from app.models import Member, MemberRole

bearer = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    description="better-auth JWT from the dashboard (`authClient.token()`), sent as `Authorization: Bearer <jwt>`.",
)


@dataclass(frozen=True)
class AuthUser:
    id: str  # better-auth user id (JWT `sub`)
    email: Optional[str]
    name: Optional[str]


async def current_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer)],
) -> AuthUser:
    if credentials is None:
        raise ApiError(401, "unauthenticated", "Missing bearer token.")
    raise ApiError(501, "not_implemented", "JWT verification lands in A2.")


WorkspaceId = Annotated[uuid.UUID, Path(alias="workspaceId")]


def workspace_member(role: Optional[MemberRole] = MemberRole.MANAGER):
    async def dependency(
        workspace_id: WorkspaceId,
        user: Annotated[AuthUser, Depends(current_user)],
    ) -> Member:
        raise ApiError(501, "not_implemented", "Workspace guard lands in A2.")

    return dependency


CurrentUser = Annotated[AuthUser, Depends(current_user)]
# v1: the dashboard is for managers; members use Telegram only.
# Module-level so tests can use app.dependency_overrides[require_manager].
require_manager = workspace_member(MemberRole.MANAGER)
Manager = Annotated[Member, Depends(require_manager)]
