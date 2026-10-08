"""Adapter configuration and thresholds."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ConfidenceThresholds:
    """Decision thresholds for adapter validation.

    - >= accept_at: auto-accept
    - [refuse_below, accept_at): needs_confirmation
    - < refuse_below: refuse
    """
    accept_at: float = 0.85
    refuse_below: float = 0.50

    def __post_init__(self):
        if not (0.0 <= self.refuse_below < self.accept_at <= 1.0):
            raise ValueError("Thresholds must satisfy 0 <= refuse_below < accept_at <= 1")


@dataclass(frozen=True)
class AdapterConfig:
    """Complete adapter configuration."""
    thresholds: ConfidenceThresholds = field(default_factory=ConfidenceThresholds)
    fx_rate_kes_usd: float = 130.0
    data_dir: Path = field(default_factory=lambda: Path("data"))
    max_outside_nairobi_pct: float = 0.10
    random_seed: int = 42
    column_mapper_model: Path = field(default_factory=lambda: Path("models/column_mapper.joblib"))
    class_inference_model: Path = field(default_factory=lambda: Path("models/class_inference.joblib"))

    @classmethod
    def load(
        cls,
        config_path: str | Path | None = None,
        *,
        fx_rate: float | None = None,
        data_dir: str | Path | None = None,
        accept_at: float | None = None,
        refuse_below: float | None = None,
    ) -> AdapterConfig:
        """Load config from file with optional overrides."""
        config_file = Path(config_path) if config_path else (
            Path(__file__).parent / "config" / "adapter.json"
        )
        fx_rate_kes_usd = 130.0
        if config_file.exists():
            with config_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            fx_rate_kes_usd = float(data.get("fx_rate_kes_usd", 130.0))
        # Env var override
        if env_fx := os.environ.get("ADAPTER_FX_RATE"):
            fx_rate_kes_usd = float(env_fx)
        # Explicit override
        if fx_rate is not None:
            fx_rate_kes_usd = float(fx_rate)

        data_dir_path = Path(data_dir) if data_dir else Path("data")

        thresholds = ConfidenceThresholds(
            accept_at=accept_at if accept_at is not None else 0.85,
            refuse_below=refuse_below if refuse_below is not None else 0.50,
        )

        return cls(
            thresholds=thresholds,
            fx_rate_kes_usd=fx_rate_kes_usd,
            data_dir=data_dir_path,
        )

    def resolve_model_path(self, model_path: Path) -> Path:
        """Resolve model path relative to adapter package."""
        if model_path.is_absolute():
            return model_path
        return Path(__file__).parent / model_path


DEFAULT_CONFIG = AdapterConfig()