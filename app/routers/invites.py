from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import GroupMembership, User
from app.schemas.group import InviteAcceptData, InviteDetails
from app.schemas.response import ApiResponse, success
from app.services.groups import get_invitation_by_token

router = APIRouter(prefix="/invite", tags=["invites"])


def _ensure_invite_valid(invitation) -> None:
    if invitation is None or invitation.used:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found.",
        )
    if invitation.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Invitation has expired.",
        )


@router.get(
    "/{token}",
    response_model=ApiResponse[InviteDetails],
    summary="Validate an invite token",
    responses={
        404: {"description": "Invitation not found."},
        410: {"description": "Invitation has expired."},
    },
)
async def get_invite_details(token: str, db: AsyncSession = Depends(get_db)):
    """Public endpoint to preview a group before login/signup."""
    invitation = await get_invitation_by_token(token, db)
    _ensure_invite_valid(invitation)

    group = invitation.group
    return success(
        data=InviteDetails(
            group_id=group.id,
            group_name=group.group_name,
            description=group.description,
            role=invitation.role,
            expires_at=invitation.expires_at,
        ),
        message="Invitation details retrieved successfully.",
    )


@router.post(
    "/{token}/accept",
    response_model=ApiResponse[InviteAcceptData],
    summary="Accept an invitation",
    responses={
        409: {"description": "Already a member of this group."},
        404: {"description": "Invitation not found."},
        410: {"description": "Invitation has expired."},
    },
)
async def accept_invite(
    token: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Join a group using an invite token. Role comes from the invitation."""
    invitation = await get_invitation_by_token(token, db)
    _ensure_invite_valid(invitation)

    from app.services.groups import get_membership

    existing = await get_membership(invitation.group_id, current_user.id, db)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already a member of this group.",
        )

    membership = GroupMembership(
        group_id=invitation.group_id,
        user_id=current_user.id,
        role=invitation.role,
    )
    db.add(membership)
    invitation.used = True

    group_name = invitation.group.group_name or "the group"
    return success(
        data=InviteAcceptData(
            group_id=invitation.group_id,
            role=invitation.role,
        ),
        message=f"You have successfully joined {group_name}.",
    )
