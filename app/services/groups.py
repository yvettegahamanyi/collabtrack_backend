from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import GroupInvitation, GroupMemberRole, GroupMembership, ProjectGroup, User


async def get_group_or_404(group_id: str, db: AsyncSession) -> ProjectGroup:
    group = await db.get(ProjectGroup, group_id)
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found.",
        )
    return group


async def get_membership(
    group_id: str, user_id: str, db: AsyncSession
) -> GroupMembership | None:
    return await db.scalar(
        select(GroupMembership).where(
            GroupMembership.group_id == group_id,
            GroupMembership.user_id == user_id,
        )
    )


async def require_membership(
    group_id: str, user: User, db: AsyncSession
) -> GroupMembership:
    membership = await get_membership(group_id, user.id, db)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this group.",
        )
    return membership


async def require_owner(group: ProjectGroup, user: User) -> None:
    if group.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the group owner can perform this action.",
        )


async def require_owner_or_instructor(
    group: ProjectGroup, user: User, db: AsyncSession
) -> GroupMembership:
    membership = await require_membership(group.id, user, db)
    if group.owner_id == user.id:
        return membership
    if membership.role == GroupMemberRole.INSTRUCTOR:
        return membership
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only the group owner or an instructor can perform this action.",
    )


async def get_invitation_by_token(
    token: str, db: AsyncSession
) -> GroupInvitation | None:
    from app.core.security import hash_invite_token

    token_hash = hash_invite_token(token)
    return await db.scalar(
        select(GroupInvitation)
        .where(GroupInvitation.token_hash == token_hash)
        .options(selectinload(GroupInvitation.group))
    )
