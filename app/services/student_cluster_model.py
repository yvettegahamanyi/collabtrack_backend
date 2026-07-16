from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from app.core.config import get_settings
from app.services.dataset_features import ML_FEATURE_COLUMNS

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_INSUFFICIENT_DATA_LABEL = "Insufficient Data"


class StudentClusterModelUnavailableError(Exception):
    """Raised when student-clustering artifact files are missing or invalid."""


@dataclass(frozen=True)
class StudentClusterModelBundle:
    scaler: Any
    kmeans: Any
    all_features: list[str]
    platforms: dict[str, list[str]]
    cluster_name_map: dict[int, str]
    eps: float
    scoring_method: str = "platform_aware_team_relative_v2"


def _model_dir() -> Path:
    settings = get_settings()
    return (_BACKEND_ROOT / settings.ML_BENCHMARK_DIR).resolve()


def _load_metadata(model_dir: Path) -> dict[str, Any]:
    metadata_path = model_dir / "student_clustering/model_metadata.json"
    if not metadata_path.is_file():
        raise StudentClusterModelUnavailableError(
            f"Missing student clustering metadata at {metadata_path}"
        )
    with metadata_path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _normalize_cluster_map(raw_map: dict[Any, Any]) -> dict[int, str]:
    return {int(key): str(value) for key, value in raw_map.items()}


def _cluster_key(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return slug or "unknown_cluster"


def _bundle_path(model_dir: Path) -> Path:
    bundle_path = model_dir / "student_clustering/model_bundle.joblib"
    if bundle_path.is_file():
        return bundle_path

    legacy_path = model_dir / "student_clustering/kmeans_model.joblib"
    if legacy_path.is_file():
        return legacy_path

    raise StudentClusterModelUnavailableError(
        "Student clustering model file not found. Place "
        f"student_clustering/model_bundle.joblib in {model_dir}."
    )


def _load_bundle_from_payload(
    payload: Any, metadata: dict[str, Any]
) -> StudentClusterModelBundle:
    if not isinstance(payload, dict) or payload.get("kmeans_1d") is None:
        raise StudentClusterModelUnavailableError(
            "Student clustering bundle must contain kmeans_1d, scaler, "
            "all_features, and platforms."
        )

    scaler = payload.get("scaler")
    kmeans = payload.get("kmeans_1d")
    all_features = list(payload.get("all_features") or [])
    platforms = dict(payload.get("platforms") or metadata.get("platforms") or {})
    if scaler is None or kmeans is None or not all_features or not platforms:
        raise StudentClusterModelUnavailableError(
            "Student clustering bundle is missing required artifacts."
        )

    input_schema = dict(metadata.get("input_schema") or {})
    metadata_features = list(
        input_schema.get("required_raw_features_in_order") or ML_FEATURE_COLUMNS
    )
    if metadata_features != all_features:
        all_features = metadata_features

    cluster_name_map = _normalize_cluster_map(
        dict(payload.get("cluster_name_map") or metadata.get("cluster_name_map") or {})
    )
    if not cluster_name_map:
        raise StudentClusterModelUnavailableError(
            "Student clustering metadata must define cluster_name_map."
        )

    return StudentClusterModelBundle(
        scaler=scaler,
        kmeans=kmeans,
        all_features=all_features,
        platforms={str(key): list(cols) for key, cols in platforms.items()},
        cluster_name_map=cluster_name_map,
        eps=float(payload.get("eps", 1e-9)),
        scoring_method=str(
            payload.get("scoring_method") or "platform_aware_team_relative_v2"
        ),
    )


@lru_cache
def get_student_cluster_model_bundle() -> StudentClusterModelBundle:
    model_dir = _model_dir()
    metadata = _load_metadata(model_dir)
    payload = joblib.load(_bundle_path(model_dir))
    return _load_bundle_from_payload(payload, metadata)


def is_student_cluster_model_available() -> bool:
    try:
        get_student_cluster_model_bundle()
        return True
    except StudentClusterModelUnavailableError:
        return False


def _insufficient_data_result() -> dict[str, Any]:
    return {
        "cluster_id": -1,
        "cluster_key": _cluster_key(_INSUFFICIENT_DATA_LABEL),
        "cluster_label": _INSUFFICIENT_DATA_LABEL,
        "composite_score": 0.0,
        "active_platforms": [],
    }


def _team_feature_matrix(
    team_students: list[dict[str, float]], feature_cols: list[str]
) -> np.ndarray:
    return np.array(
        [
            [float(student.get(col, 0.0)) for col in feature_cols]
            for student in team_students
        ],
        dtype=float,
    )


def predict_team_clusters(
    team_students: list[dict[str, float]],
) -> list[dict[str, Any]]:
    """Cluster every teammate together using platform-aware team-relative scores."""
    if not team_students:
        return []

    bundle = get_student_cluster_model_bundle()
    team_matrix = _team_feature_matrix(team_students, bundle.all_features)

    active_platforms: dict[str, bool] = {}
    active_cols: list[str] = []
    for platform, cols in bundle.platforms.items():
        col_indices = [bundle.all_features.index(col) for col in cols]
        is_active = float(team_matrix[:, col_indices].sum()) > bundle.eps
        active_platforms[platform] = is_active
        if is_active:
            active_cols.extend(cols)

    if not active_cols:
        return [_insufficient_data_result() for _ in team_students]

    scaled = bundle.scaler.transform(team_matrix)
    active_indices = [bundle.all_features.index(col) for col in active_cols]
    composite = scaled[:, active_indices].mean(axis=1)
    labels = bundle.kmeans.predict(composite.reshape(-1, 1))
    active_list = [platform for platform, active in active_platforms.items() if active]

    results: list[dict[str, Any]] = []
    for cluster_id, composite_score in zip(labels, composite, strict=True):
        cluster_label = bundle.cluster_name_map.get(
            int(cluster_id), f"Cluster {cluster_id}"
        )
        results.append(
            {
                "cluster_id": int(cluster_id),
                "cluster_key": _cluster_key(cluster_label),
                "cluster_label": cluster_label,
                "composite_score": float(composite_score),
                "active_platforms": active_list,
            }
        )
    return results
