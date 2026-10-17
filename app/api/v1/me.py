from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.api.errors import not_implemented
from app.api.v1.schemas import Me

router = APIRouter(tags=["me"])


@router.get("/me", response_model=Me, summary="Current user, their workspaces and onboarding state")
async def get_me(user: CurrentUser):
    raise not_implemented()
