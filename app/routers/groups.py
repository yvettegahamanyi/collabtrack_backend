from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.security import generate_invite_token
from app.database import get_db
from app.dependencies import get_current_user
from app.models import (
    GroupInvitation,
    GroupMemberRole,
    GroupMembership,
    ProjectGroup,
    RoleType,
    User,
)
from app.schemas.group import (
    GroupCreate,
    GroupOut,
    GroupUpdate,
    InviteCreate,
    InviteOut,
    MemberOut,
)
from app.schemas.response import ApiResponse, success
from app.services.groups import (
    get_group_or_404,
    get_membership,
    require_membership,
    require_owner,
    require_owner_or_instructor,
)

router = APIRouter(prefix="/groups", tags=["groups"])


@router.post(
    "",
    response_model=ApiResponse[GroupOut],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new project group",
    responses={403: {"description": "Only students can create groups."}},
)
async def create_group(
    payload: GroupCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a group. The authenticated student becomes the group owner."""
    if current_user.role != RoleType.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can create groups.",
        )

    group = ProjectGroup(
        group_name=payload.group_name,
        description=payload.description,
        assignment_status=payload.assignment_status,
        owner_id=current_user.id,
    )
    db.add(group)
    await db.flush()

    membership = GroupMembership(
        group_id=group.id,
        user_id=current_user.id,
        role=GroupMemberRole.STUDENT,
    )
    db.add(membership)
    return success(
        data=GroupOut.model_validate(group),
        message="Group created successfully.",
        code=status.HTTP_201_CREATED,
    )


@router.get(
    "",
    response_model=ApiResponse[list[GroupOut]],
    summary="List my groups",
)
async def list_my_groups(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all groups the current user belongs to."""
    result = await db.scalars(
        select(ProjectGroup)
        .join(GroupMembership, GroupMembership.group_id == ProjectGroup.id)
        .where(GroupMembership.user_id == current_user.id)
        .order_by(ProjectGroup.created_at.desc())
    )
    groups = [GroupOut.model_validate(group) for group in result.all()]
    return success(
        data=groups,
        message="Groups retrieved successfully.",
    )


@router.get(
    "/{group_id}",
    response_model=ApiResponse[GroupOut],
    summary="Get group details",
    responses={403: {"description": "Not a member of this group."}},
)
async def get_group(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_membership(group_id, current_user, db)
    return success(
        data=GroupOut.model_validate(group),
        message="Group retrieved successfully.",
    )


@router.put(
    "/{group_id}",
    response_model=ApiResponse[GroupOut],
    summary="Update a group",
    responses={403: {"description": "Only the group owner can update."}},
)
async def update_group(
    group_id: str,
    payload: GroupUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner(group, current_user)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    db.add(group)
    return success(
        data=GroupOut.model_validate(group),
        message="Group updated successfully.",
    )


@router.delete(
    "/{group_id}",
    response_model=ApiResponse[None],
    summary="Delete a group",
    responses={403: {"description": "Only the group owner can delete."}},
)
async def delete_group(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a group and all related memberships, assets, and reports."""
    group = await get_group_or_404(group_id, db)
    await require_owner(group, current_user)
    await db.delete(group)
    return success(
        message="Group deleted successfully.",
    )


@router.post(
    "/{group_id}/invite",
    response_model=ApiResponse[InviteOut],
    status_code=status.HTTP_201_CREATED,
    summary="Generate a shareable invite link",
    responses={
        403: {"description": "Only owner or instructor can invite."},
    },
)
async def create_invite(
    group_id: str,
    payload: InviteCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner_or_instructor(group, current_user, db)

    raw_token, token_hash = generate_invite_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        hours=payload.expires_in_hours
    )
    invitation = GroupInvitation(
        group_id=group.id,
        token_hash=token_hash,
        role=payload.role,
        expires_at=expires_at,
        created_by_id=current_user.id,
    )
    db.add(invitation)

    invite_url = f"{settings.frontend_url.rstrip('/')}/invite/{raw_token}"
    return success(
        data=InviteOut(
            token=raw_token,
            invite_url=invite_url,
            role=payload.role,
            expires_at=expires_at,
            group_id=group.id,
        ),
        message="Invite link created successfully.",
        code=status.HTTP_201_CREATED,
    )


@router.get(
    "/{group_id}/members",
    response_model=ApiResponse[list[MemberOut]],
    summary="List group members",
    responses={403: {"description": "Not a member of this group."}},
)
async def list_members(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_membership(group_id, current_user, db)

    result = await db.scalars(
        select(GroupMembership)
        .where(GroupMembership.group_id == group_id)
        .options(selectinload(GroupMembership.user))
        .order_by(GroupMembership.joined_at.asc())
    )
    memberships = list(result.all())
    members = [
        MemberOut(
            user_id=m.user_id,
            name=m.user.name,
            email=m.user.email,
            role=m.role,
            is_owner=m.user_id == group.owner_id,
            joined_at=m.joined_at,
        )
        for m in memberships
    ]
    return success(
        data=members,
        message="Group members retrieved successfully.",
    )


@router.delete(
    "/{group_id}/members/{user_id}",
    response_model=ApiResponse[None],
    summary="Remove a member from a group",
    responses={
        403: {"description": "Not authorized or cannot remove owner."},
        404: {"description": "Member not found in group."},
    },
)
async def remove_member(
    group_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner_or_instructor(group, current_user, db)

    if user_id == group.owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot remove the group owner.",
        )

    membership = await get_membership(group_id, user_id, db)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this group.",
        )

    await db.execute(
        delete(GroupMembership).where(GroupMembership.id == membership.id)
    )
    return success(
        message="Member removed successfully.",
    )
