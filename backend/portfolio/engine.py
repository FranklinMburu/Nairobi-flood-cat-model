"""Load the audited portfolio once and expose it as plain JSON-ready data.

This module does not calculate loss. It calls loss_engine and copies the
results. Return-period years are D-004 display labels only.
"""

from __future__ import annotations

import csv
import math
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loss_engine.aggregation import aggregate  # noqa: E402
from loss_engine.config import default_config  # noqa: E402
from loss_engine.validation import load_and_validate_exposure  # noqa: E402

EXPOSURE = ROOT / "data" / "exposure_nairobi_with_hazard.csv"
HOTSPOTS = ROOT / "data" / "nairobi_hotspots_geocoded.csv"

# D-004 display labels. Not derived from the rasters and not used by the engine.
ASSUMED_RETURN_PERIOD_YEARS = {
    "extreme": 10,
    "severe": 25,
    "moderate": 50,
    "occasional": 100,
    "common": 250,
}
TIER_LABELS = {
    "extreme": "Extreme",
    "severe": "Severe",
    "moderate": "Moderate",
    "occasional": "Occasional",
    "common": "Common",
}
TIER_NOTES = {
    "extreme": "Narrowest footprint. Assumed 10-year label (D-004), not the largest loss.",
    "severe": "Assumed 25-year label (D-004).",
    "moderate": "Assumed 50-year label (D-004).",
    "occasional": "Assumed 100-year label (D-004).",
    "common": "Widest footprint and largest modelled loss. Assumed 250-year label (D-004).",
}
SHORT_LABELS = {
    "reference": "Reference",
    "low": "Low",
    "high": "High",
    "reference_rcc80": "RCC 80%",
}
REFERENCE_LOSSES = {
    "extreme": 468_443_071.72,
    "severe": 826_175_003.83,
    "moderate": 1_972_325_986.45,
    "occasional": 3_279_344_346.85,
    "common": 5_103_936_640.53,
}

_CACHE = None


def json_value(value):
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating, Decimal)):
        number = float(value)
        if not math.isfinite(number):
            return None
        return number
    return value


def _build() -> dict:
    exposure = load_and_validate_exposure(EXPOSURE)
    config = default_config()
    portfolio = aggregate(config, exposure)
    if not portfolio.output_checks.passed:
        raise RuntimeError("Output checks failed.")

    metrics: dict = {}
    for row in portfolio.building_results.itertuples(index=False):
        metrics.setdefault(row.loc_id, {}).setdefault(row.scenario_id, {})[row.tier] = {
            "hazard_score": json_value(row.hazard_score),
            "damage_ratio": json_value(row.damage_ratio),
            "loss_kes": json_value(row.loss_kes),
            "affected": json_value(row.affected),
        }

    buildings = []
    for row in exposure.data.itertuples(index=False):
        buildings.append(
            {
                "loc_id": row.loc_id,
                "lat": json_value(row.lat),
                "lon": json_value(row.lon),
                "housing_class": row.housing_class,
                "tiv_kes": json_value(row.tiv_kes),
                "synthetic": json_value(row.synthetic),
                "metrics": metrics[row.loc_id],
            }
        )

    tier_summary: dict = {}
    for row in portfolio.tier_summary.itertuples(index=False):
        tier_summary.setdefault(row.scenario_id, {})[row.tier] = {
            "buildings": json_value(row.buildings),
            "tiv_kes": json_value(row.tiv_kes),
            "affected_buildings": json_value(row.affected_buildings),
            "affected_tiv_kes": json_value(row.affected_tiv_kes),
            "portfolio_loss_kes": json_value(row.portfolio_loss_kes),
            "loss_pct_portfolio": json_value(row.loss_pct_portfolio),
            "loss_pct_affected": json_value(row.loss_pct_affected),
            "avg_loss_per_affected_kes": json_value(row.avg_loss_per_affected_kes),
        }

    class_summary: dict = {}
    for row in portfolio.class_summary.itertuples(index=False):
        class_summary.setdefault(row.scenario_id, {}).setdefault(row.tier, []).append(
            {
                "housing_class": row.housing_class,
                "buildings": json_value(row.buildings),
                "tiv_kes": json_value(row.tiv_kes),
                "affected_buildings": json_value(row.affected_buildings),
                "loss_kes": json_value(row.loss_kes),
                "loss_ratio": json_value(row.loss_ratio),
                "share_of_tier_loss": json_value(row.share_of_tier_loss),
            }
        )

    hotspots = []
    with HOTSPOTS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            hotspots.append({"name": row["name"], "lat": float(row["lat"]), "lon": float(row["lon"])})

    reference = tier_summary["reference"]
    for tier, expected in REFERENCE_LOSSES.items():
        actual = reference[tier]["portfolio_loss_kes"]
        if abs(actual - expected) > 1:
            raise RuntimeError(f"{tier} portfolio loss {actual} is not the frozen value {expected}")
    if reference["common"]["affected_buildings"] != 259:
        raise RuntimeError("common affected buildings is not 259")
    if abs(reference["common"]["tiv_kes"] - 63_635_075_000) > 1:
        raise RuntimeError("portfolio TIV is not 63,635,075,000")
    if len(buildings) != 600 or len(hotspots) != 24:
        raise RuntimeError("unexpected building or hotspot count")

    return {
        "source": "loss_engine.aggregate",
        "parameter_set_id": config.parameter_set_id,
        "exposure_file": exposure.filename,
        "honesty": [
            "Synthetic portfolio",
            "Susceptibility proxy, not flood depth",
            "Damage parameter H is an assumption",
            "Affected means proxy-flagged",
            "Return periods are D-004 assumptions",
        ],
        "tiers": [
            {
                "id": tier,
                "label": TIER_LABELS[tier],
                "assumed_return_period_years": ASSUMED_RETURN_PERIOD_YEARS[tier],
                "return_period_basis": "D-004 assumption",
                "note": TIER_NOTES[tier],
            }
            for tier in config.tiers
        ],
        "scenarios": [
            {
                "id": scenario.scenario_id,
                "label": scenario.label,
                "short_label": SHORT_LABELS[scenario.scenario_id],
                "h": json_value(scenario.h),
            }
            for scenario in config.scenarios
        ],
        "buildings": buildings,
        "tier_summary": tier_summary,
        "class_summary": class_summary,
        "hotspots": hotspots,
    }


def get_payload() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = _build()
    return _CACHE
