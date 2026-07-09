from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_admin
from app.models import Assignment, CourseClass, ProjectGroup, User
from app.schemas.admin import AdminStatsOut
from app.schemas.response import ApiResponse, success
from app.schemas.user import UserOut

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_admin)],
    responses={
        401: {"description": "Missing or invalid token."},
        403: {"description": "Admin privileges required."},
    },
)


async def _get_user_or_404(user_id: str, db: AsyncSession) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    return user


@router.get(
    "/stats",
    response_model=ApiResponse[AdminStatsOut],
    summary="Platform statistics",
)
async def get_admin_stats(db: AsyncSession = Depends(get_db)):
    """Return aggregate counts for users, reports, classes, and assignments."""
    user_count = (
        await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.is_sandbox.is_(False))
        )
        or 0
    )
    active_user_count = (
        await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.is_active.is_(True), User.is_sandbox.is_(False))
        )
        or 0
    )
    report_count = (
        await db.scalar(
            select(func.count())
            .select_from(ProjectGroup)
            .where(ProjectGroup.assignment_id.is_not(None))
        )
        or 0
    )
    class_count = await db.scalar(select(func.count()).select_from(CourseClass)) or 0
    assignment_count = (
        await db.scalar(select(func.count()).select_from(Assignment)) or 0
    )
    return success(
        data=AdminStatsOut(
            user_count=user_count,
            active_user_count=active_user_count,
            report_count=report_count,
            class_count=class_count,
            assignment_count=assignment_count,
        ),
        message="Admin statistics retrieved successfully.",
    )


@router.get(
    "/users",
    response_model=ApiResponse[list[UserOut]],
    summary="List all users",
)
async def list_users(db: AsyncSession = Depends(get_db)):
    """Return all users, most recently created first."""
    result = await db.scalars(
        select(User)
        .where(User.is_sandbox.is_(False))
        .order_by(User.created_at.desc())
    )
    users = [UserOut.model_validate(user) for user in result.all()]
    return success(
        data=users,
        message="Users retrieved successfully.",
    )


@router.post(
    "/users/{user_id}/activate",
    response_model=ApiResponse[UserOut],
    summary="Activate a user",
    responses={404: {"description": "User not found."}},
)
async def activate_user(user_id: str, db: AsyncSession = Depends(get_db)):
    """Reactivate a user so they can log in again."""
    user = await _get_user_or_404(user_id, db)
    user.is_active = True
    db.add(user)
    return success(
        data=UserOut.model_validate(user),
        message="User activated successfully.",
    )


@router.post(
    "/users/{user_id}/deactivate",
    response_model=ApiResponse[UserOut],
    summary="Deactivate a user",
    responses={
        400: {"description": "Cannot deactivate your own admin account."},
        404: {"description": "User not found."},
    },
)
async def deactivate_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Deactivate a user. They can no longer log in and existing tokens are rejected."""
    user = await _get_user_or_404(user_id, db)
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own admin account.",
        )
    user.is_active = False
    db.add(user)
    return success(
        data=UserOut.model_validate(user),
        message="User deactivated successfully.",
    )
