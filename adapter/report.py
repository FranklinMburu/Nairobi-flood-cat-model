"""Adaptation report: audit trail for all transformations."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import AdapterConfig


@dataclass
class ColumnMappingEntry:
    source_column: str
    target_field: str
    confidence: float
    method: str  # "ml", "exact", "heuristic", "user"
    transform: str | None = None


@dataclass
class InferredValueEntry:
    row: int
    field: str
    value: Any
    confidence: float
    method: str  # "ml", "raster_lookup", "sequential"
    features_used: list[str] = field(default_factory=list)


@dataclass
class DerivedValueEntry:
    row: int
    field: str
    value: Any
    method: str  # "sequential", "raster_lookup", "unit_conversion"
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class FlaggedRowEntry:
    row: int
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class UserConfirmationEntry:
    field: str
    source_column: str | None
    proposed_value: Any
    confirmed: bool
    confidence: float


@dataclass
class TransformEntry:
    field: str
    operation: str
    before_sample: list[Any]
    after_sample: list[Any]
    parameters: dict[str, Any]
    rows_affected: int


@dataclass
class ModelInfo:
    name: str
    version: str
    sha256: str
    sklearn_version: str
    python_version: str
    training_date: str
    caveats: list[str] = field(default_factory=list)


@dataclass
class AdaptationReport:
    """Complete audit trail of the adaptation process."""
    adapter_version: str = "1.0.0"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    original_file: dict[str, Any] = field(default_factory=dict)
    adapted_file: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    column_mappings: list[ColumnMappingEntry] = field(default_factory=list)
    inferred_values: list[InferredValueEntry] = field(default_factory=list)
    derived_values: list[DerivedValueEntry] = field(default_factory=list)
    flagged_rows: list[FlaggedRowEntry] = field(default_factory=list)
    user_confirmations: list[UserConfirmationEntry] = field(default_factory=list)
    transforms: list[TransformEntry] = field(default_factory=list)
    model_info: list[ModelInfo] = field(default_factory=list)
    status: str = "accepted"  # accepted | needs_confirmation | refused
    refusal_reasons: list[str] = field(default_factory=list)
    needs_confirmation_proposals: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        def convert(obj):
            if hasattr(obj, '__dataclass_fields__'):
                return {k: convert(v) for k, v in asdict(obj).items()}
            elif isinstance(obj, (list, tuple)):
                return [convert(v) for v in obj]
            elif isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            elif isinstance(obj, (np.integer, np.floating)):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            elif pd.isna(obj):
                return None
            return obj

        return convert(asdict(self))

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, allow_nan=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> AdaptationReport:
        data = json.loads(text)
        # Reconstruct dataclasses - simplified for now
        return cls(**data)


def _sha256_file(path: Path) -> str:
    if path.exists():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return ""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_report(
    *,
    original_path: Path,
    adapted_df: pd.DataFrame,
    adapted_path: Path | None,
    column_mappings: list[ColumnMappingEntry],
    inferred_values: list[InferredValueEntry],
    derived_values: list[DerivedValueEntry],
    flagged_rows: list[FlaggedRowEntry],
    user_confirmations: list[UserConfirmationEntry],
    transforms: list[TransformEntry],
    model_info: list[ModelInfo],
    status: str,
    refusal_reasons: list[str],
    needs_confirmation_proposals: list[dict[str, Any]],
    config: AdapterConfig,
    profile_dict: dict[str, Any],
) -> AdaptationReport:
    """Build the complete adaptation report."""
    original_sha256 = _sha256_file(original_path)
    adapted_sha256 = _sha256_file(adapted_path) if adapted_path else ""

    return AdaptationReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        original_file={
            "path": str(original_path),
            "sha256": original_sha256,
            "profile": profile_dict,
        },
        adapted_file={
            "path": str(adapted_path) if adapted_path else "",
            "sha256": adapted_sha256,
            "n_rows": len(adapted_df),
            "n_cols": len(adapted_df.columns),
        },
        config={
            "thresholds": {
                "accept_at": config.thresholds.accept_at,
                "refuse_below": config.thresholds.refuse_below,
            },
            "fx_rate_kes_usd": config.fx_rate_kes_usd,
            "data_dir": str(config.data_dir),
            "max_outside_nairobi_pct": config.max_outside_nairobi_pct,
            "random_seed": config.random_seed,
        },
        column_mappings=column_mappings,
        inferred_values=inferred_values,
        derived_values=derived_values,
        flagged_rows=flagged_rows,
        user_confirmations=user_confirmations,
        transforms=transforms,
        model_info=model_info,
        status=status,
        refusal_reasons=refusal_reasons,
        needs_confirmation_proposals=needs_confirmation_proposals,
    )