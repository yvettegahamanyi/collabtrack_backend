from dataclasses import dataclass

from app.schemas.participation import ContributionsOut, MemberParticipationOut

BASE_WEIGHTS: dict[str, float] = {
    "code_commits": 0.15,
    "code_share": 0.05,
    "review_participation": 0.10,
    "attendance_ratio": 0.20,
    "speaking_ratio": 0.15,
    "chat_participation": 0.10,
    "docs_contribution_share": 0.20,
    "comment_activity": 0.05,
}

_RAW_TOTAL_FEATURES = {
    "code_commits",
    "code_share",
    "review_participation",
    "docs_contribution_share",
    "comment_activity",
}
_RATIO_FEATURES = {
    "attendance_ratio",
    "speaking_ratio",
    "chat_participation",
}


@dataclass
class StudentRawTotals:
    total_commits: int = 0
    lines_changed: int = 0
    prs_reviewed: int = 0
    edits: int = 0
    comments: int = 0


@dataclass
class ComputedStudentFeatures:
    student_id: str
    group_id: str
    code_commits: float
    code_share: float
    review_participation: float
    attendance_ratio: float
    speaking_participation_ratio: float
    chat_participation_ratio: float
    docs_contribution_share: float
    comment_activity: float
    benchmark_score: float


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _member_raw_totals(member: MemberParticipationOut) -> StudentRawTotals:
    totals = StudentRawTotals()
    if member.github:
        totals.total_commits = member.github.total_commits
        totals.lines_changed = member.github.lines_changed
        totals.prs_reviewed = member.github.prs_reviewed
    if member.google_docs:
        totals.edits = member.google_docs.edits
        totals.comments = member.google_docs.comments
    return totals


def build_group_activity_totals(
    members: list[MemberParticipationOut],
    *,
    total_meeting_sessions: int,
    total_speaking_turns: int,
    total_chat_messages: int,
) -> dict[str, float]:
    raw = [_member_raw_totals(member) for member in members]
    return {
        "code_commits": float(sum(item.total_commits for item in raw)),
        "code_share": float(sum(item.lines_changed for item in raw)),
        "review_participation": float(sum(item.prs_reviewed for item in raw)),
        "attendance_ratio": float(total_meeting_sessions),
        "speaking_ratio": float(total_speaking_turns),
        "chat_participation": float(total_chat_messages),
        "docs_contribution_share": float(sum(item.edits for item in raw)),
        "comment_activity": float(sum(item.comments for item in raw)),
    }


def compute_rescaled_weights(
    group_totals: dict[str, float],
    all_student_features: list[dict[str, float]],
) -> dict[str, float]:
    """Rescale base weights to features the group actually contributed to."""
    active: dict[str, float] = {}
    for key, weight in BASE_WEIGHTS.items():
        if key in _RAW_TOTAL_FEATURES:
            if group_totals.get(key, 0) > 0:
                active[key] = weight
        elif key in _RATIO_FEATURES:
            if sum(student.get(key, 0) for student in all_student_features) > 0:
                active[key] = weight

    if not active:
        return {}

    total_active_weight = sum(active.values())
    return {key: weight / total_active_weight for key, weight in active.items()}


def compute_benchmark(
    student_features: dict[str, float],
    rescaled_weights: dict[str, float],
) -> float:
    if not rescaled_weights:
        return 0.0
    return sum(
        rescaled_weights[key] * student_features[key] for key in rescaled_weights
    )


def compute_dataset_features(
    *,
    contributions: ContributionsOut,
    dataset_group_id: str,
    student_id_by_user_id: dict[str, str],
    group_activity_totals: dict[str, float],
) -> list[ComputedStudentFeatures]:
    members = contributions.members
    raw_by_user = {member.user_id: _member_raw_totals(member) for member in members}
    all_raw = list(raw_by_user.values())

    sum_lines = sum(item.lines_changed for item in all_raw)
    sum_commits = sum(item.total_commits for item in all_raw)
    sum_prs = sum(item.prs_reviewed for item in all_raw)
    sum_edits = sum(item.edits for item in all_raw)
    sum_comments = sum(item.comments for item in all_raw)

    pending: list[tuple[str, dict[str, float]]] = []
    for member in members:
        dataset_student_id = student_id_by_user_id.get(member.user_id)
        if dataset_student_id is None:
            continue

        raw = raw_by_user[member.user_id]
        attendance_ratio = 0.0
        speaking_ratio = 0.0
        chat_ratio = 0.0
        if member.meeting_engagement:
            attendance_ratio = member.meeting_engagement.attendance_ratio
            speaking_ratio = member.meeting_engagement.speaking_ratio
            chat_ratio = member.meeting_engagement.chat_participation

        student_features = {
            "code_commits": _safe_ratio(raw.total_commits, sum_commits),
            "code_share": _safe_ratio(raw.lines_changed, sum_lines),
            "review_participation": _safe_ratio(raw.prs_reviewed, sum_prs),
            "attendance_ratio": attendance_ratio,
            "speaking_ratio": speaking_ratio,
            "chat_participation": chat_ratio,
            "docs_contribution_share": _safe_ratio(raw.edits, sum_edits),
            "comment_activity": _safe_ratio(raw.comments, sum_comments),
        }
        pending.append((dataset_student_id, student_features))

    all_student_features = [features for _, features in pending]
    rescaled_weights = compute_rescaled_weights(
        group_activity_totals,
        all_student_features,
    )

    results: list[ComputedStudentFeatures] = []
    for dataset_student_id, student_features in pending:
        benchmark = compute_benchmark(student_features, rescaled_weights)

        results.append(
            ComputedStudentFeatures(
                student_id=dataset_student_id,
                group_id=dataset_group_id,
                code_commits=student_features["code_commits"],
                code_share=student_features["code_share"],
                review_participation=student_features["review_participation"],
                attendance_ratio=student_features["attendance_ratio"],
                speaking_participation_ratio=student_features["speaking_ratio"],
                chat_participation_ratio=student_features["chat_participation"],
                docs_contribution_share=student_features["docs_contribution_share"],
                comment_activity=student_features["comment_activity"],
                benchmark_score=benchmark,
            )
        )

    return results
