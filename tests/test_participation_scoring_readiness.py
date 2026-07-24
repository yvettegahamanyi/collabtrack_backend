from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models import AccountStatus, GroupMemberRole
from app.schemas.participation import (
    ContributionsOut,
    GithubMetrics,
    GithubSyncEvent,
    GoogleDocsMetrics,
    MemberParticipationOut,
    MeetingEngagementMetrics,
)
from app.services.participation_scoring import (
    _build_llm_group_input,
    _fmt_features,
    _load_assignment_description,
    _ordered_student_memberships,
    _require_synced_participation,
    _rule,
    maybe_regenerate_scores_after_sync,
    report_delivery_readiness,
)


def _membership(user_id: str, name: str, *, role: GroupMemberRole = GroupMemberRole.STUDENT):
    return SimpleNamespace(
        user_id=user_id,
        role=role,
        user=SimpleNamespace(name=name, email=f"{user_id}@example.com"),
    )


def _contribution(user_id: str, name: str) -> MemberParticipationOut:
    return MemberParticipationOut(
        user_id=user_id,
        name=name,
        email=f"{user_id}@example.com",
        github_connected=True,
        google_connected=True,
        google_email_matched=True,
        account_status=AccountStatus.ACTIVE,
        github=GithubMetrics(total_commits=3, lines_changed=40, prs_reviewed=1),
        github_events=[
            GithubSyncEvent(
                type="commit",
                owner="org",
                repo="repo",
                message="Initial commit",
            )
        ],
        google_docs=GoogleDocsMetrics(edits=5, comments=2),
        meeting_engagement=MeetingEngagementMetrics(
            attendance_ratio=1.0,
            speaking_ratio=0.5,
            chat_participation=0.25,
        ),
    )


def test_ordered_student_memberships_sorts_students_only():
    memberships = [
        _membership("u2", "Bob"),
        _membership("u1", "Alice", role=GroupMemberRole.INSTRUCTOR),
        _membership("u3", "Carol"),
    ]

    ordered = _ordered_student_memberships(memberships)

    assert [member.user_id for member in ordered] == ["u2", "u3"]


def test_build_llm_group_input_maps_contributions_to_anonymous_refs():
    memberships = [_membership("u1", "Alice"), _membership("u2", "Bob")]
    contributions = ContributionsOut(
        group_id="g1",
        members=[_contribution("u1", "Alice"), _contribution("u2", "Bob")],
    )
    feature_by_user = {
        "u1": SimpleNamespace(features={"code_commits": 0.8, "code_share": 0.6}),
        "u2": SimpleNamespace(features={"code_commits": 0.2, "code_share": 0.4}),
    }

    group_input, member_by_ref = _build_llm_group_input(
        assignment_description="Build a capstone app",
        student_memberships=memberships,
        contributions=contributions,
        feature_by_user=feature_by_user,
        group_totals={"code_commits": 1.0},
    )

    assert group_input.member_count == 2
    assert group_input.members[0].ref == "Member A"
    assert group_input.members[0].github_connected is True
    assert group_input.members[0].github_events is not None
    assert member_by_ref["Member A"].user_id == "u1"


def test_fmt_features_formats_numeric_values():
    formatted = _fmt_features({"code_commits": 0.1234, "note": "skip"})

    assert formatted == "code_commits=0.123"


def test_rule_with_and_without_label():
    assert "Member" not in _rule()
    assert "Section" in _rule("Section")


@pytest.mark.asyncio
async def test_require_synced_participation_raises_without_snapshot():
    group = SimpleNamespace(id="g1")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc:
        await _require_synced_participation(group, db)

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_load_assignment_description_combines_assignment_and_group_text():
    group = SimpleNamespace(
        assignment_id="a1",
        description="Weekly standups required",
    )
    assignment = SimpleNamespace(
        title="Capstone",
        description="Deliver a full-stack project",
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=assignment)

    description = await _load_assignment_description(group, db)

    assert "Capstone" in description
    assert "full-stack project" in description
    assert "Weekly standups required" in description


@pytest.mark.asyncio
async def test_report_delivery_readiness_blocks_unsynced_group():
    group = SimpleNamespace(
        id="g1",
        participation_scores_generated_at=None,
    )
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[None, 0, 0])

    ready, blockers, warnings = await report_delivery_readiness(group, db)

    assert ready is False
    assert "participation_not_synced" in blockers
    assert "scores_not_generated" in blockers
    assert warnings == []


@pytest.mark.asyncio
async def test_report_delivery_readiness_warns_when_github_unmatched_after_sync():
    synced_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    group = SimpleNamespace(
        id="g1",
        participation_scores_generated_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[synced_at, 1, 0])

    snapshot = SimpleNamespace(metrics={"google_docs": {}})
    scalars_result = MagicMock()
    scalars_result.all.return_value = [snapshot]
    db.scalars = AsyncMock(return_value=scalars_result)

    ready, blockers, warnings = await report_delivery_readiness(group, db)

    assert ready is True
    assert blockers == []
    assert any("GitHub repository" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_report_delivery_readiness_is_ready_when_fully_current():
    synced_at = datetime(2026, 1, 3, tzinfo=timezone.utc)
    group = SimpleNamespace(
        id="g1",
        participation_scores_generated_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[synced_at, 0, 0])

    ready, blockers, warnings = await report_delivery_readiness(group, db)

    assert ready is True
    assert blockers == []
    assert warnings == []


@pytest.mark.asyncio
async def test_report_delivery_readiness_marks_stale_scores_as_blocker():
    synced_at = datetime(2026, 1, 4, tzinfo=timezone.utc)
    group = SimpleNamespace(
        id="g1",
        participation_scores_generated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[synced_at, 0, 0])

    ready, blockers, warnings = await report_delivery_readiness(group, db)

    assert ready is False
    assert "scores_stale" in blockers
    assert warnings == []


@pytest.mark.asyncio
async def test_maybe_regenerate_scores_after_sync_skips_when_not_stale():
    group = SimpleNamespace(
        id="g1",
        participation_scores_generated_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=datetime(2026, 1, 2, tzinfo=timezone.utc))

    warnings = await maybe_regenerate_scores_after_sync(group, db)

    assert warnings == []
