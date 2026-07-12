import json
from pathlib import Path

import joblib
import numpy as np
import pytest
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from app.services import team_cluster_model
from app.services.dataset_features import ML_FEATURE_COLUMNS


@pytest.fixture(autouse=True)
def clear_model_cache():
    team_cluster_model.get_team_cluster_model_bundle.cache_clear()
    yield
    team_cluster_model.get_team_cluster_model_bundle.cache_clear()


def _write_mock_artifacts(model_dir: Path) -> None:
    team_feature_cols = [
        f"{col}_{stat}" for col in ML_FEATURE_COLUMNS for stat in ("mean", "std")
    ]
    metadata = {
        "team_feature_cols": team_feature_cols,
        "base_feature_cols": ML_FEATURE_COLUMNS,
        "cluster_archetypes": {"0": "balanced_team", "1": "code_heavy_team"},
        "archetype_labels": {
            "balanced_team": "Balanced Team",
            "code_heavy_team": "Code Heavy Team",
        },
    }
    (model_dir / "team_cluster").mkdir(parents=True)
    (model_dir / "team_cluster/team_cluster_metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )

    rng = np.random.default_rng(11)
    samples = rng.random((20, len(team_feature_cols)))
    scaler = StandardScaler()
    scaled = scaler.fit_transform(samples)
    model = KMeans(n_clusters=2, random_state=11, n_init=10)
    model.fit(scaled)

    joblib.dump(model, model_dir / "team_cluster/team_cluster_model.joblib")
    joblib.dump(scaler, model_dir / "team_cluster/team_cluster_scaler.joblib")


def _member_features(**overrides: float) -> dict[str, float]:
    base = {col: 0.25 for col in ML_FEATURE_COLUMNS}
    base.update(overrides)
    return base


def test_aggregate_team_features_builds_mean_and_std_columns():
    members = [
        _member_features(code_commits=0.2),
        _member_features(code_commits=0.6),
    ]

    aggregated = team_cluster_model.aggregate_team_features(members)

    assert aggregated["code_commits_mean"] == pytest.approx(0.4)
    assert aggregated["code_commits_std"] == pytest.approx(0.2)
    assert len(aggregated) == len(ML_FEATURE_COLUMNS) * 2


def test_aggregate_team_features_returns_zeros_for_empty_team():
    aggregated = team_cluster_model.aggregate_team_features([])

    assert aggregated["code_commits_mean"] == 0.0
    assert aggregated["code_commits_std"] == 0.0


def test_predict_team_archetype_with_mock_artifacts(monkeypatch, tmp_path: Path):
    _write_mock_artifacts(tmp_path)
    monkeypatch.setattr(team_cluster_model, "_model_dir", lambda: tmp_path)

    result = team_cluster_model.predict_team_archetype(
        [_member_features(), _member_features(code_commits=0.8)]
    )

    assert "cluster_id" in result
    assert result["archetype"] in {"balanced_team", "code_heavy_team"}
    assert result["archetype_label"]
    assert "team_features" in result
