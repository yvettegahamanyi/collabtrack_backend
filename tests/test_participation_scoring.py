from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.participation_scoring import (
    ParticipationScoreResult,
    _append_dataset_rows,
)


def _score(user_id: str, predicted: float) -> ParticipationScoreResult:
    features = {
        "code_commits": 0.5,
        "code_share": 0.5,
        "review_participation": 0.0,
        "attendance_ratio": 0.0,
        "speaking_participation_ratio": 0.0,
        "chat_participation_ratio": 0.0,
        "docs_contribution_share": 0.0,
        "comment_activity": 0.0,
    }
    return ParticipationScoreResult(
        user_id=user_id,
        name=user_id,
        predicted_score=predicted,
        contributor_tier="average",
        features=features,
        generated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_append_dataset_rows_skips_when_already_exported():
    group = MagicMock()
    group.dataset_exported_at = datetime.now(timezone.utc)
    group.dataset_group_id = "5"
    db = AsyncMock()

    await _append_dataset_rows(
        group=group,
        db=db,
        score_by_user_id={"u1": _score("u1", 0.6)},
        student_id_by_user_id={"u1": "1"},
    )

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_append_dataset_rows_allocates_group_id_and_inserts_rows():
    group = MagicMock()
    group.dataset_exported_at = None
    group.dataset_group_id = None
    db = AsyncMock()

    with patch(
        "app.services.participation_scoring.allocate_dataset_group_id",
        new=AsyncMock(return_value="9"),
    ):
        await _append_dataset_rows(
            group=group,
            db=db,
            score_by_user_id={"u1": _score("u1", 0.72)},
            student_id_by_user_id={"u1": "1"},
        )

    assert group.dataset_group_id == "9"
    assert group.dataset_exported_at is not None
    assert db.add.call_count == 2
