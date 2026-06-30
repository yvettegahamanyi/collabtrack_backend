import json
from pathlib import Path

import joblib
import numpy as np
import pytest
from app.services import team_cluster_model
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler


@pytest.fixture(autouse=True)
def clear_model_cache():
    team_cluster_model.get_team_cluster_model_bundle.cache_clear()
    yield
    team_cluster_model.get_team_cluster_model_bundle.cache_clear()


def _write_mock_artifacts(model_dir: Path) -> None:
    base_cols = [
        "code_commits",
        "code_share",
        "review_participation",
        "attendance_ratio",
        "speaking_participation_ratio",
        "chat_participation_ratio",
        "docs_contribution_share",
        "comment_activity",
    ]
    team_feature_cols = [f"{col}_{stat}" for col in base_cols for stat in ("mean", "std")]
    metadata = {
        "team_feature_cols": team_feature_cols,
        "base_feature_cols": base_cols,
        "cluster_archetypes": {"0": "balanced_team", "1": "one_dominant_contributor"},
        "archetype_labels": {
            "balanced_team": "Balanced team",
            "one_dominant_contributor": "One dominant contributor",
        },
    }
    (model_dir / "team_cluster/team_cluster_metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )

    rng = np.random.default_rng(42)
    samples = rng.random((20, len(team_feature_cols)))
    scaler = StandardScaler()
    scaled = scaler.fit_transform(samples)
    model = KMeans(n_clusters=2, random_state=42, n_init=10)
    model.fit(scaled)

    joblib.dump(model, model_dir / "team_cluster/team_cluster_model.joblib")
    joblib.dump(scaler, model_dir / "team_cluster/team_cluster_scaler.joblib")


def test_predict_team_archetype(monkeypatch, tmp_path: Path):
    _write_mock_artifacts(tmp_path)
    monkeypatch.setattr(team_cluster_model, "_model_dir", lambda: tmp_path)

    members = [
        {
            "code_commits": 0.3,
            "code_share": 0.3,
            "review_participation": 0.2,
            "attendance_ratio": 0.8,
            "speaking_participation_ratio": 0.4,
            "chat_participation_ratio": 0.2,
            "docs_contribution_share": 0.3,
            "comment_activity": 0.1,
        },
        {
            "code_commits": 0.2,
            "code_share": 0.2,
            "review_participation": 0.1,
            "attendance_ratio": 0.7,
            "speaking_participation_ratio": 0.3,
            "chat_participation_ratio": 0.1,
            "docs_contribution_share": 0.2,
            "comment_activity": 0.0,
        },
    ]
    result = team_cluster_model.predict_team_archetype(members)

    assert "cluster_id" in result
    assert "archetype" in result
    assert "archetype_label" in result
    assert len(result["team_features"]) == 16


def test_missing_team_cluster_artifacts_raises(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(team_cluster_model, "_model_dir", lambda: tmp_path)

    with pytest.raises(team_cluster_model.TeamClusterModelUnavailableError):
        team_cluster_model.get_team_cluster_model_bundle()

    assert team_cluster_model.is_team_cluster_model_available() is False
