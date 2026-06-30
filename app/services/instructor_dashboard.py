from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Assignment,
    ContributionReport,
    CourseClass,
    GroupMembership,
    MemberParticipationScore,
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
    TeamClassificationCountOut,
)
from app.services.participation_scoring import (
    ParticipationScoreResult,
    _predict_team_archetype_for_scores,
)
from app.services.team_cluster_model import (
    TeamClusterModelUnavailableError,
    get_team_cluster_model_bundle,
)

_RECENT_REPORT_LIMIT = 5
_TEAM_ARCHETYPE_ORDER = (
    "balanced_team",
    "high_performing_cohesive",
    "one_dominant_contributor",
    "uniformly_disengaged",
)
_FALLBACK_ARCHETYPE_LABELS = {
    "balanced_team": "Balanced team",
    "one_dominant_contributor": "One dominant contributor",
    "uniformly_disengaged": "Uniformly disengaged team",
    "high_performing_cohesive": "High-performing cohesive team",
}


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
        team_classifications=[],
        classified_group_count=0,
        unclassified_group_count=0,
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


def _archetype_labels() -> dict[str, str]:
    try:
        bundle = get_team_cluster_model_bundle()
        return dict(bundle.archetype_labels)
    except TeamClusterModelUnavailableError:
        return dict(_FALLBACK_ARCHETYPE_LABELS)


async def _team_classification_counts(
    groups: list[ProjectGroup], db: AsyncSession
) -> tuple[list[TeamClassificationCountOut], int, int]:
    if not groups:
        return [], 0, 0

    groups_with_scores = [
        group for group in groups if group.participation_scores_generated_at is not None
    ]
    if not groups_with_scores:
        return [], 0, len(groups)

    group_ids = [group.id for group in groups_with_scores]
    records = await db.scalars(
        select(MemberParticipationScore).where(
            MemberParticipationScore.group_id.in_(group_ids)
        )
    )

    scores_by_group: dict[str, list[ParticipationScoreResult]] = {}
    for row in records.all():
        scores_by_group.setdefault(row.group_id, []).append(
            ParticipationScoreResult(
                user_id=row.user_id,
                name=None,
                predicted_score=row.predicted_score,
                contributor_tier=row.contributor_tier,
                features=dict(row.features or {}),
                generated_at=row.generated_at,
            )
        )

    counts: dict[str, int] = {}
    classified_group_count = 0
    for group in groups_with_scores:
        scores = scores_by_group.get(group.id, [])
        if not scores:
            continue
        archetype = _predict_team_archetype_for_scores(scores)
        if archetype is None:
            continue
        counts[archetype.archetype] = counts.get(archetype.archetype, 0) + 1
        classified_group_count += 1

    labels = _archetype_labels()
    team_classifications = [
        TeamClassificationCountOut(
            archetype=archetype,
            label=labels.get(archetype, archetype.replace("_", " ").title()),
            count=counts[archetype],
        )
        for archetype in _TEAM_ARCHETYPE_ORDER
        if counts.get(archetype, 0) > 0
    ]
    unclassified_group_count = len(groups) - classified_group_count
    return team_classifications, classified_group_count, unclassified_group_count


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
            team_classifications=[],
            classified_group_count=0,
            unclassified_group_count=0,
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

    team_classifications, classified_group_count, unclassified_group_count = (
        await _team_classification_counts(groups, db)
    )

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
        team_classifications=team_classifications,
        classified_group_count=classified_group_count,
        unclassified_group_count=unclassified_group_count,
    )
