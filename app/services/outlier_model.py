from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from app.core.config import get_settings
from app.services.dataset_features import ML_FEATURE_COLUMNS

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


class OutlierModelUnavailableError(Exception):
    """Raised when outlier-detector artifact files are missing or invalid."""


@dataclass(frozen=True)
class OutlierModelBundle:
    model: Any
    scaler: Any
    feature_cols: list[str]
    contamination: float
    free_rider_max_ratio: float
    over_contributor_min_ratio: float
    peer_median_feature_total: float


def _model_dir() -> Path:
    settings = get_settings()
    return (_BACKEND_ROOT / settings.ML_BENCHMARK_DIR).resolve()


def _load_metadata(model_dir: Path) -> dict[str, Any]:
    metadata_path = model_dir / "outlier/outlier_metadata.json"
    if not metadata_path.is_file():
        raise OutlierModelUnavailableError(
            f"Missing outlier metadata at {metadata_path}"
        )
    with metadata_path.open(encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache
def get_outlier_model_bundle() -> OutlierModelBundle:
    model_dir = _model_dir()
    metadata = _load_metadata(model_dir)

    model_path = model_dir / "outlier/outlier_model.joblib"
    scaler_path = model_dir / "outlier/outlier_scaler.joblib"
    if not model_path.is_file() or not scaler_path.is_file():
        raise OutlierModelUnavailableError(
            "Outlier model files not found. Place outlier/outlier_model.joblib and "
            f"outlier/outlier_scaler.joblib in {model_dir}."
        )

    feature_cols = list(metadata.get("feature_cols") or [])
    if not feature_cols:
        raise OutlierModelUnavailableError(
            "outlier_metadata.json must define feature_cols."
        )

    classification = dict(metadata.get("classification") or {})
    return OutlierModelBundle(
        model=joblib.load(model_path),
        scaler=joblib.load(scaler_path),
        feature_cols=feature_cols,
        contamination=float(metadata.get("contamination", 0.1)),
        free_rider_max_ratio=float(classification.get("free_rider_max_ratio", 0.35)),
        over_contributor_min_ratio=float(
            classification.get("over_contributor_min_ratio", 1.65)
        ),
        peer_median_feature_total=float(
            classification.get("peer_median_feature_total", 2.0)
        ),
    )


def is_outlier_model_available() -> bool:
    try:
        get_outlier_model_bundle()
        return True
    except OutlierModelUnavailableError:
        return False


def _classify_outlier_type(
    features: dict[str, float],
    *,
    peer_median_total: float,
    free_rider_max_ratio: float,
    over_contributor_min_ratio: float,
) -> str:
    total = sum(float(features.get(col, 0.0)) for col in ML_FEATURE_COLUMNS)
    if total <= peer_median_total * free_rider_max_ratio:
        return "free_rider"
    if total >= peer_median_total * over_contributor_min_ratio:
        return "over_contributor"
    return "unusual_profile"


def detect_student_outlier(features: dict[str, float]) -> dict[str, Any]:
    """Return outlier flag, anomaly score, and sub-type for one student profile."""
    bundle = get_outlier_model_bundle()
    vector = np.array(
        [[float(features.get(col, 0.0)) for col in bundle.feature_cols]],
        dtype=float,
    )
    scaled = bundle.scaler.transform(vector)
    flag = int(bundle.model.predict(scaled)[0])
    score = float(-bundle.model.score_samples(scaled)[0])
    is_outlier = flag == -1

    outlier_type = "typical"
    if is_outlier:
        outlier_type = _classify_outlier_type(
            features,
            peer_median_total=bundle.peer_median_feature_total,
            free_rider_max_ratio=bundle.free_rider_max_ratio,
            over_contributor_min_ratio=bundle.over_contributor_min_ratio,
        )

    return {
        "is_outlier": is_outlier,
        "anomaly_score": score,
        "outlier_type": outlier_type,
    }
