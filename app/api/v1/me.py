from fastapi import APIRouter

from app.api.deps import CurrentUser, Db
from app.api.v1.schemas import Me
from app.services import workspaces as service

router = APIRouter(tags=["me"])


@router.get("/me", response_model=Me, summary="Current user, their workspaces and onboarding state")
def get_me(user: CurrentUser, db: Db):
    return {
        "user": {"id": user.id, "email": user.email or "", "name": user.name or "", "image": user.image},
        "workspaces": service.workspaces_for_user(db, user.id),
    }
