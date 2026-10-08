"""Nairobi Flood Model Data Adapter.

Transforms arbitrary exposure files (CSV, XLSX, GeoJSON) into the canonical
schema required by the deterministic loss engine, with full audit trail.
"""

from __future__ import annotations

from .config import AdapterConfig, ConfidenceThresholds, DEFAULT_CONFIG
from .pipeline import adapt_file, AdaptationResult
from .profile import profile_file, FileProfile
from .normalize import normalize_values, verify_v7_order
from .report import AdaptationReport, build_report

__version__ = "1.0.0"

__all__ = [
    "AdapterConfig",
    "ConfidenceThresholds",
    "DEFAULT_CONFIG",
    "adapt_file",
    "AdaptationResult",
    "profile_file",
    "FileProfile",
    "normalize_values",
    "verify_v7_order",
    "AdaptationReport",
    "build_report",
]