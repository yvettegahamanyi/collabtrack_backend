import json
from pathlib import Path

import joblib
import numpy as np
import pytest
from app.services import outlier_model
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler


@pytest.fixture(autouse=True)
def clear_model_cache():
    outlier_model.get_outlier_model_bundle.cache_clear()
    yield
    outlier_model.get_outlier_model_bundle.cache_clear()


def _write_mock_artifacts(model_dir: Path) -> None:
    feature_cols = [
        "code_commits",
        "code_share",
        "review_participation",
        "attendance_ratio",
        "speaking_participation_ratio",
        "chat_participation_ratio",
        "docs_contribution_share",
        "comment_activity",
    ]
    metadata = {
        "feature_cols": feature_cols,
        "contamination": 0.1,
        "classification": {
            "free_rider_max_ratio": 0.35,
            "over_contributor_min_ratio": 1.65,
            "peer_median_feature_total": 2.0,
        },
    }
    (model_dir / "outlier/outlier_metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )

    rng = np.random.default_rng(42)
    samples = rng.random((30, len(feature_cols)))
    scaler = StandardScaler()
    scaled = scaler.fit_transform(samples)
    model = IsolationForest(random_state=42, contamination=0.1)
    model.fit(scaled)

    joblib.dump(model, model_dir / "outlier/outlier_model.joblib")
    joblib.dump(scaler, model_dir / "outlier/outlier_scaler.joblib")


def test_detect_student_outlier_returns_expected_keys(monkeypatch, tmp_path: Path):
    _write_mock_artifacts(tmp_path)
    monkeypatch.setattr(outlier_model, "_model_dir", lambda: tmp_path)

    result = outlier_model.detect_student_outlier(
        {
            "code_commits": 0.0,
            "code_share": 0.0,
            "review_participation": 0.0,
            "attendance_ratio": 0.0,
            "speaking_participation_ratio": 0.0,
            "chat_participation_ratio": 0.0,
            "docs_contribution_share": 0.0,
            "comment_activity": 0.0,
        }
    )

    assert set(result) == {"is_outlier", "anomaly_score", "outlier_type"}
    assert result["outlier_type"] in {
        "typical",
        "free_rider",
        "over_contributor",
        "unusual_profile",
    }


def test_missing_outlier_artifacts_raises(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(outlier_model, "_model_dir", lambda: tmp_path)

    with pytest.raises(outlier_model.OutlierModelUnavailableError):
        outlier_model.get_outlier_model_bundle()

    assert outlier_model.is_outlier_model_available() is False
