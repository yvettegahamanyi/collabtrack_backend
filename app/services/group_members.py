from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GroupMemberRole, GroupMembership, ProjectGroup, RoleType, User
from app.services.groups import get_membership, require_owner_or_instructor


async def add_member_if_missing(
    db: AsyncSession,
    *,
    group_id: str,
    user_id: str,
    role: GroupMemberRole,
) -> GroupMembership | None:
    """Add a membership row if absent. Returns None when already a member."""
    existing = await get_membership(group_id, user_id, db)
    if existing is not None:
        return None

    membership = GroupMembership(
        group_id=group_id,
        user_id=user_id,
        role=role,
    )
    db.add(membership)
    await db.flush()
    return membership


async def require_instructor_can_manage_group(
    group: ProjectGroup, user: User, db: AsyncSession
) -> GroupMembership:
    """Only instructors who own or belong to the group as instructor may manage rosters."""
    if user.role != RoleType.INSTRUCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors can add members to a group roster.",
        )
    return await require_owner_or_instructor(group, user, db)
