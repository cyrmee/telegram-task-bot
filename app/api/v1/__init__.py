"""/api/v1 routers. Mounted by the app factory (A1): app.include_router(api_router)."""
from fastapi import APIRouter

from app.api.v1 import analytics, me, members, projects, tasks, workspaces
from app.api.v1.schemas import ErrorResponse

COMMON_ERRORS = {
    401: {"model": ErrorResponse, "description": "`unauthenticated`: missing, invalid or expired JWT"},
    403: {"model": ErrorResponse, "description": "`forbidden`: role too low"},
    404: {"model": ErrorResponse, "description": "`not_found`: no such resource in this workspace (or not a member)"},
    422: {"model": ErrorResponse, "description": "`validation_error`"},
}

api_router = APIRouter(prefix="/api/v1", responses=COMMON_ERRORS)
for module in (me, workspaces, members, projects, tasks, analytics):
    api_router.include_router(module.router)
