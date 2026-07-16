import json
from pathlib import Path

import joblib
import numpy as np
import pytest
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from app.services import student_cluster_model
from app.services.dataset_features import ML_FEATURE_COLUMNS


@pytest.fixture(autouse=True)
def clear_model_cache():
    student_cluster_model.get_student_cluster_model_bundle.cache_clear()
    yield
    student_cluster_model.get_student_cluster_model_bundle.cache_clear()


def _write_mock_artifacts(model_dir: Path) -> None:
    platforms = {
        "code": ["code_commits", "code_share", "review_participation"],
        "meetings": ["attendance_ratio", "speaking_participation_ratio"],
        "communication": [
            "chat_participation_ratio",
            "docs_contribution_share",
            "comment_activity",
        ],
    }
    metadata = {
        "model_type": "KMeans (platform-aware composite score)",
        "version": "v2",
        "n_clusters": 3,
        "platforms": platforms,
        "input_schema": {
            "required_raw_features_in_order": list(ML_FEATURE_COLUMNS),
        },
        "cluster_name_map": {
            "0": "Free-Rider",
            "1": "Over-Contributor",
            "2": "Normal Contributor",
        },
    }
    (model_dir / "student_clustering").mkdir(parents=True)
    (model_dir / "student_clustering/model_metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )

    rng = np.random.default_rng(11)
    samples = rng.random((30, len(ML_FEATURE_COLUMNS)))
    scaler = StandardScaler()
    scaled = scaler.fit_transform(samples)
    composite = scaled.mean(axis=1, keepdims=True)
    kmeans = KMeans(n_clusters=3, random_state=11, n_init=10)
    kmeans.fit(composite)

    joblib.dump(
        {
            "scaler": scaler,
            "kmeans_1d": kmeans,
            "all_features": list(ML_FEATURE_COLUMNS),
            "platforms": platforms,
            "cluster_name_map": {
                0: "Free-Rider",
                1: "Over-Contributor",
                2: "Normal Contributor",
            },
            "eps": 1e-9,
            "scoring_method": "platform_aware_team_relative_v2",
        },
        model_dir / "student_clustering/model_bundle.joblib",
    )


def _features(**overrides: float) -> dict[str, float]:
    base = {col: 0.0 for col in ML_FEATURE_COLUMNS}
    base.update(overrides)
    return base


def test_is_student_cluster_model_available_with_mock_artifacts(
    monkeypatch, tmp_path: Path
):
    _write_mock_artifacts(tmp_path)
    monkeypatch.setattr(student_cluster_model, "_model_dir", lambda: tmp_path)

    assert student_cluster_model.is_student_cluster_model_available() is True


def test_predict_team_clusters_returns_one_result_per_student(
    monkeypatch, tmp_path: Path
):
    _write_mock_artifacts(tmp_path)
    monkeypatch.setattr(student_cluster_model, "_model_dir", lambda: tmp_path)

    team = [
        _features(code_commits=0.05, code_share=0.05),
        _features(code_commits=0.8, code_share=0.8, attendance_ratio=0.8),
        _features(code_commits=0.25, code_share=0.25, attendance_ratio=0.8),
    ]
    results = student_cluster_model.predict_team_clusters(team)

    assert len(results) == 3
    for result in results:
        assert "cluster_id" in result
        assert "cluster_key" in result
        assert "cluster_label" in result
        assert "composite_score" in result
        assert "active_platforms" in result


def test_predict_team_clusters_marks_insufficient_data_when_no_platforms(
    monkeypatch, tmp_path: Path
):
    _write_mock_artifacts(tmp_path)
    monkeypatch.setattr(student_cluster_model, "_model_dir", lambda: tmp_path)

    team = [_features(), _features()]
    results = student_cluster_model.predict_team_clusters(team)

    assert len(results) == 2
    assert all(result["cluster_label"] == "Insufficient Data" for result in results)
    assert all(result["active_platforms"] == [] for result in results)


def test_predict_team_clusters_with_real_artifacts():
    team = [
        _features(code_commits=0.05, code_share=0.05),
        _features(
            code_commits=0.8,
            code_share=0.8,
            attendance_ratio=0.8,
            speaking_participation_ratio=0.8,
            chat_participation_ratio=0.8,
            docs_contribution_share=0.8,
        ),
        _features(code_commits=0.25, code_share=0.25, attendance_ratio=0.8),
        _features(code_commits=0.25, code_share=0.25, attendance_ratio=0.8),
    ]
    results = student_cluster_model.predict_team_clusters(team)

    assert len(results) == 4
    for result in results:
        assert result["cluster_label"]
        assert "composite_score" in result
        assert isinstance(result["active_platforms"], list)

    labels = {result["cluster_label"] for result in results}
    assert len(labels) >= 2


def test_cluster_key_slugifies_label():
    assert student_cluster_model._cluster_key("Free-Rider") == "free_rider"
    assert (
        student_cluster_model._cluster_key("Normal Contributor")
        == "normal_contributor"
    )
    assert (
        student_cluster_model._cluster_key("Over-Contributor")
        == "over_contributor"
    )
