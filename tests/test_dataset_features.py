from app.schemas.participation import (
    ContributionsOut,
    GithubMetrics,
    GoogleDocsMetrics,
    MemberParticipationOut,
    MeetingEngagementMetrics,
)
from app.services.dataset_features import (
    build_group_activity_totals,
    compute_benchmark,
    compute_dataset_features,
    compute_member_features_from_contributions,
    compute_rescaled_weights,
)


def _member(
    user_id: str,
    *,
    commits: int = 0,
    lines: int = 0,
    prs: int = 0,
    edits: int = 0,
    comments: int = 0,
    attendance: float = 0.0,
    speaking: float = 0.0,
    chat: float = 0.0,
) -> MemberParticipationOut:
    return MemberParticipationOut(
        user_id=user_id,
        name=user_id,
        email=f"{user_id}@example.com",
        github_connected=False,
        google_connected=False,
        github=GithubMetrics(
            total_commits=commits,
            lines_changed=lines,
            prs_reviewed=prs,
        ),
        google_docs=GoogleDocsMetrics(edits=edits, comments=comments),
        meeting_engagement=MeetingEngagementMetrics(
            attendance_ratio=attendance,
            speaking_ratio=speaking,
            chat_participation=chat,
        ),
    )


def test_share_ratios_use_zero_when_denominator_missing():
    contributions = ContributionsOut(
        group_id="internal",
        members=[
            _member("u1", commits=3, lines=10, prs=2, edits=5, comments=1),
            _member("u2"),
        ],
    )
    group_totals = build_group_activity_totals(
        contributions.members,
        total_meeting_sessions=0,
        total_speaking_turns=0,
        total_chat_messages=0,
    )
    rows = compute_dataset_features(
        contributions=contributions,
        dataset_group_id="1",
        student_id_by_user_id={"u1": "1", "u2": "2"},
        group_activity_totals=group_totals,
    )
    by_student = {row.student_id: row for row in rows}

    assert by_student["1"].code_commits == 1.0
    assert by_student["1"].code_share == 1.0
    assert by_student["2"].code_commits == 0.0
    assert by_student["2"].code_share == 0.0
    assert by_student["2"].review_participation == 0.0
    assert by_student["2"].docs_contribution_share == 0.0
    assert by_student["2"].comment_activity == 0.0


def test_code_commits_and_lines_use_separate_benchmark_weights():
    student_one = {
        "code_commits": 0.25,
        "code_share": 0.99,
        "review_participation": 0.0,
        "attendance_ratio": 0.0,
        "speaking_ratio": 0.0,
        "chat_participation": 0.0,
        "docs_contribution_share": 0.0,
        "comment_activity": 0.0,
    }
    student_two = {
        "code_commits": 0.75,
        "code_share": 0.01,
        "review_participation": 0.0,
        "attendance_ratio": 0.0,
        "speaking_ratio": 0.0,
        "chat_participation": 0.0,
        "docs_contribution_share": 0.0,
        "comment_activity": 0.0,
    }
    group_totals = {
        "code_commits": 40.0,
        "code_share": 100000.0,
        "review_participation": 0.0,
        "attendance_ratio": 0.0,
        "speaking_ratio": 0.0,
        "chat_participation": 0.0,
        "docs_contribution_share": 0.0,
        "comment_activity": 0.0,
    }
    weights = compute_rescaled_weights(group_totals, [student_one, student_two])

    assert abs(weights["code_commits"] - 0.75) < 1e-9
    assert abs(weights["code_share"] - 0.25) < 1e-9
    assert abs(compute_benchmark(student_one, weights) - 0.435) < 1e-9
    assert abs(compute_benchmark(student_two, weights) - 0.565) < 1e-9


def test_benchmark_rescales_active_features_only():
    student_features = {
        "code_commits": 0.0,
        "code_share": 0.8,
        "review_participation": 0.0,
        "attendance_ratio": 0.0,
        "speaking_ratio": 0.0,
        "chat_participation": 0.0,
        "docs_contribution_share": 0.2,
        "comment_activity": 0.0,
    }
    group_totals = {
        "code_commits": 0.0,
        "code_share": 100.0,
        "review_participation": 0.0,
        "attendance_ratio": 0.0,
        "speaking_ratio": 0.0,
        "chat_participation": 0.0,
        "docs_contribution_share": 50.0,
        "comment_activity": 0.0,
    }
    weights = compute_rescaled_weights(group_totals, [student_features])

    score = compute_benchmark(student_features, weights)

    # Active weights: code lines 0.05 + docs 0.20 => rescale to 0.2 and 0.8
    assert abs(score - 0.32) < 1e-9


def test_benchmark_excludes_meeting_weights_when_no_student_participation():
    student_one = {
        "code_commits": 0.5,
        "code_share": 0.98,
        "review_participation": 0.0,
        "attendance_ratio": 0.0,
        "speaking_ratio": 0.0,
        "chat_participation": 0.0,
        "docs_contribution_share": 0.16,
        "comment_activity": 0.0,
    }
    student_two = {
        "code_commits": 0.5,
        "code_share": 0.02,
        "review_participation": 0.0,
        "attendance_ratio": 0.0,
        "speaking_ratio": 0.0,
        "chat_participation": 0.0,
        "docs_contribution_share": 0.84,
        "comment_activity": 0.0,
    }
    group_totals = {
        "code_commits": 20.0,
        "code_share": 100.0,
        "review_participation": 0.0,
        "attendance_ratio": 1.0,
        "speaking_ratio": 0.0,
        "chat_participation": 0.0,
        "docs_contribution_share": 50.0,
        "comment_activity": 0.0,
    }
    weights = compute_rescaled_weights(group_totals, [student_one, student_two])

    assert "attendance_ratio" not in weights
    assert "speaking_ratio" not in weights
    assert abs(weights["code_commits"] - 0.375) < 1e-9
    assert abs(weights["code_share"] - 0.125) < 1e-9
    assert weights["docs_contribution_share"] == 0.5
    assert abs(compute_benchmark(student_one, weights) - 0.39) < 1e-9
    assert abs(compute_benchmark(student_two, weights) - 0.61) < 1e-9


def test_benchmark_returns_zero_when_no_group_activity():
    student_features = {
        "code_commits": 0.0,
        "code_share": 0.0,
        "review_participation": 0.0,
        "attendance_ratio": 0.0,
        "speaking_ratio": 0.0,
        "chat_participation": 0.0,
        "docs_contribution_share": 0.0,
        "comment_activity": 0.0,
    }
    group_totals = {
        "code_commits": 0.0,
        "code_share": 0.0,
        "review_participation": 0.0,
        "attendance_ratio": 0.0,
        "speaking_ratio": 0.0,
        "chat_participation": 0.0,
        "docs_contribution_share": 0.0,
        "comment_activity": 0.0,
    }

    weights = compute_rescaled_weights(group_totals, [student_features])
    assert compute_benchmark(student_features, weights) == 0.0


def test_per_group_student_numbering():
    contributions = ContributionsOut(
        group_id="internal",
        members=[_member("u1", commits=1, lines=1), _member("u2", commits=1, lines=1)],
    )
    group_totals = build_group_activity_totals(
        contributions.members,
        total_meeting_sessions=0,
        total_speaking_turns=0,
        total_chat_messages=0,
    )
    rows = compute_dataset_features(
        contributions=contributions,
        dataset_group_id="7",
        student_id_by_user_id={"u1": "1", "u2": "2"},
        group_activity_totals=group_totals,
    )

    assert [row.student_id for row in rows] == ["1", "2"]
    assert all(row.group_id == "7" for row in rows)


def test_compute_member_features_from_contributions():
    contributions = ContributionsOut(
        group_id="internal",
        members=[
            _member("u1", commits=2, lines=80, prs=1, edits=10, comments=2, attendance=1.0),
            _member("u2", commits=2, lines=20, prs=1, edits=10, comments=2, attendance=0.5),
        ],
    )
    rows = compute_member_features_from_contributions(contributions)
    by_user = {row.user_id: row.features for row in rows}

    assert by_user["u1"]["code_commits"] == 0.5
    assert by_user["u1"]["code_share"] == 0.8
    assert by_user["u1"]["attendance_ratio"] == 1.0
    assert by_user["u2"]["code_share"] == 0.2
    assert by_user["u2"]["speaking_participation_ratio"] == 0.0
