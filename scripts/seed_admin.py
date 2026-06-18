"""Seed (or update) the admin account.

Reads ADMIN_EMAIL / ADMIN_PASSWORD / ADMIN_NAME from the environment (.env)
and ensures a single active ADMIN user exists.

Run from the project root:

    python -m scripts.seed_admin
"""

import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv()

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@collabtrack.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")
ADMIN_NAME = os.getenv("ADMIN_NAME", "CollabTrack Admin")

from app.core.security import hash_password
from app.database import AsyncSessionLocal, engine
from app.models import RoleType, User


async def seed_admin() -> None:
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(
            select(User).where(User.email == ADMIN_EMAIL)
        )

        if existing is not None:
            existing.role = RoleType.ADMIN
            existing.is_active = True
            existing.password_hash = hash_password(ADMIN_PASSWORD)
            existing.name = existing.name or ADMIN_NAME
            await session.commit()
            print(f"Updated existing admin: {ADMIN_EMAIL}")
        else:
            admin = User(
                email=ADMIN_EMAIL,
                name=ADMIN_NAME,
                password_hash=hash_password(ADMIN_PASSWORD),
                role=RoleType.ADMIN,
                is_active=True,
            )
            session.add(admin)
            await session.commit()
            print(f"Created admin: {ADMIN_EMAIL}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_admin())
