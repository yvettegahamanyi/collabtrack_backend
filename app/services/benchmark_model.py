from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from app.core.config import get_settings

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


class BenchmarkModelUnavailableError(Exception):
    """Raised when ML artifact files are missing or invalid."""


@dataclass(frozen=True)
class BenchmarkModelBundle:
    model: Any
    scaler: Any
    feature_cols: list[str]
    score_min: float
    score_max: float
    thresholds: dict[str, float]


def _model_dir() -> Path:
    settings = get_settings()
    return (_BACKEND_ROOT / settings.ML_BENCHMARK_DIR).resolve()


def _load_metadata(model_dir: Path) -> dict[str, Any]:
    metadata_path = model_dir / "model_metadata.json"
    if not metadata_path.is_file():
        raise BenchmarkModelUnavailableError(
            f"Missing model metadata at {metadata_path}"
        )
    with metadata_path.open(encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache
def get_benchmark_model_bundle() -> BenchmarkModelBundle:
    model_dir = _model_dir()
    metadata = _load_metadata(model_dir)

    model_path = model_dir / "best_model.joblib"
    scaler_path = model_dir / "scaler.joblib"
    if not model_path.is_file() or not scaler_path.is_file():
        raise BenchmarkModelUnavailableError(
            "ML model files not found. Place best_model.joblib and scaler.joblib "
            f"in {model_dir}."
        )

    feature_cols = list(metadata.get("feature_cols") or [])
    if not feature_cols:
        raise BenchmarkModelUnavailableError(
            "model_metadata.json must define feature_cols."
        )

    score_range = metadata.get("score_range") or [0.0, 1.0]
    score_min = float(score_range[0])
    score_max = float(score_range[1]) if len(score_range) > 1 else 1.0
    thresholds = dict(metadata.get("thresholds") or {})

    return BenchmarkModelBundle(
        model=joblib.load(model_path),
        scaler=joblib.load(scaler_path),
        feature_cols=feature_cols,
        score_min=score_min,
        score_max=score_max,
        thresholds=thresholds,
    )


def is_benchmark_model_available() -> bool:
    try:
        get_benchmark_model_bundle()
        return True
    except BenchmarkModelUnavailableError:
        return False


def predict_benchmark_score(features: dict[str, float]) -> float:
    bundle = get_benchmark_model_bundle()
    vector = np.array(
        [[float(features.get(col, 0.0)) for col in bundle.feature_cols]],
        dtype=float,
    )
    scaled = bundle.scaler.transform(vector)
    raw = float(bundle.model.predict(scaled)[0])
    return float(np.clip(raw, bundle.score_min, bundle.score_max))


def classify_contributor(score: float) -> str:
    bundle = get_benchmark_model_bundle()
    strong = float(bundle.thresholds.get("strong_contributor", 0.7))
    average = float(bundle.thresholds.get("average_contributor", 0.5))
    if score >= strong:
        return "strong"
    if score >= average:
        return "average"
    return "below"
