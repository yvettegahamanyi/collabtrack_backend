import asyncio
import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status
from pylti1p3.assignments_grades import AssignmentsGradesService, TAssignmentsGradersData
from pylti1p3.exception import LtiException, LtiServiceException
from pylti1p3.grade import Grade
from pylti1p3.service_connector import ServiceConnector
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    GroupMemberRole,
    GroupMembership,
    MemberParticipationScore,
    MoodleActivityLink,
    ProjectGroup,
    User,
)
from app.services.lti.config import (
    LTI_AGS_DEFAULT_SCORE_MAXIMUM,
    LTI_CLAIM_AGS,
    get_moodle_registration,
    lti_is_configured,
)
from app.services.moodle_client import MoodleClientError, get_users_by_field, moodle_ws_configured

logger = logging.getLogger(__name__)

AGS_SCORE_SCOPE = "https://purl.imsglobal.org/spec/lti-ags/scope/score"


def extract_ags_endpoint(launch_data: dict) -> dict | None:
    endpoint = launch_data.get(LTI_CLAIM_AGS)
    if not isinstance(endpoint, dict):
        return None
    if not endpoint.get("lineitem") and not endpoint.get("lineitems"):
        return None
    return endpoint


def activity_link_has_ags(link: MoodleActivityLink | None) -> bool:
    if link is None:
        return False
    scopes = link.ags_scopes or []
    return bool(link.ags_lineitem_url and AGS_SCORE_SCOPE in scopes)


def apply_ags_endpoint_to_activity_link(
    link: MoodleActivityLink,
    endpoint: dict,
) -> None:
    link.ags_lineitem_url = endpoint.get("lineitem")
    link.ags_lineitems_url = endpoint.get("lineitems")
    scopes = endpoint.get("scope")
    link.ags_scopes = list(scopes) if isinstance(scopes, list) else None


def _build_ags_service(link: MoodleActivityLink) -> AssignmentsGradesService:
    if not link.ags_lineitem_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Moodle grade passback is not configured for this activity. "
                "Enable Assignment and Grade Services on the Moodle external tool, "
                "then launch CollabTrack again from Moodle."
            ),
        )
    scopes = link.ags_scopes or []
    if AGS_SCORE_SCOPE not in scopes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Moodle did not grant the score scope for grade passback. "
                "Enable the score scope in the tool's LTI Advantage services."
            ),
        )

    service_data: TAssignmentsGradersData = {
        "scope": scopes,  # type: ignore[typeddict-item]
        "lineitem": link.ags_lineitem_url,
    }
    if link.ags_lineitems_url:
        service_data["lineitems"] = link.ags_lineitems_url

    registration = get_moodle_registration()
    connector = ServiceConnector(registration)
    return AssignmentsGradesService(connector, service_data)


def _resolve_score_maximum(link: MoodleActivityLink, ags: AssignmentsGradesService) -> float:
    if link.ags_score_maximum and link.ags_score_maximum > 0:
        return float(link.ags_score_maximum)

    try:
        lineitem = ags.get_lineitem()
        maximum = lineitem.get_score_maximum()
        if maximum and maximum > 0:
            return float(maximum)
    except (LtiException, LtiServiceException) as exc:
        logger.warning("Could not fetch Moodle line item scoreMaximum: %s", exc)

    return LTI_AGS_DEFAULT_SCORE_MAXIMUM


async def _resolve_moodle_user_id(db: AsyncSession, user: User) -> str | None:
    if user.moodle_user_id:
        return str(user.moodle_user_id)

    if not moodle_ws_configured():
        return None

    try:
        matches = await get_users_by_field("email", [user.email])
    except MoodleClientError as exc:
        logger.warning("Moodle lookup failed for %s: %s", user.email, exc)
        return None

    if not matches:
        return None

    moodle_id = str(matches[0].get("id", "")).strip()
    if not moodle_id:
        return None

    user.moodle_user_id = moodle_id
    db.add(user)
    await db.flush()
    return moodle_id


def _build_grade_comment(score: MemberParticipationScore) -> str:
    rationale = score.llm_rationale or {}
    reasoning = str(rationale.get("reasoning") or "").strip()
    tier = score.contributor_tier
    parts = [f"CollabTrack tier: {tier}."]
    if reasoning:
        parts.append(reasoning[:400])
    return " ".join(parts)[:500]


def _put_grade_sync(
    ags: AssignmentsGradesService,
    *,
    moodle_user_id: str,
    score_given: float,
    score_maximum: float,
    comment: str,
) -> None:
    grade = (
        Grade()
        .set_user_id(moodle_user_id)
        .set_score_given(round(score_given, 2))
        .set_score_maximum(score_maximum)
        .set_activity_progress("Completed")
        .set_grading_progress("FullyGraded")
        .set_timestamp(datetime.now(timezone.utc).isoformat())
        .set_comment(comment)
    )
    ags.put_grade(grade)


async def get_activity_link_for_assignment(
    db: AsyncSession,
    assignment_id: str,
) -> MoodleActivityLink | None:
    return await db.scalar(
        select(MoodleActivityLink).where(
            MoodleActivityLink.assignment_id == assignment_id
        )
    )


async def sync_group_scores_to_moodle(
    db: AsyncSession,
    *,
    group: ProjectGroup,
    assignment_id: str,
) -> dict:
    if not lti_is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LTI is not configured on this server.",
        )

    link = await get_activity_link_for_assignment(db, assignment_id)
    if not activity_link_has_ags(link):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Moodle grade passback is unavailable. Enable Assignment and Grade "
                "Services on the Moodle external tool, then launch CollabTrack from "
                "Moodle again to register the grade endpoints."
            ),
        )

    assert link is not None

    scores = await db.scalars(
        select(MemberParticipationScore)
        .where(MemberParticipationScore.group_id == group.id)
        .options(selectinload(MemberParticipationScore.user))
        .order_by(MemberParticipationScore.generated_at.desc())
    )
    score_rows = list(scores.all())
    if not score_rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Generate participation scores before syncing grades to Moodle.",
        )

    latest_by_user: dict[str, MemberParticipationScore] = {}
    for row in score_rows:
        latest_by_user.setdefault(row.user_id, row)

    member_ids = set(
        await db.scalars(
            select(GroupMembership.user_id).where(
                GroupMembership.group_id == group.id,
                GroupMembership.role == GroupMemberRole.STUDENT,
            )
        )
    )

    ags = _build_ags_service(link)
    score_maximum = await asyncio.to_thread(_resolve_score_maximum, link, ags)
    link.ags_score_maximum = score_maximum

    results: list[dict] = []
    synced_count = 0
    failed_count = 0
    skipped_count = 0

    for user_id, score_row in latest_by_user.items():
        if user_id not in member_ids:
            continue

        user = score_row.user
        student_name = user.name or user.email
        moodle_user_id = await _resolve_moodle_user_id(db, user)
        if not moodle_user_id:
            skipped_count += 1
            results.append(
                {
                    "user_id": user.id,
                    "student_name": student_name,
                    "moodle_user_id": None,
                    "score_given": None,
                    "status": "skipped",
                    "message": "Could not resolve Moodle user id for this student.",
                }
            )
            continue

        score_given = round(float(score_row.predicted_score) * score_maximum, 2)
        comment = _build_grade_comment(score_row)

        try:
            await asyncio.to_thread(
                _put_grade_sync,
                ags,
                moodle_user_id=moodle_user_id,
                score_given=score_given,
                score_maximum=score_maximum,
                comment=comment,
            )
            synced_count += 1
            results.append(
                {
                    "user_id": user.id,
                    "student_name": student_name,
                    "moodle_user_id": moodle_user_id,
                    "score_given": score_given,
                    "status": "synced",
                    "message": None,
                }
            )
        except (LtiException, LtiServiceException) as exc:
            failed_count += 1
            logger.warning(
                "Moodle grade passback failed for user %s: %s", user.id, exc
            )
            results.append(
                {
                    "user_id": user.id,
                    "student_name": student_name,
                    "moodle_user_id": moodle_user_id,
                    "score_given": score_given,
                    "status": "failed",
                    "message": str(exc),
                }
            )

    link.last_grade_sync_at = datetime.now(timezone.utc)
    db.add(link)
    await db.commit()

    return {
        "group_id": group.id,
        "assignment_id": assignment_id,
        "score_maximum": score_maximum,
        "synced_count": synced_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "results": results,
        "synced_at": link.last_grade_sync_at,
    }
