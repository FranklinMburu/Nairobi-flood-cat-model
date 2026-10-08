"""Deterministic value normalization: units, coordinates, booleans."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

import numpy as np
import pandas as pd

from .config import AdapterConfig
from .profile import HAZARD_TIERS

# Nairobi bounds
NAIROBI_LAT_RANGE = (-1.45, -1.10)
NAIROBI_LON_RANGE = (36.60, 37.00)

# Exact conversion factors (using Decimal for precision)
SQFT_TO_SQM = Decimal("0.09290304")  # exact: 1 sq ft = 0.09290304 m²


@dataclass
class TransformRecord:
    """Record of a single transformation applied."""
    field: str
    operation: str
    before_sample: list[Any]
    after_sample: list[Any]
    parameters: dict[str, Any]
    rows_affected: int


@dataclass
class NormalizeResult:
    """Result of normalization step."""
    df: pd.DataFrame
    transforms: list[TransformRecord]
    warnings: list[str]


def _to_numeric(series: pd.Series) -> pd.Series:
    """Convert to numeric, preserving NaN for non-numeric."""
    return pd.to_numeric(series.str.strip(), errors="coerce")


def _detect_and_convert_area(series: pd.Series, field: str) -> tuple[pd.Series, TransformRecord | None]:
    """Detect sqft and convert to m². Returns (converted_series, record_or_None)."""
    numeric = _to_numeric(series)
    if numeric.isna().all():
        return numeric, None

    # Heuristic: if max > 1000, likely sqft (since max m² ~ 1100 in data)
    finite = numeric[np.isfinite(numeric)]
    if len(finite) == 0:
        return numeric, None

    max_val = float(finite.max())
    if max_val > 1000:
        # Convert sqft -> m² using exact decimal
        converted = numeric.apply(lambda x: float(Decimal(str(x)) * SQFT_TO_SQM) if pd.notna(x) else np.nan)
        return converted, TransformRecord(
            field=field,
            operation="sqft_to_sqm",
            before_sample=finite.head(3).tolist(),
            after_sample=converted.dropna().head(3).tolist(),
            parameters={"factor": float(SQFT_TO_SQM)},
            rows_affected=int(converted.notna().sum()),
        )
    return numeric, None


def _detect_and_convert_cost(series: pd.Series, field: str, fx_rate: float) -> tuple[pd.Series, TransformRecord | None]:
    """Detect USD or thousands and convert to KES. Returns (converted_series, record_or_None)."""
    numeric = _to_numeric(series)
    if numeric.isna().all():
        return numeric, None

    finite = numeric[np.isfinite(numeric)]
    if len(finite) == 0:
        return numeric, None

    # Heuristics:
    # - If values look like thousands (e.g., 5.5 meaning 5500): multiply by 1000
    # - If max < 1000 and median < 500: likely USD (KES costs are 5K-85K)
    max_val = float(finite.max())
    median_val = float(finite.median())

    # Check if values are in thousands (e.g., 5.5, 13.6 meaning 5500, 13600)
    if max_val < 200 and median_val < 100:
        # Likely thousands -> raw
        converted = numeric * 1000
        return converted, TransformRecord(
            field=field,
            operation="thousands_to_raw",
            before_sample=finite.head(3).tolist(),
            after_sample=converted.dropna().head(3).tolist(),
            parameters={"factor": 1000},
            rows_affected=int(converted.notna().sum()),
        )

    # Check if values are in USD range (~10-500 USD/m² = ~1300-65000 KES/m²)
    if max_val < 1000 and median_val < 500:
        # Likely USD -> KES
        converted = numeric * fx_rate
        return converted, TransformRecord(
            field=field,
            operation="usd_to_kes",
            before_sample=finite.head(3).tolist(),
            after_sample=converted.dropna().head(3).tolist(),
            parameters={"fx_rate": fx_rate},
            rows_affected=int(converted.notna().sum()),
        )

    return numeric, None


def _detect_and_convert_tiv(series: pd.Series, field: str) -> tuple[pd.Series, TransformRecord | None]:
    """Detect thousands/millions in TIV and convert to raw KES."""
    numeric = _to_numeric(series)
    if numeric.isna().all():
        return numeric, None

    finite = numeric[np.isfinite(numeric)]
    if len(finite) == 0:
        return numeric, None

    max_val = float(finite.max())
    median_val = float(finite.median())

    # Supplied TIV range: ~500K to ~580M
    # If max < 1e3 and values look like millions (e.g., 5, 10, 20): millions -> raw
    # If max < 1e6: likely thousands -> raw
    # Check millions first (small numbers that represent millions)
    if max_val < 1e3 and median_val < 1e3:
        # Likely millions -> raw
        converted = numeric * 1_000_000
        return converted, TransformRecord(
            field=field,
            operation="millions_to_raw",
            before_sample=finite.head(3).tolist(),
            after_sample=converted.dropna().head(3).tolist(),
            parameters={"factor": 1_000_000},
            rows_affected=int(converted.notna().sum()),
        )
    elif max_val < 1e6:
        # Likely thousands -> raw
        converted = numeric * 1000
        return converted, TransformRecord(
            field=field,
            operation="thousands_to_raw",
            before_sample=finite.head(3).tolist(),
            after_sample=converted.dropna().head(3).tolist(),
            parameters={"factor": 1000},
            rows_affected=int(converted.notna().sum()),
        )

    return numeric, None


def _normalize_boolean(series: pd.Series, field: str) -> tuple[pd.Series, TransformRecord | None]:
    """Normalize various boolean representations to True/False."""
    str_vals = series.astype(str).str.strip().str.lower()
    true_vals = {"true", "1", "yes", "y", "t"}
    false_vals = {"false", "0", "no", "n", "f"}

    def map_bool(v: str) -> bool | None:
        if v in true_vals:
            return True
        if v in false_vals:
            return False
        return None

    converted = str_vals.apply(map_bool)
    if converted.isna().all():
        return converted, None

    before = series.dropna().head(3).tolist()
    after = converted.dropna().head(3).tolist()
    return converted, TransformRecord(
        field=field,
        operation="normalize_boolean",
        before_sample=before,
        after_sample=after,
        parameters={"true_values": list(true_vals), "false_values": list(false_vals)},
        rows_affected=int(converted.notna().sum()),
    )


def _maybe_swap_lon_lat(lat_series: pd.Series, lon_series: pd.Series) -> tuple[pd.Series, pd.Series, TransformRecord | None]:
    """Swap lat/lon if they appear reversed. Only when unambiguous."""
    # Handle both string and numeric series
    if lat_series.dtype == object:
        lat_num = pd.to_numeric(lat_series.astype(str).str.strip(), errors="coerce")
    else:
        lat_num = pd.to_numeric(lat_series, errors="coerce")
    
    if lon_series.dtype == object:
        lon_num = pd.to_numeric(lon_series.astype(str).str.strip(), errors="coerce")
    else:
        lon_num = pd.to_numeric(lon_series, errors="coerce")

    lat_finite = lat_num[np.isfinite(lat_num)]
    lon_finite = lon_num[np.isfinite(lon_num)]

    if len(lat_finite) == 0 or len(lon_finite) == 0:
        return lat_num, lon_num, None

    # Check how many in each range
    lat_in_lat_range = ((lat_finite >= NAIROBI_LAT_RANGE[0]) & (lat_finite <= NAIROBI_LAT_RANGE[1])).sum()
    lat_in_lon_range = ((lat_finite >= NAIROBI_LON_RANGE[0]) & (lat_finite <= NAIROBI_LON_RANGE[1])).sum()
    lon_in_lat_range = ((lon_finite >= NAIROBI_LAT_RANGE[0]) & (lon_finite <= NAIROBI_LAT_RANGE[1])).sum()
    lon_in_lon_range = ((lon_finite >= NAIROBI_LON_RANGE[0]) & (lon_finite <= NAIROBI_LON_RANGE[1])).sum()

    # Unambiguous swap: lat column has lon-range values AND lon column has lat-range values
    lat_is_lon = lat_in_lon_range > lat_in_lat_range * 2
    lon_is_lat = lon_in_lat_range > lon_in_lon_range * 2

    if lat_is_lon and lon_is_lat:
        # Swap
        before_lat = lat_finite.head(3).tolist()
        before_lon = lon_finite.head(3).tolist()
        return lon_num, lat_num, TransformRecord(
            field="lat/lon",
            operation="swap_lon_lat",
            before_sample={"lat": before_lat, "lon": before_lon},
            after_sample={"lat": before_lon, "lon": before_lat},
            parameters={},
            rows_affected=len(lat_finite),
        )

    return lat_num, lon_num, None


def normalize_values(
    df: pd.DataFrame,
    column_mapping: dict[str, str],  # source_col -> target_field
    config: AdapterConfig,
    synthetic_declared: bool | None = None,
) -> NormalizeResult:
    """Apply deterministic normalizations based on target field mapping."""
    transforms = []
    warnings = []
    out = df.copy()

    # Build reverse mapping: target_field -> source_col
    target_to_source = {v: k for k, v in column_mapping.items()}

    # 1. loc_id: ensure string, strip
    if "loc_id" in target_to_source:
        src = target_to_source["loc_id"]
        before = out[src].dropna().head(3).tolist()
        out[src] = out[src].astype(str).str.strip()
        after = out[src].dropna().head(3).tolist()
        if before != after:
            transforms.append(TransformRecord(
                field="loc_id",
                operation="strip_whitespace",
                before_sample=before,
                after_sample=after,
                parameters={},
                rows_affected=len(out),
            ))

    # 2. floor_area_m2: detect sqft
    if "floor_area_m2" in target_to_source:
        src = target_to_source["floor_area_m2"]
        converted, record = _detect_and_convert_area(out[src], "floor_area_m2")
        if record:
            out[src] = converted
            transforms.append(record)

    # 3. cost_per_m2_kes: detect USD or thousands
    if "cost_per_m2_kes" in target_to_source:
        src = target_to_source["cost_per_m2_kes"]
        converted, record = _detect_and_convert_cost(out[src], "cost_per_m2_kes", config.fx_rate_kes_usd)
        if record:
            out[src] = converted
            transforms.append(record)
            warnings.append(
                f"cost_per_m2_kes: applied {record.operation} (fx_rate={config.fx_rate_kes_usd}). "
                "Confirm this is correct."
            )

    # 4. tiv_kes: detect thousands/millions
    if "tiv_kes" in target_to_source:
        src = target_to_source["tiv_kes"]
        converted, record = _detect_and_convert_tiv(out[src], "tiv_kes")
        if record:
            out[src] = converted
            transforms.append(record)
            warnings.append(
                f"tiv_kes: applied {record.operation}. "
                "Confirm this is correct (D-001: supplied TIV must be used as-is)."
            )

    # 5. synthetic: normalize boolean
    if "synthetic" in target_to_source:
        src = target_to_source["synthetic"]
        converted, record = _normalize_boolean(out[src], "synthetic")
        if record:
            out[src] = converted
            transforms.append(record)

    # 6. lat/lon: maybe swap
    if "lat" in target_to_source and "lon" in target_to_source:
        lat_src = target_to_source["lat"]
        lon_src = target_to_source["lon"]
        lat_conv, lon_conv, record = _maybe_swap_lon_lat(out[lat_src], out[lon_src])
        if record:
            out[lat_src] = lat_conv
            out[lon_src] = lon_conv
            transforms.append(record)
            warnings.append("lat/lon: swapped columns based on Nairobi coordinate ranges. Confirm correct.")

    # 7. housing_class: strip whitespace, lower for matching
    if "housing_class" in target_to_source:
        src = target_to_source["housing_class"]
        before = out[src].dropna().head(3).tolist()
        out[src] = out[src].astype(str).str.strip().str.lower()
        after = out[src].dropna().head(3).tolist()
        if before != after:
            transforms.append(TransformRecord(
                field="housing_class",
                operation="strip_lower",
                before_sample=before,
                after_sample=after,
                parameters={},
                rows_affected=len(out),
            ))

    # 8. hazard scores: ensure numeric in [0,1]
    for hazard_col in ("hazard_score_common", "hazard_score_occasional", "hazard_score_moderate",
                       "hazard_score_severe", "hazard_score_extreme"):
        if hazard_col in target_to_source:
            src = target_to_source[hazard_col]
            before = out[src].dropna().head(3).tolist()
            out[src] = _to_numeric(out[src])
            after = out[src].dropna().head(3).tolist()
            if before != after:
                transforms.append(TransformRecord(
                    field=hazard_col,
                    operation="to_numeric",
                    before_sample=before,
                    after_sample=after,
                    parameters={},
                    rows_affected=int(out[src].notna().sum()),
                ))

    # 9. source: strip
    if "source" in target_to_source:
        src = target_to_source["source"]
        before = out[src].dropna().head(3).tolist()
        out[src] = out[src].astype(str).str.strip()
        after = out[src].dropna().head(3).tolist()
        if before != after:
            transforms.append(TransformRecord(
                field="source",
                operation="strip_whitespace",
                before_sample=before,
                after_sample=after,
                parameters={},
                rows_affected=len(out),
            ))

    return NormalizeResult(df=out, transforms=transforms, warnings=warnings)


def verify_v7_order(df: pd.DataFrame) -> list[str]:
    """Verify hazard scores satisfy V7 ordering (common >= occasional >= moderate >= severe >= extreme).
    Returns list of violations (empty if valid)."""
    violations = []
    hazard_cols = [f"hazard_score_{t}" for t in HAZARD_TIERS]
    missing = [c for c in hazard_cols if c not in df.columns]
    if missing:
        return [f"Missing hazard columns: {missing}"]

    for idx, row in df.iterrows():
        vals = [row[c] for c in hazard_cols]
        if any(pd.isna(v) for v in vals):
            continue
        for i in range(len(vals) - 1):
            if vals[i] < vals[i + 1] - 1e-9:
                violations.append(f"Row {idx}: {hazard_cols[i]}={vals[i]} < {hazard_cols[i+1]}={vals[i+1]}")
                break
        if len(violations) > 10:
            violations.append(f"... and {len(df) - idx - 1} more rows unchecked")
            break
    return violations