from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas.user import UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/me",
    response_model=UserOut,
    summary="Get my profile (whoami)",
    responses={
        200: {"description": "The authenticated user's profile."},
        401: {"description": "Missing or invalid token."},
        403: {"description": "Account deactivated."},
    },
)
async def whoami(current_user: User = Depends(get_current_user)):
    """Return the authenticated user's profile."""
    return current_user


@router.patch(
    "/me",
    response_model=UserOut,
    summary="Update my profile / onboarding",
    responses={
        200: {"description": "Updated profile."},
        401: {"description": "Missing or invalid token."},
        403: {"description": "Account deactivated."},
        422: {"description": "Invalid role (must be STUDENT or INSTRUCTOR)."},
    },
)
async def update_me(
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the authenticated user's profile.

    Primarily used during onboarding to set the role (`STUDENT` / `INSTRUCTOR`)
    and display name. Only the provided fields are changed.
    """
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(current_user, field, value)
    db.add(current_user)
    return current_user
