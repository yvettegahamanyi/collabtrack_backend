"""Seed instructor and student accounts for Playwright E2E tests.

Reads E2E_* env vars (with sensible defaults) and ensures both users exist
with the correct roles.

Run from the project root:

    python -m scripts.seed_e2e_users
"""

import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv()

INSTRUCTOR_EMAIL = os.getenv("E2E_INSTRUCTOR_EMAIL", "e2e.instructor@example.com")
INSTRUCTOR_PASSWORD = os.getenv("E2E_INSTRUCTOR_PASSWORD", "E2ETest123!")
INSTRUCTOR_NAME = os.getenv("E2E_INSTRUCTOR_NAME", "E2E Instructor")

STUDENT_EMAIL = os.getenv("E2E_STUDENT_EMAIL", "e2e.student@example.com")
STUDENT_PASSWORD = os.getenv("E2E_STUDENT_PASSWORD", "E2ETest123!")
STUDENT_NAME = os.getenv("E2E_STUDENT_NAME", "E2E Student")

from app.core.security import hash_password
from app.database import AsyncSessionLocal, engine
from app.models import RoleType, User


async def _upsert_user(
    session,
    *,
    email: str,
    password: str,
    name: str,
    role: RoleType,
) -> None:
    existing = await session.scalar(select(User).where(User.email == email))
    password_hash = hash_password(password)

    if existing is not None:
        existing.name = name
        existing.role = role
        existing.is_active = True
        existing.password_hash = password_hash
        print(f"Updated {role.value.lower()}: {email}")
        return

    session.add(
        User(
            email=email,
            name=name,
            password_hash=password_hash,
            role=role,
            is_active=True,
        )
    )
    print(f"Created {role.value.lower()}: {email}")


async def seed_e2e_users() -> None:
    async with AsyncSessionLocal() as session:
        await _upsert_user(
            session,
            email=INSTRUCTOR_EMAIL,
            password=INSTRUCTOR_PASSWORD,
            name=INSTRUCTOR_NAME,
            role=RoleType.INSTRUCTOR,
        )
        await _upsert_user(
            session,
            email=STUDENT_EMAIL,
            password=STUDENT_PASSWORD,
            name=STUDENT_NAME,
            role=RoleType.STUDENT,
        )
        await session.commit()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_e2e_users())
