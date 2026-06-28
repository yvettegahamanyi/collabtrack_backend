from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Assignment,
    ContributionReport,
    ContributionReportStatus,
    CourseClass,
    MeetingSession,
    MeetingSessionStatus,
    ProjectGroup,
    ReportStatus,
)
from app.services.email import send_report_ready_notification
from app.services.participation import get_contributions
from app.services.participation_scoring import try_generate_participation_scores_for_report


async def aggregate_and_save_report(
    group: ProjectGroup, assignment: Assignment, db: AsyncSession
) -> ContributionReport:
    contributions = await get_contributions(group, db)
    scores_payload = contributions.model_dump(mode="json")

    existing = await db.scalar(
        select(ContributionReport)
        .where(ContributionReport.group_id == group.id)
        .order_by(ContributionReport.generated_at.desc())
        .limit(1)
    )
    if existing is not None:
        existing.final_calculated_scores = scores_payload
        existing.status = ContributionReportStatus.READY
        existing.generated_at = datetime.now(timezone.utc)
        db.add(existing)
        report = existing
    else:
        report = ContributionReport(
            group_id=group.id,
            assignment_id=assignment.id,
            status=ContributionReportStatus.READY,
            final_calculated_scores=scores_payload,
        )
        db.add(report)

    group.report_status = ReportStatus.READY
    db.add(group)
    await db.flush()
    return report


async def _notify_instructor_report_ready(
    group: ProjectGroup,
    assignment: Assignment,
    report: ContributionReport,
    db: AsyncSession,
) -> None:
    if report.notification_sent_at is not None:
        return

    group_with_owner = await db.scalar(
        select(ProjectGroup)
        .where(ProjectGroup.id == group.id)
        .options(selectinload(ProjectGroup.owner))
    )
    if group_with_owner is None or group_with_owner.owner is None:
        return

    instructor_email = group_with_owner.owner.email
    if not instructor_email:
        return

    await send_report_ready_notification(
        to_email=instructor_email,
        assignment_title=assignment.title,
        group_name=group.group_name or f"Group {group.group_number}",
        assignment_id=assignment.id,
        group_id=group.id,
    )
    report.notification_sent_at = datetime.now(timezone.utc)
    db.add(report)


async def finalize_ready_report(
    group: ProjectGroup, assignment: Assignment, db: AsyncSession
) -> ContributionReport:
    report = await aggregate_and_save_report(group, assignment, db)
    await try_generate_participation_scores_for_report(group, db)
    await db.commit()
    try:
        await _notify_instructor_report_ready(group, assignment, report, db)
        await db.commit()
    except Exception:
        await db.rollback()
    return report


async def check_and_finalize_report(group_id: str) -> None:
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        group = await db.get(ProjectGroup, group_id)
        if group is None or group.assignment_id is None:
            return

        assignment = await db.scalar(
            select(Assignment)
            .where(Assignment.id == group.assignment_id)
            .options(
                selectinload(Assignment.course_class).selectinload(CourseClass.instructor)
            )
        )
        if assignment is None:
            return

        sessions = list(
            (
                await db.scalars(
                    select(MeetingSession).where(MeetingSession.group_id == group_id)
                )
            ).all()
        )
        if not sessions:
            return

        statuses = {session.status for session in sessions}
        if MeetingSessionStatus.FAILED in statuses:
            group.report_status = ReportStatus.FAILED
            db.add(group)
            failed_report = await db.scalar(
                select(ContributionReport)
                .where(ContributionReport.group_id == group_id)
                .order_by(ContributionReport.generated_at.desc())
                .limit(1)
            )
            if failed_report is None:
                failed_report = ContributionReport(
                    group_id=group_id,
                    assignment_id=assignment.id,
                    status=ContributionReportStatus.FAILED,
                )
                db.add(failed_report)
            else:
                failed_report.status = ContributionReportStatus.FAILED
                db.add(failed_report)
            await db.commit()
            return

        pending = {
            MeetingSessionStatus.PENDING,
            MeetingSessionStatus.UPLOADED,
            MeetingSessionStatus.NEEDS_MAPPING,
            MeetingSessionStatus.PROCESSING,
        }
        if statuses & pending:
            group.report_status = ReportStatus.PROCESSING
            db.add(group)
            await db.commit()
            return

        if statuses == {MeetingSessionStatus.COMPLETED}:
            await finalize_ready_report(group, assignment, db)


async def resend_supervisor_notification(
    group: ProjectGroup, assignment: Assignment, db: AsyncSession
) -> None:
    assignment = await db.scalar(
        select(Assignment)
        .where(Assignment.id == assignment.id)
        .options(selectinload(Assignment.course_class).selectinload(CourseClass.instructor))
    )
    if assignment is None:
        return

    report = await db.scalar(
        select(ContributionReport)
        .where(ContributionReport.group_id == group.id)
        .order_by(ContributionReport.generated_at.desc())
        .limit(1)
    )
    if report is None:
        return

    report.notification_sent_at = None
    db.add(report)
    await db.flush()
    await _notify_instructor_report_ready(group, assignment, report, db)
