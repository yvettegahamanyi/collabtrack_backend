from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    CollabTrackDataset,
    GroupMemberRole,
    GroupMembership,
    MeetingRawMetric,
    MeetingSession,
    MeetingSessionStatus,
    MemberParticipationScore,
    ParticipationSnapshot,
    ProjectGroup,
)
from app.services.benchmark_model import (
    BenchmarkModelUnavailableError,
    classify_contributor,
    is_benchmark_model_available,
    predict_benchmark_score,
)
from app.services.dataset import allocate_dataset_group_id
from app.services.dataset_features import (
    ML_FEATURE_COLUMNS,
    build_group_activity_totals,
    compute_benchmark,
    compute_member_features_from_contributions,
    compute_rescaled_weights,
)
from app.services.groups import get_group_members
from app.services.participation import get_contributions


@dataclass
class ParticipationScoreResult:
    user_id: str
    name: str | None
    predicted_score: float
    contributor_tier: str
    features: dict[str, float]
    generated_at: datetime


@dataclass
class ParticipationScoresSummary:
    group_id: str
    generated_at: datetime
    scores: list[ParticipationScoreResult]
    warnings: list[str]


def _tier_label(tier: str) -> str:
    if tier == "strong":
        return "Strong contributor"
    if tier == "average":
        return "Average contributor"
    return "Below average contributor"


def tier_display_label(tier: str) -> str:
    return _tier_label(tier)


def _classify_score(score: float) -> str:
    try:
        return classify_contributor(score)
    except BenchmarkModelUnavailableError:
        if score >= 0.7:
            return "strong"
        if score >= 0.5:
            return "average"
        return "below"


def _predict_member_score(
    features: dict[str, float], *, rule_based_score: float
) -> tuple[float, bool]:
    """Return (score, used_ml). Falls back to rule-based when ML artifacts are absent."""
    if not is_benchmark_model_available():
        return rule_based_score, False
    try:
        return predict_benchmark_score(features), True
    except BenchmarkModelUnavailableError:
        return rule_based_score, False


async def _meeting_activity_totals(
    group: ProjectGroup, db: AsyncSession
) -> tuple[int, int, int]:
    completed_sessions = await db.scalars(
        select(MeetingSession).where(
            MeetingSession.group_id == group.id,
            MeetingSession.status == MeetingSessionStatus.COMPLETED,
        )
    )
    session_ids = [session.id for session in completed_sessions.all()]

    speaking_total = 0
    chat_total = 0
    if session_ids:
        raw_metrics = await db.scalars(
            select(MeetingRawMetric).where(
                MeetingRawMetric.meeting_session_id.in_(session_ids)
            )
        )
        for metric in raw_metrics.all():
            speaking_total += metric.speaking_turns
            chat_total += metric.chat_messages

    return len(session_ids), speaking_total, chat_total


def _benchmark_features(features: dict[str, float]) -> dict[str, float]:
    benchmark_features = dict(features)
    benchmark_features["speaking_ratio"] = benchmark_features.pop(
        "speaking_participation_ratio"
    )
    benchmark_features["chat_participation"] = benchmark_features.pop(
        "chat_participation_ratio"
    )
    return benchmark_features


async def _rule_based_scores_by_user(
    group: ProjectGroup,
    contributions,
    student_user_ids: set[str],
    db: AsyncSession,
) -> dict[str, float]:
    session_count, speaking_total, chat_total = await _meeting_activity_totals(
        group, db
    )
    group_activity_totals = build_group_activity_totals(
        contributions.members,
        total_meeting_sessions=session_count,
        total_speaking_turns=speaking_total,
        total_chat_messages=chat_total,
    )
    feature_rows = compute_member_features_from_contributions(contributions)
    benchmark_inputs = [
        _benchmark_features(dict(row.features))
        for row in feature_rows
        if row.user_id in student_user_ids
    ]
    if not benchmark_inputs:
        return {}

    weights = compute_rescaled_weights(group_activity_totals, benchmark_inputs)
    scores: dict[str, float] = {}
    for row in feature_rows:
        if row.user_id not in student_user_ids:
            continue
        scores[row.user_id] = compute_benchmark(
            _benchmark_features(dict(row.features)),
            weights,
        )
    return scores


async def _require_synced_participation(group: ProjectGroup, db: AsyncSession) -> None:
    snapshot = await db.scalar(
        select(ParticipationSnapshot.id).where(
            ParticipationSnapshot.group_id == group.id
        ).limit(1)
    )
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Sync participation before generating scores.",
        )


def _ordered_student_memberships(
    memberships: list[GroupMembership],
) -> list[GroupMembership]:
    students = [
        membership
        for membership in memberships
        if membership.role == GroupMemberRole.STUDENT
    ]
    return sorted(
        students,
        key=lambda membership: (
            (membership.user.name or "").lower(),
            membership.user_id,
        ),
    )


async def _load_existing_scores(
    group: ProjectGroup, db: AsyncSession
) -> ParticipationScoresSummary | None:
    if group.participation_scores_generated_at is None:
        return None

    records = await db.scalars(
        select(MemberParticipationScore)
        .where(MemberParticipationScore.group_id == group.id)
        .options(selectinload(MemberParticipationScore.user))
        .order_by(MemberParticipationScore.generated_at.asc())
    )
    rows = list(records.all())
    if not rows:
        return None

    return ParticipationScoresSummary(
        group_id=group.id,
        generated_at=group.participation_scores_generated_at,
        scores=[
            ParticipationScoreResult(
                user_id=row.user_id,
                name=row.user.name,
                predicted_score=row.predicted_score,
                contributor_tier=row.contributor_tier,
                features=dict(row.features or {}),
                generated_at=row.generated_at,
            )
            for row in rows
        ],
        warnings=[],
    )


async def _append_dataset_rows(
    *,
    group: ProjectGroup,
    db: AsyncSession,
    score_by_user_id: dict[str, ParticipationScoreResult],
    student_id_by_user_id: dict[str, str],
) -> None:
    if group.dataset_exported_at is not None:
        return

    if not group.dataset_group_id:
        group.dataset_group_id = await allocate_dataset_group_id(db)

    for user_id, student_id in student_id_by_user_id.items():
        score_row = score_by_user_id[user_id]
        features = score_row.features
        db.add(
            CollabTrackDataset(
                student_id=student_id,
                group_id=group.dataset_group_id,
                code_commits=features["code_commits"],
                code_share=features["code_share"],
                review_participation=features["review_participation"],
                attendance_ratio=features["attendance_ratio"],
                speaking_participation_ratio=features["speaking_participation_ratio"],
                chat_participation_ratio=features["chat_participation_ratio"],
                docs_contribution_share=features["docs_contribution_share"],
                comment_activity=features["comment_activity"],
                benchmark_score=score_row.predicted_score,
            )
        )

    group.dataset_exported_at = datetime.now(timezone.utc)
    db.add(group)


async def generate_participation_scores(
    group: ProjectGroup,
    db: AsyncSession,
    *,
    allow_regenerate: bool = False,
) -> ParticipationScoresSummary:
    if group.participation_scores_generated_at and not allow_regenerate:
        existing = await _load_existing_scores(group, db)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Participation scores have already been generated for this group.",
            )

    await _require_synced_participation(group, db)

    warnings: list[str] = []
    try:
        contributions = await get_contributions(group, db)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load contribution data.",
        ) from exc

    memberships = await get_group_members(group, db)
    student_memberships = _ordered_student_memberships(memberships)
    if not student_memberships:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No student members found in this group.",
        )

    feature_rows = compute_member_features_from_contributions(contributions)
    feature_by_user = {row.user_id: row for row in feature_rows}
    student_user_ids = {membership.user_id for membership in student_memberships}
    rule_based_scores = await _rule_based_scores_by_user(
        group, contributions, student_user_ids, db
    )

    generated_at = datetime.now(timezone.utc)
    score_results: list[ParticipationScoreResult] = []
    score_by_user_id: dict[str, ParticipationScoreResult] = {}
    used_ml = False

    for membership in student_memberships:
        feature_row = feature_by_user.get(membership.user_id)
        if feature_row is None:
            features = {col: 0.0 for col in ML_FEATURE_COLUMNS}
        else:
            features = dict(feature_row.features)

        rule_score = rule_based_scores.get(membership.user_id, 0.0)
        predicted, member_used_ml = _predict_member_score(
            features, rule_based_score=rule_score
        )
        used_ml = used_ml or member_used_ml
        tier = _classify_score(predicted)
        result = ParticipationScoreResult(
            user_id=membership.user_id,
            name=membership.user.name,
            predicted_score=predicted,
            contributor_tier=tier,
            features=features,
            generated_at=generated_at,
        )
        score_results.append(result)
        score_by_user_id[membership.user_id] = result

        db.add(
            MemberParticipationScore(
                group_id=group.id,
                user_id=membership.user_id,
                predicted_score=predicted,
                contributor_tier=tier,
                features=features,
                generated_at=generated_at,
            )
        )

    if not used_ml:
        warnings.append(
            "ML model unavailable; participation scores used rule-based weighting."
        )

    student_id_by_user_id = {
        membership.user_id: str(index + 1)
        for index, membership in enumerate(student_memberships)
    }

    await _append_dataset_rows(
        group=group,
        db=db,
        score_by_user_id=score_by_user_id,
        student_id_by_user_id=student_id_by_user_id,
    )

    group.participation_scores_generated_at = generated_at
    db.add(group)
    await db.flush()

    return ParticipationScoresSummary(
        group_id=group.id,
        generated_at=generated_at,
        scores=score_results,
        warnings=warnings,
    )


async def get_participation_scores_for_group(
    group: ProjectGroup,
    db: AsyncSession,
    *,
    viewer_user_id: str,
    viewer_is_manager: bool,
) -> ParticipationScoresSummary:
    existing = await _load_existing_scores(group, db)
    if existing is None:
        return ParticipationScoresSummary(
            group_id=group.id,
            generated_at=datetime.now(timezone.utc),
            scores=[],
            warnings=[],
        )

    if viewer_is_manager:
        return existing

    filtered = [
        score for score in existing.scores if score.user_id == viewer_user_id
    ]
    return ParticipationScoresSummary(
        group_id=group.id,
        generated_at=existing.generated_at,
        scores=filtered,
        warnings=[],
    )


async def get_member_participation_score(
    group: ProjectGroup,
    user_id: str,
    db: AsyncSession,
) -> ParticipationScoreResult | None:
    row = await db.scalar(
        select(MemberParticipationScore)
        .where(
            MemberParticipationScore.group_id == group.id,
            MemberParticipationScore.user_id == user_id,
        )
        .options(selectinload(MemberParticipationScore.user))
    )
    if row is None:
        return None

    return ParticipationScoreResult(
        user_id=row.user_id,
        name=row.user.name,
        predicted_score=row.predicted_score,
        contributor_tier=row.contributor_tier,
        features=dict(row.features or {}),
        generated_at=row.generated_at,
    )


async def try_generate_participation_scores_for_report(
    group: ProjectGroup,
    db: AsyncSession,
) -> list[str]:
    """Best-effort score generation for instructor reports; never raises."""
    warnings: list[str] = []
    if group.participation_scores_generated_at is not None:
        return warnings

    try:
        await _require_synced_participation(group, db)
    except HTTPException as exc:
        warnings.append(str(exc.detail))
        return warnings

    try:
        await generate_participation_scores(group, db)
    except HTTPException as exc:
        warnings.append(str(exc.detail))
    except Exception:
        warnings.append("Failed to generate ML participation scores.")
    return warnings
