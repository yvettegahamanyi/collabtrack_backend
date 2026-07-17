from __future__ import annotations

import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models import (
    Assignment,
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
from app.services.benchmark_model import (
    BenchmarkModelUnavailableError,
    classify_contributor,
)
from app.services.student_cluster_model import (
    StudentClusterModelUnavailableError,
    is_student_cluster_model_available,
    predict_team_clusters,
)
from app.services.dataset import allocate_dataset_group_id
from app.services.dataset_features import (
    ML_FEATURE_COLUMNS,
    build_group_activity_totals,
    compute_member_features_from_contributions,
)
from app.services.groups import get_group_members
from app.services.llm_participation import (
    GroupScoringInput,
    LLMScoringError,
    LLMScoringUnavailableError,
    MemberScoringInput,
    is_llm_scoring_available,
    ref_for_index,
    score_group,
)
from app.services.participation import get_contributions


@dataclass
class StudentClusterResult:
    cluster_id: int
    cluster_key: str
    cluster_label: str
    composite_score: float = 0.0
    active_platforms: list[str] | None = None


@dataclass
class ParticipationScoreResult:
    user_id: str
    name: str | None
    predicted_score: float
    contributor_tier: str
    features: dict[str, float]
    generated_at: datetime
    student_cluster: StudentClusterResult | None = None
    llm_rationale: dict | None = None


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


def _student_cluster_from_prediction(result: dict[str, Any]) -> StudentClusterResult:
    return StudentClusterResult(
        cluster_id=int(result["cluster_id"]),
        cluster_key=str(result["cluster_key"]),
        cluster_label=str(result["cluster_label"]),
        composite_score=float(result.get("composite_score", 0.0)),
        active_platforms=list(result.get("active_platforms") or []),
    )


def _attach_student_clusters_to_scores(
    scores: list[ParticipationScoreResult],
) -> list[ParticipationScoreResult]:
    if not scores or not is_student_cluster_model_available():
        return scores

    try:
        cluster_results = predict_team_clusters(
            [score.features for score in scores]
        )
    except StudentClusterModelUnavailableError:
        return scores

    if len(cluster_results) != len(scores):
        return scores

    return [
        ParticipationScoreResult(
            user_id=score.user_id,
            name=score.name,
            predicted_score=score.predicted_score,
            contributor_tier=score.contributor_tier,
            features=score.features,
            generated_at=score.generated_at,
            student_cluster=_student_cluster_from_prediction(cluster),
            llm_rationale=score.llm_rationale,
        )
        for score, cluster in zip(scores, cluster_results, strict=True)
    ]


def enrich_scores_summary_with_ml_insights(
    summary: ParticipationScoresSummary,
) -> ParticipationScoresSummary:
    if not summary.scores:
        return summary

    enriched_scores = _attach_student_clusters_to_scores(summary.scores)
    return ParticipationScoresSummary(
        group_id=summary.group_id,
        generated_at=summary.generated_at,
        scores=enriched_scores,
        warnings=list(summary.warnings),
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


async def _load_assignment_description(
    group: ProjectGroup, db: AsyncSession
) -> str:
    """Fetch the assignment description for LLM context, or a neutral default."""
    parts: list[str] = []
    if group.assignment_id:
        assignment = await db.get(Assignment, group.assignment_id)
        if assignment is not None:
            if assignment.title:
                parts.append(f"Assignment: {assignment.title}")
            if assignment.description:
                parts.append(assignment.description)
    if group.description:
        parts.append(f"Group brief: {group.description}")
    return "\n".join(parts)


def _build_llm_group_input(
    *,
    assignment_description: str,
    student_memberships: list[GroupMembership],
    contributions,
    feature_by_user: dict,
    group_totals: dict[str, float],
) -> tuple[GroupScoringInput, dict[str, GroupMembership]]:
    """Assemble the anonymized per-group payload for the LLM."""
    contribution_by_user = {
        member.user_id: member for member in contributions.members
    }
    member_by_ref: dict[str, GroupMembership] = {}
    llm_members: list[MemberScoringInput] = []

    for index, membership in enumerate(student_memberships):
        ref = ref_for_index(index)
        member_by_ref[ref] = membership

        feature_row = feature_by_user.get(membership.user_id)
        features = (
            dict(feature_row.features)
            if feature_row is not None
            else {col: 0.0 for col in ML_FEATURE_COLUMNS}
        )

        contribution = contribution_by_user.get(membership.user_id)
        raw_github = (
            contribution.github.model_dump()
            if contribution and contribution.github
            else None
        )
        github_events = (
            [event.model_dump() for event in contribution.github_events]
            if contribution and contribution.github_events
            else None
        )
        raw_docs = (
            contribution.google_docs.model_dump()
            if contribution and contribution.google_docs
            else None
        )
        meeting = (
            contribution.meeting_engagement.model_dump()
            if contribution and contribution.meeting_engagement
            else None
        )
        github_connected = bool(contribution.github_connected) if contribution else False
        google_connected = bool(contribution.google_connected) if contribution else False
        google_matched = contribution.google_email_matched if contribution else None
        account_status = (
            contribution.account_status.value
            if contribution and contribution.account_status is not None
            else None
        )

        llm_members.append(
            MemberScoringInput(
                ref=ref,
                features=features,
                raw_github=raw_github,
                github_events=github_events,
                raw_google_docs=raw_docs,
                meeting=meeting,
                github_connected=github_connected,
                google_connected=google_connected,
                google_email_matched=google_matched,
                account_status=account_status,
            )
        )

    group_input = GroupScoringInput(
        assignment_description=assignment_description,
        member_count=len(student_memberships),
        group_totals=group_totals,
        members=llm_members,
    )
    return group_input, member_by_ref


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
) -> tuple[bool, list[str], list[str]]:
    """Report is ready once participation is synced and scores are current.

    A linked GitHub repo / Google Doc that matched no group members only blocks
    delivery *before* the first sync has run. Once a sync pass has completed but
    still matched nobody (e.g. commit-author emails don't match member emails),
    it is surfaced as a warning instead of blocking the report indefinitely.
    """
    blockers: list[str] = []
    warnings: list[str] = []
    last_synced_at = await _latest_snapshot_sync_at(group, db)
    has_synced = last_synced_at is not None
    if not has_synced:
        blockers.append("participation_not_synced")

    if not await _snapshots_include_github(group, db):
        if has_synced:
            warnings.append(
                "A GitHub repository is linked but no commits could be matched to "
                "group members. This usually happens when the emails used to list "
                "the members don't match the GitHub commit-author emails. "
                "Continuing without GitHub contribution data."
            )
        else:
            blockers.append("github_not_synced")

    if not await _snapshots_include_google_docs(group, db):
        if has_synced:
            warnings.append(
                "A Google Doc is linked but no edits could be matched to group "
                "members. Continuing without Google Docs contribution data."
            )
        else:
            blockers.append("google_docs_not_synced")

    if group.participation_scores_generated_at is None:
        blockers.append("scores_not_generated")
    elif _participation_scores_are_stale(group, last_synced_at):
        blockers.append("scores_stale")

    return len(blockers) == 0, blockers, warnings


async def maybe_regenerate_scores_after_sync(
    group: ProjectGroup,
    db: AsyncSession,
) -> list[str]:
    """Regenerate scores when snapshots were synced after the last score run."""
    warnings: list[str] = []
    last_synced_at = await _latest_snapshot_sync_at(group, db)
    is_stale = _participation_scores_are_stale(group, last_synced_at)

    if not is_stale:
        return warnings

    try:
        summary = await generate_participation_scores(
            group, db, allow_regenerate=True
        )
        from app.services.contribution_report import refresh_contribution_report_cache

        await refresh_contribution_report_cache(group, db)
        warnings.extend(summary.warnings)
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
                llm_rationale=dict(row.llm_rationale) if row.llm_rationale else None,
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


# ---------------------------------------------------------------------------
# Terminal debug output (toggle with SCORING_DEBUG in .env)
# ---------------------------------------------------------------------------
class _C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[96m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    MAGENTA = "\033[95m"
    BLUE = "\033[94m"
    GREY = "\033[90m"


_TIER_COLOR = {
    "strong": _C.GREEN,
    "average": _C.YELLOW,
    "below": _C.RED,
}
_BOX_WIDTH = 74


def _rule(label: str = "") -> str:
    if not label:
        return _C.GREY + "─" * _BOX_WIDTH + _C.RESET
    prefix = f"── {label} "
    pad = max(0, _BOX_WIDTH - len(prefix))
    return _C.YELLOW + _C.BOLD + prefix + _C.GREY + "─" * pad + _C.RESET


def _fmt_features(features: dict[str, float]) -> str:
    parts = [
        f"{key}={value:.3f}"
        for key, value in features.items()
        if isinstance(value, (int, float))
    ]
    return ", ".join(parts)


def _print_scoring_debug(
    *,
    group: ProjectGroup,
    group_input: GroupScoringInput,
    member_by_ref: dict[str, GroupMembership],
    llm_output,
    summary: ParticipationScoresSummary,
) -> None:
    if not get_settings().SCORING_DEBUG:
        return

    ref_to_userid = {ref: m.user_id for ref, m in member_by_ref.items()}
    ref_to_name = {
        ref: (m.user.name or m.user.email) for ref, m in member_by_ref.items()
    }
    score_by_userid = {s.user_id: s for s in summary.scores}
    output_by_ref = {m.ref: m for m in llm_output.members}

    lines: list[str] = []
    lines.append("")
    title = f"ML PARTICIPATION SCORING  ·  {group.group_name or group.id}"
    lines.append(_C.CYAN + _C.BOLD + "╔" + "═" * _BOX_WIDTH + "╗" + _C.RESET)
    lines.append(
        _C.CYAN + _C.BOLD + "║ " + title.ljust(_BOX_WIDTH - 1) + "║" + _C.RESET
    )
    lines.append(_C.CYAN + _C.BOLD + "╚" + "═" * _BOX_WIDTH + "╝" + _C.RESET)

    # --- LLM input ---
    lines.append(_rule("LLM INPUT"))
    desc = group_input.assignment_description or "(none)"
    lines.append(
        f"{_C.BOLD}Assignment:{_C.RESET} "
        + textwrap.shorten(desc.replace("\n", " "), width=90, placeholder=" …")
    )
    lines.append(
        f"{_C.BOLD}Members:{_C.RESET} {group_input.member_count}   "
        f"{_C.BOLD}Group totals:{_C.RESET} {_C.DIM}{group_input.group_totals}{_C.RESET}"
    )
    for member in group_input.members:
        name = ref_to_name.get(member.ref, "?")
        lines.append(f"  {_C.GREEN}{_C.BOLD}{member.ref}{_C.RESET} ({name})")
        lines.append(f"    {_C.DIM}shares:{_C.RESET} {_fmt_features(member.features)}")
        lines.append(
            f"    {_C.DIM}github:{_C.RESET} {member.raw_github}  "
            f"{_C.DIM}github_events:{_C.RESET} {len(member.github_events or [])} commits  "
            f"{_C.DIM}docs:{_C.RESET} {member.raw_google_docs}"
        )
        lines.append(f"    {_C.DIM}meeting:{_C.RESET} {member.meeting}")
        lines.append(
            f"    {_C.DIM}connected:{_C.RESET} github={member.github_connected} "
            f"google={member.google_connected} matched={member.google_email_matched} "
            f"account={member.account_status}"
        )

    # --- LLM output ---
    lines.append(_rule(f"LLM OUTPUT  (model={llm_output.model_version or 'n/a'})"))
    for ref, membership in member_by_ref.items():
        result = output_by_ref.get(ref)
        if result is None:
            continue
        name = ref_to_name.get(ref, "?")
        tier = _classify_score(result.score)
        tier_color = _TIER_COLOR.get(tier, _C.RESET)
        lines.append(
            f"  {_C.GREEN}{_C.BOLD}{ref}{_C.RESET} ({name})  "
            f"{tier_color}{_C.BOLD}score={result.score:.3f} [{tier}]{_C.RESET}  "
            f"{_C.DIM}confidence={result.confidence:.2f}{_C.RESET}"
        )
        lines.append(f"    {_C.DIM}top_area:{_C.RESET} {result.top_area}")
        if result.flags:
            lines.append(
                f"    {_C.MAGENTA}flags:{_C.RESET} {_C.MAGENTA}{result.flags}{_C.RESET}"
            )
        reasoning = result.reasoning or "(none)"
        wrapped = textwrap.wrap(reasoning, width=_BOX_WIDTH - 6) or ["(none)"]
        lines.append(f"    {_C.DIM}reasoning:{_C.RESET}")
        for wline in wrapped:
            lines.append(f"      {_C.DIM}{wline}{_C.RESET}")
    if llm_output.group_observations:
        lines.append(f"  {_C.BLUE}{_C.BOLD}Group observations:{_C.RESET}")
        for wline in textwrap.wrap(llm_output.group_observations, width=_BOX_WIDTH - 4):
            lines.append(f"    {_C.BLUE}{wline}{_C.RESET}")

    # --- ML enrichment (student clustering) ---
    lines.append(_rule("ML ENRICHMENT"))
    for ref, membership in member_by_ref.items():
        score = score_by_userid.get(ref_to_userid.get(ref))
        if score is None:
            continue
        name = ref_to_name.get(ref, "?")
        cluster = score.student_cluster
        if cluster is None:
            lines.append(
                f"  {ref} ({name}): {_C.DIM}student cluster unavailable{_C.RESET}"
            )
        else:
            platforms = ", ".join(cluster.active_platforms or []) or "none"
            lines.append(
                f"  {ref} ({name}): {_C.CYAN}{cluster.cluster_label}{_C.RESET}  "
                f"{_C.DIM}(cluster {cluster.cluster_id}, composite="
                f"{cluster.composite_score:.3f}, platforms={platforms}){_C.RESET}"
            )

    lines.append(_C.CYAN + _C.BOLD + "═" * (_BOX_WIDTH + 2) + _C.RESET)
    lines.append("")
    print("\n".join(lines), flush=True)


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

    if not is_llm_scoring_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Participation scoring is unavailable: the Gemini API is not "
                "configured. Set GEMINI_API_KEY and try again."
            ),
        )

    feature_rows = compute_member_features_from_contributions(contributions)
    feature_by_user = {row.user_id: row for row in feature_rows}

    session_count, speaking_total, chat_total = await _meeting_activity_totals(
        group, db
    )
    group_totals = build_group_activity_totals(
        contributions.members,
        total_meeting_sessions=session_count,
        total_speaking_turns=speaking_total,
        total_chat_messages=chat_total,
    )
    assignment_description = await _load_assignment_description(group, db)

    group_input, member_by_ref = _build_llm_group_input(
        assignment_description=assignment_description,
        student_memberships=student_memberships,
        contributions=contributions,
        feature_by_user=feature_by_user,
        group_totals=group_totals,
    )

    try:
        llm_output = await score_group(group_input)
    except LLMScoringUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Participation scoring is unavailable: {exc}",
        ) from exc
    except LLMScoringError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "The scoring model could not produce results. Please try again. "
                f"({exc})"
            ),
        ) from exc

    output_by_ref = {member.ref: member for member in llm_output.members}

    generated_at = datetime.now(timezone.utc)
    score_results: list[ParticipationScoreResult] = []

    for index, membership in enumerate(student_memberships):
        ref = ref_for_index(index)
        member_result = output_by_ref.get(ref)
        if member_result is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The scoring model returned incomplete results. Please retry.",
            )

        feature_row = feature_by_user.get(membership.user_id)
        features = (
            dict(feature_row.features)
            if feature_row is not None
            else {col: 0.0 for col in ML_FEATURE_COLUMNS}
        )
        predicted = member_result.score
        tier = _classify_score(predicted)
        rationale = {
            "reasoning": member_result.reasoning,
            "top_area": member_result.top_area,
            "flags": member_result.flags,
            "confidence": member_result.confidence,
            "group_observations": llm_output.group_observations,
            "model_version": llm_output.model_version,
        }

        result = ParticipationScoreResult(
            user_id=membership.user_id,
            name=membership.user.name,
            predicted_score=predicted,
            contributor_tier=tier,
            features=features,
            generated_at=generated_at,
            llm_rationale=rationale,
        )
        score_results.append(result)

        db.add(
            MemberParticipationScore(
                group_id=group.id,
                user_id=membership.user_id,
                predicted_score=predicted,
                contributor_tier=tier,
                features=features,
                llm_rationale=rationale,
                generated_at=generated_at,
            )
        )

    group.participation_scores_generated_at = generated_at
    db.add(group)
    await db.flush()

    enriched = enrich_scores_summary_with_ml_insights(
        ParticipationScoresSummary(
            group_id=group.id,
            generated_at=generated_at,
            scores=score_results,
            warnings=warnings,
        )
    )
    _print_scoring_debug(
        group=group,
        group_input=group_input,
        member_by_ref=member_by_ref,
        llm_output=llm_output,
        summary=enriched,
    )
    return enriched


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
        return enrich_scores_summary_with_ml_insights(existing)

    filtered = [
        score for score in existing.scores if score.user_id == viewer_user_id
    ]
    return enrich_scores_summary_with_ml_insights(
        ParticipationScoresSummary(
            group_id=group.id,
            generated_at=existing.generated_at,
            scores=filtered,
            warnings=[],
        )
    )


async def get_member_participation_score(
    group: ProjectGroup,
    user_id: str,
    db: AsyncSession,
) -> ParticipationScoreResult | None:
    rows = await db.scalars(
        select(MemberParticipationScore)
        .where(MemberParticipationScore.group_id == group.id)
        .options(selectinload(MemberParticipationScore.user))
        .order_by(MemberParticipationScore.user_id.asc())
    )
    all_rows = list(rows.all())
    if not all_rows:
        return None

    target = next((row for row in all_rows if row.user_id == user_id), None)
    if target is None:
        return None

    score_results = [
        ParticipationScoreResult(
            user_id=row.user_id,
            name=row.user.name,
            predicted_score=row.predicted_score,
            contributor_tier=row.contributor_tier,
            features=dict(row.features or {}),
            generated_at=row.generated_at,
            llm_rationale=dict(row.llm_rationale) if row.llm_rationale else None,
        )
        for row in all_rows
    ]
    enriched = _attach_student_clusters_to_scores(score_results)
    return next((score for score in enriched if score.user_id == user_id), None)


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
        from app.services.contribution_report import refresh_contribution_report_cache

        await refresh_contribution_report_cache(group, db)
    except HTTPException as exc:
        warnings.append(str(exc.detail))
    except Exception:
        warnings.append("Failed to generate ML participation scores.")
    return warnings
