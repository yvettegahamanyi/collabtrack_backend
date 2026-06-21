import os

from dotenv import load_dotenv
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

load_dotenv()

DEFAULT_STUDENT_PASSWORD = os.getenv(
    "DEFAULT_STUDENT_PASSWORD", "ChangeMeOnFirstLogin123!"
)

from app.core.security import hash_password
from app.models import AccountStatus, RoleType, User


async def get_or_create_student(
    db: AsyncSession,
    *,
    email: str,
    name: str,
    instructor_id: str,
) -> User:
    """Return an existing student or auto-provision a pending one."""
    normalized_email = email.lower().strip()
    user = await db.scalar(select(User).where(User.email == normalized_email))
    if user is not None:
        if user.role != RoleType.STUDENT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User exists but is not a student.",
            )
        return user

    user = User(
        email=normalized_email,
        name=name.strip(),
        password_hash=hash_password(DEFAULT_STUDENT_PASSWORD),
        role=RoleType.STUDENT,
        account_status=AccountStatus.PENDING,
        has_logged_in=False,
        must_change_password=True,
        provisioned_by_instructor_id=instructor_id,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user
