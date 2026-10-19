from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Query

from app.api.deps import Manager, WorkspaceId
from app.api.errors import not_implemented
from app.api.v1.schemas import Analytics

router = APIRouter(tags=["analytics"])


@router.get("/workspaces/{workspaceId}/analytics", response_model=Analytics, summary="Everything the analytics screen needs")
async def get_analytics(
    workspace_id: WorkspaceId,
    member: Manager,
    from_: Annotated[Optional[date], Query(alias="from", description="Workspace-local date, inclusive. Default: to - 29 days")] = None,
    to: Annotated[Optional[date], Query(description="Workspace-local date, inclusive. Default: today")] = None,
):
    raise not_implemented()
