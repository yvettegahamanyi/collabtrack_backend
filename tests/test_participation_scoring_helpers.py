from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.participation_scoring import (
    ParticipationScoreResult,
    ParticipationScoresSummary,
    _classify_score,
    _participation_scores_are_stale,
    enrich_scores_summary_with_ml_insights,
    tier_display_label,
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


@pytest.mark.parametrize(
    "tier,label",
    [
        ("strong", "Strong contributor"),
        ("average", "Average contributor"),
        ("below", "Below average contributor"),
        ("unknown", "Below average contributor"),
    ],
)
def test_tier_display_label(tier, label):
    assert tier_display_label(tier) == label


def test_classify_score_uses_benchmark_model_when_available():
    with patch(
        "app.services.participation_scoring.classify_contributor",
        return_value="strong",
    ):
        assert _classify_score(0.9) == "strong"


def test_classify_score_falls_back_when_model_unavailable():
    from app.services.benchmark_model import BenchmarkModelUnavailableError

    with patch(
        "app.services.participation_scoring.classify_contributor",
        side_effect=BenchmarkModelUnavailableError("missing"),
    ):
        assert _classify_score(0.75) == "strong"
        assert _classify_score(0.55) == "average"
        assert _classify_score(0.25) == "below"


def test_participation_scores_are_stale_when_sync_is_newer():
    group = SimpleNamespace(
        participation_scores_generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    last_synced_at = datetime(2026, 1, 2, tzinfo=timezone.utc)

    assert _participation_scores_are_stale(group, last_synced_at) is True


def test_participation_scores_are_not_stale_without_timestamps():
    group = SimpleNamespace(participation_scores_generated_at=None)

    assert _participation_scores_are_stale(group, None) is False


def test_enrich_scores_summary_returns_empty_summary_unchanged():
    summary = ParticipationScoresSummary(
        group_id="g1",
        generated_at=datetime.now(timezone.utc),
        scores=[],
        warnings=["none"],
    )

    enriched = enrich_scores_summary_with_ml_insights(summary)

    assert enriched.scores == []
    assert enriched.warnings == ["none"]


def test_enrich_scores_summary_adds_outlier_and_student_cluster(monkeypatch):
    summary = ParticipationScoresSummary(
        group_id="g1",
        generated_at=datetime.now(timezone.utc),
        scores=[_score("u1", 0.72)],
        warnings=[],
    )

    monkeypatch.setattr(
        "app.services.participation_scoring.is_outlier_model_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.participation_scoring.detect_student_outlier",
        lambda features: {
            "is_outlier": True,
            "anomaly_score": 0.9,
            "outlier_type": "free_rider",
        },
    )
    monkeypatch.setattr(
        "app.services.participation_scoring.is_student_cluster_model_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.participation_scoring.predict_team_clusters",
        lambda team_students: [
            {
                "cluster_id": 2,
                "cluster_key": "normal_contributor",
                "cluster_label": "Normal Contributor",
                "composite_score": 0.31,
                "active_platforms": ["code", "meetings"],
            }
            for _ in team_students
        ],
    )

    enriched = enrich_scores_summary_with_ml_insights(summary)

    assert enriched.scores[0].outlier.is_outlier is True
    assert enriched.scores[0].student_cluster.cluster_label == "Normal Contributor"
    assert enriched.scores[0].student_cluster.composite_score == 0.31
