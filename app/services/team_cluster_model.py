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


class TeamClusterModelUnavailableError(Exception):
    """Raised when team-cluster artifact files are missing or invalid."""


@dataclass(frozen=True)
class TeamClusterModelBundle:
    model: Any
    scaler: Any
    team_feature_cols: list[str]
    base_feature_cols: list[str]
    cluster_archetypes: dict[int, str]
    archetype_labels: dict[str, str]


def _model_dir() -> Path:
    settings = get_settings()
    return (_BACKEND_ROOT / settings.ML_BENCHMARK_DIR).resolve()


def _load_metadata(model_dir: Path) -> dict[str, Any]:
    metadata_path = model_dir / "team_cluster/team_cluster_metadata.json"
    if not metadata_path.is_file():
        raise TeamClusterModelUnavailableError(
            f"Missing team cluster metadata at {metadata_path}"
        )
    with metadata_path.open(encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache
def get_team_cluster_model_bundle() -> TeamClusterModelBundle:
    model_dir = _model_dir()
    metadata = _load_metadata(model_dir)

    model_path = model_dir / "team_cluster/team_cluster_model.joblib"
    scaler_path = model_dir / "team_cluster/team_cluster_scaler.joblib"
    if not model_path.is_file() or not scaler_path.is_file():
        raise TeamClusterModelUnavailableError(
            "Team cluster model files not found. Place team_cluster/team_cluster_model.joblib "
            f"and team_cluster/team_cluster_scaler.joblib in {model_dir}."
        )

    team_feature_cols = list(metadata.get("team_feature_cols") or [])
    base_feature_cols = list(metadata.get("base_feature_cols") or ML_FEATURE_COLUMNS)
    if not team_feature_cols:
        raise TeamClusterModelUnavailableError(
            "team_cluster_metadata.json must define team_feature_cols."
        )

    raw_archetypes = dict(metadata.get("cluster_archetypes") or {})
    cluster_archetypes = {int(k): str(v) for k, v in raw_archetypes.items()}

    return TeamClusterModelBundle(
        model=joblib.load(model_path),
        scaler=joblib.load(scaler_path),
        team_feature_cols=team_feature_cols,
        base_feature_cols=base_feature_cols,
        cluster_archetypes=cluster_archetypes,
        archetype_labels=dict(metadata.get("archetype_labels") or {}),
    )


def is_team_cluster_model_available() -> bool:
    try:
        get_team_cluster_model_bundle()
        return True
    except TeamClusterModelUnavailableError:
        return False


def aggregate_team_features(
    member_features: list[dict[str, float]],
) -> dict[str, float]:
    """Build mean/std team vector from individual member feature dicts."""
    if not member_features:
        return {col: 0.0 for col in _expected_team_cols()}

    matrix = np.array(
        [
            [float(row.get(col, 0.0)) for col in ML_FEATURE_COLUMNS]
            for row in member_features
        ],
        dtype=float,
    )
    means = matrix.mean(axis=0)
    stds = matrix.std(axis=0)

    aggregated: dict[str, float] = {}
    for index, col in enumerate(ML_FEATURE_COLUMNS):
        aggregated[f"{col}_mean"] = float(means[index])
        aggregated[f"{col}_std"] = float(stds[index])
    return aggregated


def _expected_team_cols() -> list[str]:
    return [f"{col}_{stat}" for col in ML_FEATURE_COLUMNS for stat in ("mean", "std")]


def predict_team_archetype(
    member_features: list[dict[str, float]],
) -> dict[str, Any]:
    """Cluster a team from member profiles; returns cluster id and archetype label."""
    bundle = get_team_cluster_model_bundle()
    aggregated = aggregate_team_features(member_features)

    vector = np.array(
        [[float(aggregated.get(col, 0.0)) for col in bundle.team_feature_cols]],
        dtype=float,
    )
    scaled = bundle.scaler.transform(vector)
    cluster_id = int(bundle.model.predict(scaled)[0])
    archetype_key = bundle.cluster_archetypes.get(cluster_id, "balanced_team")
    display_label = bundle.archetype_labels.get(
        archetype_key, archetype_key.replace("_", " ").title()
    )

    return {
        "cluster_id": cluster_id,
        "archetype": archetype_key,
        "archetype_label": display_label,
        "team_features": aggregated,
    }
