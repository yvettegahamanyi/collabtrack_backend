"""Delete benchmark dataset rows, training collections, sandbox groups, and sandbox users."""

import asyncio

from sqlalchemy import delete, func, select

from app.database import AsyncSessionLocal
from app.models import (
    CollabTrackDataset,
    ProjectGroup,
    TrainingCollection,
    TrainingCollectionMember,
    User,
)

TRAINING_GROUP_DESCRIPTION = "Sandbox group for training data collection"
SANDBOX_EMAIL_PATTERN = "%@collabtrack.local"


async def main() -> None:
    async with AsyncSessionLocal() as db:
        dataset_count = await db.scalar(select(func.count()).select_from(CollabTrackDataset))
        member_count = await db.scalar(
            select(func.count()).select_from(TrainingCollectionMember)
        )
        collection_count = await db.scalar(
            select(func.count()).select_from(TrainingCollection)
        )

        training_groups = list(
            (
                await db.scalars(
                    select(ProjectGroup).where(
                        ProjectGroup.description == TRAINING_GROUP_DESCRIPTION
                    )
                )
            ).all()
        )
        sandbox_users = list(
            (
                await db.scalars(
                    select(User).where(User.email.like(SANDBOX_EMAIL_PATTERN))
                )
            ).all()
        )

        await db.execute(delete(TrainingCollectionMember))
        await db.execute(delete(TrainingCollection))
        await db.execute(delete(CollabTrackDataset))

        for group in training_groups:
            await db.delete(group)

        for user in sandbox_users:
            await db.delete(user)

        await db.commit()

        print(
            "Cleared training data: "
            f"{dataset_count or 0} dataset rows, "
            f"{collection_count or 0} collections, "
            f"{member_count or 0} collection members, "
            f"{len(training_groups)} sandbox groups, "
            f"{len(sandbox_users)} sandbox users."
        )


if __name__ == "__main__":
    asyncio.run(main())
