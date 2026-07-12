import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import joblib
import numpy as np
import pytest
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from app.services import outlier_model
from app.services.dataset_features import ML_FEATURE_COLUMNS


@pytest.fixture(autouse=True)
def clear_model_cache():
    outlier_model.get_outlier_model_bundle.cache_clear()
    yield
    outlier_model.get_outlier_model_bundle.cache_clear()


def _write_mock_artifacts(model_dir: Path) -> None:
    metadata = {
        "feature_cols": ML_FEATURE_COLUMNS,
        "contamination": 0.1,
        "classification": {
            "free_rider_max_ratio": 0.35,
            "over_contributor_min_ratio": 1.65,
            "peer_median_feature_total": 2.0,
        },
    }
    (model_dir / "outlier").mkdir(parents=True)
    (model_dir / "outlier/outlier_metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )

    rng = np.random.default_rng(7)
    samples = rng.random((30, len(ML_FEATURE_COLUMNS)))
    scaler = StandardScaler()
    scaled = scaler.fit_transform(samples)
    model = IsolationForest(random_state=7, contamination=0.1)
    model.fit(scaled)

    joblib.dump(model, model_dir / "outlier/outlier_model.joblib")
    joblib.dump(scaler, model_dir / "outlier/outlier_scaler.joblib")


def _features(**overrides: float) -> dict[str, float]:
    base = {col: 0.0 for col in ML_FEATURE_COLUMNS}
    base.update(overrides)
    return base


def test_is_outlier_model_available_with_mock_artifacts(monkeypatch, tmp_path: Path):
    _write_mock_artifacts(tmp_path)
    monkeypatch.setattr(outlier_model, "_model_dir", lambda: tmp_path)

    assert outlier_model.is_outlier_model_available() is True


def test_detect_student_outlier_returns_typed_result(monkeypatch, tmp_path: Path):
    _write_mock_artifacts(tmp_path)
    monkeypatch.setattr(outlier_model, "_model_dir", lambda: tmp_path)

    result = outlier_model.detect_student_outlier(_features(code_commits=0.5))

    assert "is_outlier" in result
    assert "anomaly_score" in result
    assert result["outlier_type"] in {
        "typical",
        "free_rider",
        "over_contributor",
        "unusual_profile",
    }


@pytest.mark.parametrize(
    "features,expected",
    [
        (_features(code_commits=0.01), "free_rider"),
        (_features(code_commits=2.0, code_share=2.0), "over_contributor"),
        (_features(code_commits=0.5, code_share=0.5), "unusual_profile"),
    ],
)
def test_classify_outlier_type(features, expected):
    assert (
        outlier_model._classify_outlier_type(
            features,
            peer_median_total=2.0,
            free_rider_max_ratio=0.35,
            over_contributor_min_ratio=1.65,
        )
        == expected
    )
