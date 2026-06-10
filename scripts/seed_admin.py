"""Seed (or update) the admin account.

Reads ADMIN_EMAIL / ADMIN_PASSWORD / ADMIN_NAME from the environment (.env)
and ensures a single active ADMIN user exists.

Run from the project root:

    python -m scripts.seed_admin
"""

import asyncio

from sqlalchemy import select

from app.config import settings
from app.core.security import hash_password
from app.database import AsyncSessionLocal, engine
from app.models import RoleType, User


async def seed_admin() -> None:
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(
            select(User).where(User.email == settings.admin_email)
        )

        if existing is not None:
            existing.role = RoleType.ADMIN
            existing.is_active = True
            existing.password_hash = hash_password(settings.admin_password)
            existing.name = existing.name or settings.admin_name
            await session.commit()
            print(f"Updated existing admin: {settings.admin_email}")
        else:
            admin = User(
                email=settings.admin_email,
                name=settings.admin_name,
                password_hash=hash_password(settings.admin_password),
                role=RoleType.ADMIN,
                is_active=True,
            )
            session.add(admin)
            await session.commit()
            print(f"Created admin: {settings.admin_email}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_admin())
