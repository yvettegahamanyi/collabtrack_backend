from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ContributionReport,
    GroupGithubRepo,
    GroupGoogleDoc,
    ProjectGroup,
    User,
)
from app.schemas.report import AssignmentReportOut


async def serialize_assignment_reports(
    assignment_id: str, db: AsyncSession
) -> list[AssignmentReportOut]:
    groups = await db.scalars(
        select(ProjectGroup)
        .where(ProjectGroup.assignment_id == assignment_id)
        .order_by(ProjectGroup.group_number.asc())
    )
    reports: list[AssignmentReportOut] = []
    for group in groups.all():
        contribution = await db.scalar(
            select(ContributionReport)
            .where(ContributionReport.group_id == group.id)
            .order_by(ContributionReport.generated_at.desc())
            .limit(1)
        )
        repo_count = await db.scalar(
            select(func.count())
            .select_from(GroupGithubRepo)
            .where(GroupGithubRepo.group_id == group.id)
        )
        doc_count = await db.scalar(
            select(func.count())
            .select_from(GroupGoogleDoc)
            .where(GroupGoogleDoc.group_id == group.id)
        )
        has_resources = (repo_count or 0) + (doc_count or 0) > 0
        reports.append(
            AssignmentReportOut(
                group_id=group.id,
                group_name=group.group_name,
                group_number=group.group_number,
                assignment_id=assignment_id,
                report_status=group.report_status,
                created_at=group.created_at,
                contribution_report_id=contribution.id if contribution else None,
                contribution_report_status=contribution.status if contribution else None,
                notification_sent_at=(
                    contribution.notification_sent_at if contribution else None
                ),
                has_collaboration_resources=has_resources,
            )
        )
    return reports


async def allocate_group_number(assignment_id: str, db: AsyncSession) -> int:
    current_max = await db.scalar(
        select(func.max(ProjectGroup.group_number)).where(
            ProjectGroup.assignment_id == assignment_id
        )
    )
    return (current_max or 0) + 1
