import json
from pathlib import Path

import joblib
import numpy as np
import pytest
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from app.services import benchmark_model


@pytest.fixture(autouse=True)
def clear_model_cache():
    benchmark_model.get_benchmark_model_bundle.cache_clear()
    yield
    benchmark_model.get_benchmark_model_bundle.cache_clear()


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
        "score_range": [0.0, 1.0],
        "thresholds": {
            "strong_contributor": 0.7,
            "average_contributor": 0.5,
        },
    }
    (model_dir / "model_metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )

    rng = np.random.default_rng(42)
    samples = rng.random((20, len(feature_cols)))
    targets = samples.mean(axis=1)
    scaler = StandardScaler()
    scaled = scaler.fit_transform(samples)
    model = Ridge()
    model.fit(scaled, targets)

    joblib.dump(model, model_dir / "best_model.joblib")
    joblib.dump(scaler, model_dir / "scaler.joblib")


def test_predict_benchmark_score_clips_to_range(monkeypatch, tmp_path: Path):
    _write_mock_artifacts(tmp_path)
    monkeypatch.setattr(benchmark_model, "_model_dir", lambda: tmp_path)

    features = {
        "code_commits": 0.5,
        "code_share": 0.5,
        "review_participation": 0.2,
        "attendance_ratio": 0.8,
        "speaking_participation_ratio": 0.3,
        "chat_participation_ratio": 0.1,
        "docs_contribution_share": 0.4,
        "comment_activity": 0.2,
    }
    score = benchmark_model.predict_benchmark_score(features)

    assert 0.0 <= score <= 1.0


def test_classify_contributor_tiers(monkeypatch, tmp_path: Path):
    _write_mock_artifacts(tmp_path)
    monkeypatch.setattr(benchmark_model, "_model_dir", lambda: tmp_path)

    assert benchmark_model.classify_contributor(0.85) == "strong"
    assert benchmark_model.classify_contributor(0.6) == "average"
    assert benchmark_model.classify_contributor(0.3) == "below"


def test_missing_artifacts_raises(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(benchmark_model, "_model_dir", lambda: tmp_path)

    with pytest.raises(benchmark_model.BenchmarkModelUnavailableError):
        benchmark_model.get_benchmark_model_bundle()

    assert benchmark_model.is_benchmark_model_available() is False


def test_missing_joblib_files_raises(monkeypatch, tmp_path: Path):
    (tmp_path / "model_metadata.json").write_text(
        json.dumps({"feature_cols": ["code_commits"], "score_range": [0.0, 1.0]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(benchmark_model, "_model_dir", lambda: tmp_path)

    with pytest.raises(benchmark_model.BenchmarkModelUnavailableError, match="ML model files"):
        benchmark_model.get_benchmark_model_bundle()


def test_missing_feature_columns_raises(monkeypatch, tmp_path: Path):
    _write_mock_artifacts(tmp_path)
    (tmp_path / "model_metadata.json").write_text(
        json.dumps({"feature_cols": [], "score_range": [0.0, 1.0]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(benchmark_model, "_model_dir", lambda: tmp_path)

    with pytest.raises(benchmark_model.BenchmarkModelUnavailableError, match="feature_cols"):
        benchmark_model.get_benchmark_model_bundle()
