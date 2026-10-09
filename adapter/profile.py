"""File profiling: read CSV/XLSX/GeoJSON/PDF and extract metadata."""

from __future__ import annotations

import hashlib
import io
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import AdapterConfig, DEFAULT_CONFIG


@dataclass
class FileProfile:
    """Metadata profile of an input file."""
    sha256: str
    format: str  # "csv", "xlsx", "geojson", "pdf"
    n_rows: int
    n_cols: int
    headers: list[str]
    dtypes: dict[str, str]
    sample_values: dict[str, list]
    numeric_ranges: dict[str, tuple[float, float]]
    coord_candidates: list[str]
    class_candidates: list[str]
    tiv_candidates: list[str]
    synthetic_candidates: list[str]
    source_candidates: list[str]
    hazard_candidates: list[str]
    geojson_geometry_types: list[str] | None = None
    geojson_crs: str | None = None
    geojson_polygon_count: int = 0
    geojson_point_count: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "format": self.format,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "headers": self.headers,
            "dtypes": self.dtypes,
            "sample_values": {k: v[:5] for k, v in self.sample_values.items()},
            "numeric_ranges": self.numeric_ranges,
            "coord_candidates": self.coord_candidates,
            "class_candidates": self.class_candidates,
            "tiv_candidates": self.tiv_candidates,
            "synthetic_candidates": self.synthetic_candidates,
            "source_candidates": self.source_candidates,
            "hazard_candidates": self.hazard_candidates,
            "geojson_geometry_types": self.geojson_geometry_types,
            "geojson_crs": self.geojson_crs,
            "geojson_polygon_count": self.geojson_polygon_count,
            "geojson_point_count": self.geojson_point_count,
            "warnings": self.warnings,
        }


# Nairobi bounds (from validation.py)
NAIROBI_LAT_RANGE = (-1.45, -1.10)
NAIROBI_LON_RANGE = (36.60, 37.00)

# Target columns (from validation.py REQUIRED_COLUMNS)
TARGET_FIELDS = [
    "loc_id", "lat", "lon", "housing_class", "floor_area_m2",
    "cost_per_m2_kes", "tiv_kes", "synthetic", "source",
    "hazard_score_common", "hazard_score_occasional",
    "hazard_score_moderate", "hazard_score_severe", "hazard_score_extreme",
]

HAZARD_TIERS = ("common", "occasional", "moderate", "severe", "extreme")
HAZARD_COLUMNS = tuple(f"hazard_score_{t}" for t in HAZARD_TIERS)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _in_nairobi_range(series: pd.Series) -> float:
    """Fraction of non-null values within Nairobi lat/lon bounds."""
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if len(vals) == 0:
        return 0.0
    in_lat = ((vals >= NAIROBI_LAT_RANGE[0]) & (vals <= NAIROBI_LAT_RANGE[1])).sum()
    in_lon = ((vals >= NAIROBI_LON_RANGE[0]) & (vals <= NAIROBI_LON_RANGE[1])).sum()
    # A coordinate column should be either lat OR lon, not both
    return max(in_lat, in_lon) / len(vals)


def _looks_like_tiv(series: pd.Series) -> float:
    """Heuristic: values in TIV range (millions to billions)."""
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if len(vals) == 0:
        return 0.0
    # TIV in supplied data: ~500K to ~580M
    in_range = ((vals >= 1e5) & (vals <= 1e9)).sum()
    return in_range / len(vals)


def _looks_like_cost_per_m2(series: pd.Series) -> float:
    """Heuristic: values in cost range (thousands to hundreds of thousands)."""
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if len(vals) == 0:
        return 0.0
    # Cost per m2 in supplied data: ~5K to ~85K KES
    in_range = ((vals >= 1e3) & (vals <= 2e5)).sum()
    return in_range / len(vals)


def _looks_like_area(series: pd.Series) -> float:
    """Heuristic: values in floor area range (tens to thousands)."""
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if len(vals) == 0:
        return 0.0
    # Floor area in supplied data: ~8 to ~1100 m2
    in_range = ((vals >= 1) & (vals <= 5000)).sum()
    return in_range / len(vals)


def _looks_like_hazard_score(series: pd.Series) -> float:
    """Heuristic: values in [0, 1]."""
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if len(vals) == 0:
        return 0.0
    in_range = ((vals >= 0) & (vals <= 1)).sum()
    return in_range / len(vals)


def read_file(path: str | Path) -> tuple[pd.DataFrame, str]:
    """Read a file and return (DataFrame, format)."""
    path = Path(path)
    raw = path.read_bytes()
    suffix = path.suffix.lower()

    if suffix == ".csv":
        df = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
        return df, "csv"
    elif suffix in (".xlsx", ".xls"):
        df = pd.read_excel(io.BytesIO(raw), dtype=str, keep_default_na=False)
        return df, "xlsx"
    elif suffix in (".geojson", ".json"):
        # Read with stdlib first to check structure
        with io.BytesIO(raw) as f:
            gj = json.load(f)
        if gj.get("type") != "FeatureCollection":
            raise ValueError("GeoJSON must be a FeatureCollection")
        features = gj.get("features", [])
        if not features:
            raise ValueError("GeoJSON has no features")
        # Check geometry types
        geom_types = [f.get("geometry", {}).get("type") for f in features]
        # For now, only Points are handled without geo extra
        if any(g not in ("Point", "Polygon", "MultiPolygon") for g in geom_types):
            raise ValueError(f"Unsupported geometry types: {set(geom_types)}. Only Point, Polygon, MultiPolygon allowed.")
        # CRS
        crs = gj.get("crs", {}).get("properties", {}).get("name")
        # Convert to DataFrame
        rows = []
        for feat in features:
            props = feat.get("properties", {})
            geom = feat.get("geometry", {})
            if geom.get("type") == "Point":
                coords = geom.get("coordinates", [None, None])
                props["_geometry_lon"] = coords[0]
                props["_geometry_lat"] = coords[1]
            elif geom.get("type") in ("Polygon", "MultiPolygon"):
                props["_geometry_polygon"] = geom
            rows.append(props)
        df = pd.DataFrame(rows)
        return df, "geojson"
    elif suffix == ".pdf":
        from .pdf_tables import extract_table  # lazy: needs the adapter extra

        return extract_table(path), "pdf"
    else:
        raise ValueError(f"Unsupported file format: {suffix}")


def profile_file(
    path: str | Path,
    config: AdapterConfig | None = None,
    *,
    preloaded: tuple[pd.DataFrame, str] | None = None,
) -> FileProfile:
    """Profile a file and return metadata.

    `preloaded` is an already-read (DataFrame, format) pair from read_file, so
    the file is parsed once (a PDF parse is slow).
    """
    config = config or DEFAULT_CONFIG
    path = Path(path)
    raw = path.read_bytes()
    sha256 = _sha256_bytes(raw)
    df, fmt = preloaded if preloaded is not None else read_file(path)

    headers = list(df.columns)
    dtypes = {c: str(df[c].dtype) for c in headers}
    sample_values = {c: df[c].dropna().head(5).tolist() for c in headers}

    numeric_ranges = {}
    coord_candidates = []
    class_candidates = []
    tiv_candidates = []
    synthetic_candidates = []
    source_candidates = []
    hazard_candidates = []

    for c in headers:
        series = df[c]
        # Try numeric
        numeric = pd.to_numeric(series, errors="coerce")
        if numeric.notna().any():
            finite = numeric[np.isfinite(numeric)]
            if len(finite) > 0:
                numeric_ranges[c] = (float(finite.min()), float(finite.max()))

        # Heuristics
        lc = c.lower()
        in_nairobi = _in_nairobi_range(series)
        if in_nairobi > 0.5:
            coord_candidates.append(c)
        if any(k in lc for k in ["class", "type", "hous", "building", "constr", "material"]):
            class_candidates.append(c)
        if _looks_like_tiv(series) > 0.5:
            tiv_candidates.append(c)
        if _looks_like_cost_per_m2(series) > 0.5:
            class_candidates.append(c)  # cost often indicates class
        if _looks_like_area(series) > 0.5:
            class_candidates.append(c)  # area often indicates class
        if any(k in lc for k in ["synth", "gen", "fake", "test", "sample"]):
            synthetic_candidates.append(c)
        if any(k in lc for k in ["source", "origin", "provider", "dataset"]):
            source_candidates.append(c)
        if _looks_like_hazard_score(series) > 0.5 or any(t in lc for t in HAZARD_TIERS):
            hazard_candidates.append(c)

    # GeoJSON specifics
    geojson_geometry_types = None
    geojson_crs = None
    geojson_polygon_count = 0
    geojson_point_count = 0
    if fmt == "geojson":
        with io.BytesIO(raw) as f:
            gj = json.load(f)
        geojson_crs = gj.get("crs", {}).get("properties", {}).get("name")
        features = gj.get("features", [])
        geom_types = [f.get("geometry", {}).get("type") for f in features]
        geojson_geometry_types = sorted(set(geom_types))
        geojson_polygon_count = sum(1 for g in geom_types if g in ("Polygon", "MultiPolygon"))
        geojson_point_count = sum(1 for g in geom_types if g == "Point")
        if geojson_polygon_count > 0:
            # Need to warn that polygons require geo extra
            pass

    warnings = list(df.attrs.get("notes", [])) if fmt == "pdf" else []
    if fmt == "geojson" and geojson_polygon_count > 0:
        warnings.append(
            f"File contains {geojson_polygon_count} polygon(s); centroid extraction requires optional 'geo' extra"
        )

    return FileProfile(
        sha256=sha256,
        format=fmt,
        n_rows=len(df),
        n_cols=len(df.columns),
        headers=headers,
        dtypes=dtypes,
        sample_values=sample_values,
        numeric_ranges=numeric_ranges,
        coord_candidates=coord_candidates,
        class_candidates=class_candidates,
        tiv_candidates=tiv_candidates,
        synthetic_candidates=synthetic_candidates,
        source_candidates=source_candidates,
        hazard_candidates=hazard_candidates,
        geojson_geometry_types=geojson_geometry_types,
        geojson_crs=geojson_crs,
        geojson_polygon_count=geojson_polygon_count,
        geojson_point_count=geojson_point_count,
        warnings=warnings,
    )