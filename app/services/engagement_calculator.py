from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    EngagementScore,
    GroupMemberRole,
    GroupMembership,
    MeetingRawMetric,
    MeetingSession,
    MeetingSessionStatus,
)


async def recalculate_group_engagement(
    group_id: str, db: AsyncSession
) -> datetime | None:
    """Recompute aggregated engagement scores from all completed sessions."""
    sessions = await db.scalars(
        select(MeetingSession).where(
            MeetingSession.group_id == group_id,
            MeetingSession.status == MeetingSessionStatus.COMPLETED,
        )
    )
    completed_sessions = list(sessions.all())
    total_sessions = len(completed_sessions)

    # #region agent log
    import json as _json, time as _time
    with open("/Users/gahamanyi/Documents/alu/CAPSTON PROJECT/.cursor/debug-ab9586.log", "a") as _f:
        _f.write(_json.dumps({"sessionId":"ab9586","hypothesisId":"H1","location":"engagement_calculator.py:recalculate","message":"completed sessions found for engagement","data":{"group_id":group_id,"total_sessions":total_sessions,"session_ids":[s.id for s in completed_sessions]},"timestamp":int(_time.time()*1000)}) + "\n")
    # #endregion

    memberships = await db.scalars(
        select(GroupMembership)
        .where(
            GroupMembership.group_id == group_id,
            GroupMembership.role == GroupMemberRole.STUDENT,
        )
        .options(selectinload(GroupMembership.user))
    )
    student_members = list(memberships.all())
    if not student_members:
        await db.execute(
            delete(EngagementScore).where(EngagementScore.group_id == group_id)
        )
        return None

    session_ids = [session.id for session in completed_sessions]
    metrics_by_session: dict[str, list[MeetingRawMetric]] = {}
    if session_ids:
        raw_metrics = await db.scalars(
            select(MeetingRawMetric).where(
                MeetingRawMetric.meeting_session_id.in_(session_ids)
            )
        )
        for metric in raw_metrics.all():
            metrics_by_session.setdefault(metric.meeting_session_id, []).append(metric)

    session_duration = {
        session.id: session.duration_minutes for session in completed_sessions
    }

    now = datetime.now(timezone.utc)
    last_updated = now if total_sessions else None

    existing_scores = await db.scalars(
        select(EngagementScore).where(EngagementScore.group_id == group_id)
    )
    score_by_user = {score.user_id: score for score in existing_scores.all()}
    seen_user_ids: set[str] = set()

    for membership in student_members:
        user_id = membership.user_id
        seen_user_ids.add(user_id)

        attendance_ratios: list[float] = []
        speaking_ratios: list[float] = []
        chat_ratios: list[float] = []
        meeting_lead_count = 0
        sessions_attended = 0

        for session in completed_sessions:
            session_metrics = metrics_by_session.get(session.id, [])
            user_metric = next(
                (metric for metric in session_metrics if metric.user_id == user_id),
                None,
            )
            if user_metric is None or user_metric.duration_minutes <= 0:
                continue

            sessions_attended += 1

            duration = session_duration[session.id]
            if duration > 0:
                ratio = min(user_metric.duration_minutes / duration, 1.0)
                attendance_ratios.append(ratio)

            total_speaking = sum(metric.speaking_turns for metric in session_metrics)
            if total_speaking > 0 and user_metric.speaking_turns > 0:
                speaking_ratios.append(user_metric.speaking_turns / total_speaking)

            total_chat = sum(metric.chat_messages for metric in session_metrics)
            if total_chat > 0 and user_metric.chat_messages > 0:
                chat_ratios.append(user_metric.chat_messages / total_chat)

            if user_metric.was_facilitator:
                meeting_lead_count += 1

        score = score_by_user.get(user_id)
        if score is None:
            score = EngagementScore(group_id=group_id, user_id=user_id)
            db.add(score)

        score.attendance_ratio = (
            sum(attendance_ratios) / len(attendance_ratios) if attendance_ratios else 0.0
        )
        score.speaking_ratio = (
            sum(speaking_ratios) / len(speaking_ratios) if speaking_ratios else 0.0
        )
        score.chat_participation = (
            sum(chat_ratios) / len(chat_ratios) if chat_ratios else 0.0
        )
        score.meeting_lead_count = meeting_lead_count
        score.sessions_attended = sessions_attended
        score.total_sessions = total_sessions
        score.last_updated = now

        # #region agent log
        if user_id == student_members[0].user_id:
            with open("/Users/gahamanyi/Documents/alu/CAPSTON PROJECT/.cursor/debug-ab9586.log", "a") as _f:
                _f.write(_json.dumps({"sessionId":"ab9586","hypothesisId":"H1","location":"engagement_calculator.py:score_update","message":"first student engagement score computed","data":{"user_id":user_id,"attendance_ratio":score.attendance_ratio,"speaking_ratio":score.speaking_ratio,"chat_participation":score.chat_participation,"sessions_attended":score.sessions_attended,"total_sessions":score.total_sessions},"timestamp":int(_time.time()*1000)}) + "\n")
        # #endregion

    stale_user_ids = set(score_by_user.keys()) - seen_user_ids
    if stale_user_ids:
        await db.execute(
            delete(EngagementScore).where(
                EngagementScore.group_id == group_id,
                EngagementScore.user_id.in_(stale_user_ids),
            )
        )

    return last_updated
