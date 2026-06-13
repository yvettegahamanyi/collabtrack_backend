from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import GroupInvitation, GroupMemberRole, GroupMembership, ProjectGroup, User
from app.schemas.group import GroupDetailOut, GroupOut, MemberOut


def serialize_members(
    group: ProjectGroup, memberships: list[GroupMembership]
) -> list[MemberOut]:
    ordered = sorted(memberships, key=lambda membership: membership.joined_at)
    return [
        MemberOut(
            user_id=membership.user_id,
            name=membership.user.name,
            email=membership.user.email,
            role=membership.role,
            is_owner=membership.user_id == group.owner_id,
            joined_at=membership.joined_at,
        )
        for membership in ordered
    ]


def serialize_group_detail(
    group: ProjectGroup, memberships: list[GroupMembership]
) -> GroupDetailOut:
    return GroupDetailOut(
        **GroupOut.model_validate(group).model_dump(),
        members=serialize_members(group, memberships),
    )


async def get_group_members(
    group: ProjectGroup, db: AsyncSession
) -> list[GroupMembership]:
    result = await db.scalars(
        select(GroupMembership)
        .where(GroupMembership.group_id == group.id)
        .options(selectinload(GroupMembership.user))
        .order_by(GroupMembership.joined_at.asc())
    )
    return list(result.all())


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
