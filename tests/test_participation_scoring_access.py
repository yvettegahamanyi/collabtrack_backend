from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.participation_scoring import (
    ParticipationScoreResult,
    ParticipationScoresSummary,
    get_member_participation_score,
    get_participation_scores_for_group,
    try_generate_participation_scores_for_report,
)


def _score(user_id: str) -> ParticipationScoreResult:
    features = {key: 0.5 for key in [
        "code_commits", "code_share", "review_participation", "attendance_ratio",
        "speaking_participation_ratio", "chat_participation_ratio",
        "docs_contribution_share", "comment_activity",
    ]}
    return ParticipationScoreResult(
        user_id=user_id,
        name=user_id,
        predicted_score=0.6,
        contributor_tier="average",
        features=features,
        generated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_get_participation_scores_for_group_returns_empty_when_missing():
    group = SimpleNamespace(id="g1", participation_scores_generated_at=None)
    db = AsyncMock()

    summary = await get_participation_scores_for_group(
        group, db, viewer_user_id="u1", viewer_is_manager=False
    )

    assert summary.scores == []


@pytest.mark.asyncio
async def test_get_participation_scores_for_group_filters_for_student_view(monkeypatch):
    group = SimpleNamespace(
        id="g1",
        participation_scores_generated_at=datetime.now(timezone.utc),
    )
    existing = ParticipationScoresSummary(
        group_id="g1",
        generated_at=datetime.now(timezone.utc),
        scores=[_score("u1"), _score("u2")],
        warnings=[],
    )
    db = AsyncMock()
    monkeypatch.setattr(
        "app.services.participation_scoring._load_existing_scores",
        AsyncMock(return_value=existing),
    )
    monkeypatch.setattr(
        "app.services.participation_scoring.is_student_cluster_model_available",
        lambda: False,
    )

    summary = await get_participation_scores_for_group(
        group, db, viewer_user_id="u1", viewer_is_manager=False
    )

    assert len(summary.scores) == 1
    assert summary.scores[0].user_id == "u1"


@pytest.mark.asyncio
async def test_get_member_participation_score_returns_matching_member(monkeypatch):
    group = SimpleNamespace(id="g1")
    row = SimpleNamespace(
        user_id="u1",
        predicted_score=0.7,
        contributor_tier="strong",
        features={"code_commits": 0.8},
        generated_at=datetime.now(timezone.utc),
        llm_rationale={"summary": "Strong code contributor"},
        user=SimpleNamespace(name="Alice"),
    )
    scalars_result = MagicMock()
    scalars_result.all.return_value = [row]
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=scalars_result)
    monkeypatch.setattr(
        "app.services.participation_scoring.is_student_cluster_model_available",
        lambda: False,
    )

    score = await get_member_participation_score(group, "u1", db)

    assert score is not None
    assert score.user_id == "u1"
    assert score.llm_rationale == {"summary": "Strong code contributor"}


@pytest.mark.asyncio
async def test_get_member_participation_score_returns_none_when_missing():
    group = SimpleNamespace(id="g1")
    scalars_result = MagicMock()
    scalars_result.all.return_value = []
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=scalars_result)

    score = await get_member_participation_score(group, "u1", db)

    assert score is None


@pytest.mark.asyncio
async def test_try_generate_participation_scores_skips_when_already_generated():
    group = SimpleNamespace(
        id="g1",
        participation_scores_generated_at=datetime.now(timezone.utc),
    )
    db = AsyncMock()

    warnings = await try_generate_participation_scores_for_report(group, db)

    assert warnings == []
