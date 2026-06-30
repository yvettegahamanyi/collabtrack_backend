from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    CollabTrackDataset,
    GroupGithubRepo,
    GroupGoogleDoc,
    GroupMemberRole,
    GroupMembership,
    MeetingRawMetric,
    MeetingSession,
    MeetingSessionStatus,
    MemberParticipationScore,
    ParticipationSnapshot,
    ProjectGroup,
)

_DEBUG_LOG_PATH = (
    "/Users/gahamanyi/Documents/alu/CAPSTON PROJECT/.cursor/debug-2a07f5.log"
)


def _agent_debug_log(
    *,
    location: str,
    message: str,
    data: dict,
    hypothesis_id: str,
    run_id: str = "pre-fix",
) -> None:
    # #region agent log
    try:
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(
                json.dumps(
                    {
                        "sessionId": "2a07f5",
                        "runId": run_id,
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    }
                )
                + "\n"
            )
    except OSError:
        pass
    # #endregion
from app.services.benchmark_model import (
    BenchmarkModelUnavailableError,
    classify_contributor,
    is_benchmark_model_available,
    predict_benchmark_score,
)
from app.services.outlier_model import (
    OutlierModelUnavailableError,
    detect_student_outlier,
    is_outlier_model_available,
)
from app.services.team_cluster_model import (
    TeamClusterModelUnavailableError,
    is_team_cluster_model_available,
    predict_team_archetype,
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
class OutlierDetectionResult:
    is_outlier: bool
    anomaly_score: float
    outlier_type: str


@dataclass
class TeamArchetypeResult:
    cluster_id: int
    archetype: str
    archetype_label: str


@dataclass
class ParticipationScoreResult:
    user_id: str
    name: str | None
    predicted_score: float
    contributor_tier: str
    features: dict[str, float]
    generated_at: datetime
    outlier: OutlierDetectionResult | None = None


@dataclass
class ParticipationScoresSummary:
    group_id: str
    generated_at: datetime
    scores: list[ParticipationScoreResult]
    warnings: list[str]
    team_archetype: TeamArchetypeResult | None = None


def _tier_label(tier: str) -> str:
    if tier == "strong":
        return "Strong contributor"
    if tier == "average":
        return "Average contributor"
    return "Below average contributor"


def tier_display_label(tier: str) -> str:
    return _tier_label(tier)


def _detect_outlier_for_features(
    features: dict[str, float],
) -> OutlierDetectionResult | None:
    if not is_outlier_model_available():
        return None
    try:
        result = detect_student_outlier(features)
    except OutlierModelUnavailableError:
        return None
    return OutlierDetectionResult(
        is_outlier=bool(result["is_outlier"]),
        anomaly_score=float(result["anomaly_score"]),
        outlier_type=str(result["outlier_type"]),
    )


def _predict_team_archetype_for_scores(
    scores: list[ParticipationScoreResult],
) -> TeamArchetypeResult | None:
    if not scores or not is_team_cluster_model_available():
        return None
    try:
        result = predict_team_archetype([score.features for score in scores])
    except TeamClusterModelUnavailableError:
        return None
    return TeamArchetypeResult(
        cluster_id=int(result["cluster_id"]),
        archetype=str(result["archetype"]),
        archetype_label=str(result["archetype_label"]),
    )


def enrich_scores_summary_with_ml_insights(
    summary: ParticipationScoresSummary,
    *,
    include_team_archetype: bool = True,
) -> ParticipationScoresSummary:
    if not summary.scores:
        return summary

    enriched_scores = [
        ParticipationScoreResult(
            user_id=score.user_id,
            name=score.name,
            predicted_score=score.predicted_score,
            contributor_tier=score.contributor_tier,
            features=score.features,
            generated_at=score.generated_at,
            outlier=_detect_outlier_for_features(score.features),
        )
        for score in summary.scores
    ]
    team_archetype = (
        _predict_team_archetype_for_scores(enriched_scores)
        if include_team_archetype
        else None
    )
    return ParticipationScoresSummary(
        group_id=summary.group_id,
        generated_at=summary.generated_at,
        scores=enriched_scores,
        warnings=list(summary.warnings),
        team_archetype=team_archetype,
    )


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


async def _latest_snapshot_sync_at(
    group: ProjectGroup, db: AsyncSession
) -> datetime | None:
    synced_at = await db.scalar(
        select(func.max(ParticipationSnapshot.synced_at)).where(
            ParticipationSnapshot.group_id == group.id
        )
    )
    return synced_at


def _participation_scores_are_stale(
    group: ProjectGroup, last_synced_at: datetime | None
) -> bool:
    if group.participation_scores_generated_at is None or last_synced_at is None:
        return False
    return group.participation_scores_generated_at < last_synced_at


async def _clear_participation_scores(
    group: ProjectGroup, db: AsyncSession
) -> None:
    await db.execute(
        delete(MemberParticipationScore).where(
            MemberParticipationScore.group_id == group.id
        )
    )
    group.participation_scores_generated_at = None
    db.add(group)
    await db.flush()


async def _snapshots_include_github(group: ProjectGroup, db: AsyncSession) -> bool:
    repo_count = await db.scalar(
        select(func.count())
        .select_from(GroupGithubRepo)
        .where(GroupGithubRepo.group_id == group.id)
    )
    if not repo_count:
        return True

    snapshots = await db.scalars(
        select(ParticipationSnapshot).where(
            ParticipationSnapshot.group_id == group.id
        )
    )
    for snapshot in snapshots.all():
        if snapshot.metrics and "github" in snapshot.metrics:
            return True
    return False


async def _snapshots_include_google_docs(group: ProjectGroup, db: AsyncSession) -> bool:
    doc_count = await db.scalar(
        select(func.count())
        .select_from(GroupGoogleDoc)
        .where(GroupGoogleDoc.group_id == group.id)
    )
    if not doc_count:
        return True

    snapshots = await db.scalars(
        select(ParticipationSnapshot).where(
            ParticipationSnapshot.group_id == group.id
        )
    )
    for snapshot in snapshots.all():
        if snapshot.metrics and "google_docs" in snapshot.metrics:
            return True
    return False


async def report_delivery_readiness(
    group: ProjectGroup, db: AsyncSession
) -> tuple[bool, list[str]]:
    """True when linked integrations are synced and participation scores are current."""
    blockers: list[str] = []
    last_synced_at = await _latest_snapshot_sync_at(group, db)
    if last_synced_at is None:
        blockers.append("participation_not_synced")

    if not await _snapshots_include_github(group, db):
        blockers.append("github_not_synced")

    if not await _snapshots_include_google_docs(group, db):
        blockers.append("google_docs_not_synced")

    if group.participation_scores_generated_at is None:
        blockers.append("scores_not_generated")
    elif _participation_scores_are_stale(group, last_synced_at):
        blockers.append("scores_stale")

    return len(blockers) == 0, blockers


async def maybe_regenerate_scores_after_sync(
    group: ProjectGroup,
    db: AsyncSession,
) -> list[str]:
    """Regenerate scores when snapshots were synced after the last score run."""
    warnings: list[str] = []
    last_synced_at = await _latest_snapshot_sync_at(group, db)
    is_stale = _participation_scores_are_stale(group, last_synced_at)

    # #region agent log
    _agent_debug_log(
        location="participation_scoring.py:maybe_regenerate_scores_after_sync",
        message="stale_check",
        data={
            "group_id": group.id,
            "scores_generated_at": (
                group.participation_scores_generated_at.isoformat()
                if group.participation_scores_generated_at
                else None
            ),
            "last_synced_at": (
                last_synced_at.isoformat() if last_synced_at else None
            ),
            "is_stale": is_stale,
        },
        hypothesis_id="A",
    )
    # #endregion

    if not is_stale:
        return warnings

    try:
        summary = await generate_participation_scores(
            group, db, allow_regenerate=True
        )
        from app.services.contribution_report import refresh_contribution_report_cache

        await refresh_contribution_report_cache(group, db)
        warnings.extend(summary.warnings)
        # #region agent log
        _agent_debug_log(
            location="participation_scoring.py:maybe_regenerate_scores_after_sync",
            message="regenerated_scores",
            data={
                "group_id": group.id,
                "new_generated_at": summary.generated_at.isoformat(),
                "score_count": len(summary.scores),
            },
            hypothesis_id="A",
            run_id="post-fix",
        )
        # #endregion
    except HTTPException as exc:
        warnings.append(str(exc.detail))
    except Exception:
        warnings.append("Failed to regenerate participation scores after sync.")
    return warnings


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

    if allow_regenerate and group.participation_scores_generated_at:
        await _clear_participation_scores(group, db)

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

    last_synced_at = await _latest_snapshot_sync_at(group, db)
    total_commits = sum(
        float(row.features.get("code_commits", 0.0)) for row in feature_rows
    )
    # #region agent log
    _agent_debug_log(
        location="participation_scoring.py:generate_participation_scores",
        message="score_generation_inputs",
        data={
            "group_id": group.id,
            "allow_regenerate": allow_regenerate,
            "scores_generated_at": (
                group.participation_scores_generated_at.isoformat()
                if group.participation_scores_generated_at
                else None
            ),
            "last_synced_at": (
                last_synced_at.isoformat() if last_synced_at else None
            ),
            "total_code_commits": total_commits,
            "member_count": len(student_memberships),
        },
        hypothesis_id="B",
    )
    # #endregion

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

    return enrich_scores_summary_with_ml_insights(
        ParticipationScoresSummary(
            group_id=group.id,
            generated_at=generated_at,
            scores=score_results,
            warnings=warnings,
        )
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
        return enrich_scores_summary_with_ml_insights(
            existing,
            include_team_archetype=True,
        )

    filtered = [
        score for score in existing.scores if score.user_id == viewer_user_id
    ]
    return enrich_scores_summary_with_ml_insights(
        ParticipationScoresSummary(
            group_id=group.id,
            generated_at=existing.generated_at,
            scores=filtered,
            warnings=[],
        ),
        include_team_archetype=True,
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

    result = ParticipationScoreResult(
        user_id=row.user_id,
        name=row.user.name,
        predicted_score=row.predicted_score,
        contributor_tier=row.contributor_tier,
        features=dict(row.features or {}),
        generated_at=row.generated_at,
        outlier=_detect_outlier_for_features(dict(row.features or {})),
    )
    return result


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

    if not await _snapshots_include_github(group, db):
        warnings.append(
            "GitHub data not yet synced; deferring participation score generation."
        )
        # #region agent log
        _agent_debug_log(
            location="participation_scoring.py:try_generate_participation_scores_for_report",
            message="deferred_github_missing",
            data={"group_id": group.id},
            hypothesis_id="C",
        )
        # #endregion
        return warnings

    try:
        await generate_participation_scores(group, db)
        from app.services.contribution_report import refresh_contribution_report_cache

        await refresh_contribution_report_cache(group, db)
    except HTTPException as exc:
        warnings.append(str(exc.detail))
    except Exception:
        warnings.append("Failed to generate ML participation scores.")
    return warnings
