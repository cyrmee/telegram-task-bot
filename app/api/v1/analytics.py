from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Query

from app.api.deps import Db, Manager, WorkspaceId
from app.api.v1.schemas import Analytics
from app.services import analytics as service

router = APIRouter(tags=["analytics"])


@router.get("/workspaces/{workspaceId}/analytics", response_model=Analytics, summary="Everything the analytics screen needs")
def get_analytics(
    workspace_id: WorkspaceId,
    member: Manager,
    db: Db,
    from_: Annotated[Optional[date], Query(alias="from", description="Workspace-local date, inclusive. Default: to - 29 days")] = None,
    to: Annotated[Optional[date], Query(description="Workspace-local date, inclusive. Default: today")] = None,
):
    return service.get_analytics(db, workspace_id, from_, to)
