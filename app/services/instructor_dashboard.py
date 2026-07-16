from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Assignment,
    ContributionReport,
    CourseClass,
    GroupMembership,
    ProjectGroup,
    ReportStatus,
    ServiceType,
    User,
)
from app.schemas.instructor_dashboard import (
    DashboardAssignmentActionOut,
    DashboardReportRowOut,
    DashboardSummaryOut,
    InstructorDashboardOut,
)

_RECENT_REPORT_LIMIT = 5


def _empty_dashboard() -> InstructorDashboardOut:
    summary = DashboardSummaryOut(
        class_count=0,
        active_assignment_count=0,
        reports_ready_count=0,
        failed_report_count=0,
    )
    return InstructorDashboardOut(
        summary=summary,
        recent_reports=[],
        all_reports=[],
        failed_reports=[],
        assignments_without_reports=[],
    )


async def _member_counts_by_group(
    group_ids: list[str], db: AsyncSession
) -> dict[str, int]:
    if not group_ids:
        return {}
    rows = await db.execute(
        select(GroupMembership.group_id, func.count())
        .where(GroupMembership.group_id.in_(group_ids))
        .group_by(GroupMembership.group_id)
    )
    return {group_id: count for group_id, count in rows.all()}


async def _notification_sent_by_group(
    group_ids: list[str], db: AsyncSession
) -> dict[str, datetime | None]:
    if not group_ids:
        return {}
    rows = await db.execute(
        select(ContributionReport.group_id, ContributionReport.notification_sent_at)
        .where(ContributionReport.group_id.in_(group_ids))
        .order_by(
            ContributionReport.group_id.asc(),
            ContributionReport.generated_at.desc(),
        )
    )
    result: dict[str, datetime | None] = {}
    for group_id, notification_sent_at in rows.all():
        if group_id not in result:
            result[group_id] = notification_sent_at
    return result


async def get_instructor_dashboard(
    user: User, db: AsyncSession
) -> InstructorDashboardOut:
    classes = (
        await db.scalars(
            select(CourseClass)
            .where(CourseClass.instructor_id == user.id)
            .order_by(CourseClass.created_at.desc())
        )
    ).all()

    if not classes:
        return _empty_dashboard()

    class_ids = [course_class.id for course_class in classes]
    class_name_by_id = {course_class.id: course_class.name for course_class in classes}

    assignments = (
        await db.scalars(
            select(Assignment)
            .where(Assignment.class_id.in_(class_ids))
            .options(selectinload(Assignment.course_class))
            .order_by(Assignment.created_at.desc())
        )
    ).all()

    active_assignment_count = sum(
        1 for assignment in assignments if assignment.status == ServiceType.ACTIVE
    )

    assignment_ids = [assignment.id for assignment in assignments]
    assignments_without_reports: list[DashboardAssignmentActionOut] = []

    if not assignment_ids:
        return InstructorDashboardOut(
            summary=DashboardSummaryOut(
                class_count=len(classes),
                active_assignment_count=active_assignment_count,
                reports_ready_count=0,
                failed_report_count=0,
            ),
            recent_reports=[],
            all_reports=[],
            failed_reports=[],
            assignments_without_reports=[],
        )

    report_counts = await db.execute(
        select(ProjectGroup.assignment_id, func.count())
        .where(ProjectGroup.assignment_id.in_(assignment_ids))
        .group_by(ProjectGroup.assignment_id)
    )
    report_count_by_assignment = {
        assignment_id: count for assignment_id, count in report_counts.all()
    }

    for assignment in assignments:
        if report_count_by_assignment.get(assignment.id, 0) == 0:
            assignments_without_reports.append(
                DashboardAssignmentActionOut(
                    id=assignment.id,
                    title=assignment.title,
                    class_id=assignment.class_id,
                    class_name=class_name_by_id.get(
                        assignment.class_id, assignment.course_class.name
                    ),
                    status=assignment.status,
                )
            )

    groups = (
        await db.scalars(
            select(ProjectGroup)
            .where(ProjectGroup.assignment_id.in_(assignment_ids))
            .order_by(ProjectGroup.created_at.desc())
        )
    ).all()

    assignment_by_id = {assignment.id: assignment for assignment in assignments}
    group_ids = [group.id for group in groups]
    member_counts = await _member_counts_by_group(group_ids, db)
    notifications = await _notification_sent_by_group(group_ids, db)

    all_reports: list[DashboardReportRowOut] = []
    reports_ready_count = 0
    failed_report_count = 0

    for group in groups:
        assignment = assignment_by_id.get(group.assignment_id or "")
        if assignment is None:
            continue

        status = group.report_status
        if status == ReportStatus.READY:
            reports_ready_count += 1
        elif status == ReportStatus.FAILED:
            failed_report_count += 1

        all_reports.append(
            DashboardReportRowOut(
                group_id=group.id,
                group_name=group.group_name,
                group_number=group.group_number,
                assignment_id=assignment.id,
                assignment_title=assignment.title,
                class_id=assignment.class_id,
                class_name=class_name_by_id.get(
                    assignment.class_id, assignment.course_class.name
                ),
                report_status=status,
                created_at=group.created_at,
                notification_sent_at=notifications.get(group.id),
                member_count=member_counts.get(group.id, 0),
            )
        )

    all_reports.sort(key=lambda row: row.created_at, reverse=True)
    recent_reports = all_reports[:_RECENT_REPORT_LIMIT]
    failed_reports = [
        row for row in all_reports if row.report_status == ReportStatus.FAILED
    ]

    summary = DashboardSummaryOut(
        class_count=len(classes),
        active_assignment_count=active_assignment_count,
        reports_ready_count=reports_ready_count,
        failed_report_count=failed_report_count,
    )

    return InstructorDashboardOut(
        summary=summary,
        recent_reports=recent_reports,
        all_reports=all_reports,
        failed_reports=failed_reports,
        assignments_without_reports=assignments_without_reports,
    )
