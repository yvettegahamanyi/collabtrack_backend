from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GroupMemberRole, User
from app.services.group_members import add_member_if_missing
from app.services.meeting_parser import MemberRow
from app.services.user_provisioning import get_or_create_student


async def provision_members_from_attendance(
    db: AsyncSession,
    *,
    group_id: str,
    instructor_id: str,
    rows: list[MemberRow],
) -> int:
    """Provision students from member rows and add them to the group."""
    added = 0
    for row in rows:
        student = await get_or_create_student(
            db,
            email=row.email,
            name=row.name,
            instructor_id=instructor_id,
            moodle_user_id=row.moodle_user_id,
        )
        membership = await add_member_if_missing(
            db,
            group_id=group_id,
            user_id=student.id,
            role=GroupMemberRole.STUDENT,
        )
        if membership is not None:
            added += 1
    return added
