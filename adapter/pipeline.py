"""Main adapter pipeline: orchestrates profile -> map -> normalize -> derive -> validate -> report."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .config import AdapterConfig, DEFAULT_CONFIG
from .profile import profile_file, read_file, FileProfile
from .normalize import normalize_values, verify_v7_order, NormalizeResult
from .report import (
    AdaptationReport,
    ColumnMappingEntry,
    InferredValueEntry,
    DerivedValueEntry,
    FlaggedRowEntry,
    UserConfirmationEntry,
    TransformEntry,
    ModelInfo,
    build_report,
)


@dataclass
class AdaptationResult:
    """Result of the adaptation process."""
    status: str  # "accepted" | "needs_confirmation" | "refused"
    adapted_path: Path | None
    report_path: Path | None
    report: AdaptationReport
    proposals: list[dict[str, Any]]  # For needs_confirmation: structured proposals


def _validate_mapping_confidence(mappings: list[ColumnMappingEntry], thresholds) -> tuple[str, list[str], list[dict]]:
    """Validate column mapping confidences against thresholds."""
    refusal_reasons = []
    needs_confirmation_proposals = []

    for m in mappings:
        if m.confidence < thresholds.refuse_below:
            refusal_reasons.append(
                f"Column mapping '{m.source_column}' -> '{m.target_field}' "
                f"confidence {m.confidence:.3f} below refuse threshold {thresholds.refuse_below}"
            )
        elif m.confidence < thresholds.accept_at:
            needs_confirmation_proposals.append({
                "type": "column_mapping",
                "source_column": m.source_column,
                "target_field": m.target_field,
                "confidence": m.confidence,
                "method": m.method,
            })

    if refusal_reasons:
        return "refused", refusal_reasons, needs_confirmation_proposals
    if needs_confirmation_proposals:
        return "needs_confirmation", [], needs_confirmation_proposals
    return "accepted", [], []


def _validate_required_fields(mappings: list[ColumnMappingEntry], required_fields: list[str], synthetic_declared: bool | None = None) -> list[str]:
    """Check all required fields are mapped."""
    mapped_targets = {m.target_field for m in mappings}
    # If synthetic is declared via parameter, it doesn't need to be in mappings
    if synthetic_declared is not None:
        required_fields = [f for f in required_fields if f != "synthetic"]
    missing = [f for f in required_fields if f not in mapped_targets]
    return [f"Required field not mapped: {f}" for f in missing]


def _validate_tiv_present(mappings: list[ColumnMappingEntry]) -> list[str]:
    """TIV must be present (never derived from area*cost)."""
    if "tiv_kes" not in {m.target_field for m in mappings}:
        return ["tiv_kes is required and cannot be derived from area × cost (D-001)"]
    return []


def _validate_synthetic_declared(mappings: list[ColumnMappingEntry], synthetic_declared: bool | None) -> list[str]:
    """Synthetic must be explicitly declared."""
    if synthetic_declared is None:
        if "synthetic" not in {m.target_field for m in mappings}:
            return ["synthetic column not found and not declared via --synthetic flag"]
    return []


def _validate_outside_nairobi(flagged_rows: list[FlaggedRowEntry], max_pct: float, n_rows: int) -> list[str]:
    """Check fraction of rows outside Nairobi extent."""
    outside_count = sum(1 for f in flagged_rows if f.reason == "outside_nairobi_extent")
    if n_rows > 0 and (outside_count / n_rows) > max_pct:
        return [f"{outside_count}/{n_rows} rows ({outside_count/n_rows:.1%}) outside Nairobi extent, exceeds {max_pct:.0%} threshold"]
    return []


def _validate_duplicates(df: pd.DataFrame) -> list[str]:
    """Check for duplicate loc_id."""
    if "loc_id" in df.columns:
        dupes = df["loc_id"].duplicated(keep=False)
        if dupes.any():
            dup_ids = df.loc[dupes, "loc_id"].unique()[:10]
            return [f"Duplicate loc_id after adaptation: {list(dup_ids)}"]
    return []


def _validate_v7_order(violations: list[str]) -> list[str]:
    """V7 ordering violations become needs_confirmation (never auto-reorder)."""
    return violations


def adapt_file(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    config: AdapterConfig | None = None,
    synthetic: bool | None = None,
    fx_rate: float | None = None,
    accept_uncertain: bool = False,
    column_mapping: dict[str, str] | None = None,  # For testing: bypass ML mapper
) -> AdaptationResult:
    """Main adapter entry point.

    Args:
        input_path: Path to input file (CSV, XLSX, GeoJSON)
        output_dir: Directory to write adapted.csv and adaptation_report.json
        config: AdapterConfig (uses DEFAULT_CONFIG if None)
        synthetic: Explicit synthetic declaration (True/False/None)
        fx_rate: Override FX rate (KES per USD)
        accept_uncertain: If True, treat needs_confirmation as accepted
        column_mapping: Pre-defined source->target mapping (for testing)

    Returns:
        AdaptationResult with status, paths, report, and proposals.
    """
    config = config or DEFAULT_CONFIG
    if fx_rate is not None:
        config = AdapterConfig(
            thresholds=config.thresholds,
            fx_rate_kes_usd=fx_rate,
            data_dir=config.data_dir,
            max_outside_nairobi_pct=config.max_outside_nairobi_pct,
            random_seed=config.random_seed,
        )

    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    adapted_path = output_dir / "adapted.csv"
    report_path = output_dir / "adaptation_report.json"

    # Step 1-2: Read the file once, then profile it
    df, fmt = read_file(input_path)
    profile = profile_file(input_path, config, preloaded=(df, fmt))

    # Step 3: Column mapping (placeholder for Phase 1 - use provided or identity)
    if column_mapping is None:
        # Identity mapping for columns that match target fields exactly
        column_mapping = {}
        for src in df.columns:
            if src in profile.headers and src in profile.headers:
                pass  # Will be handled by exact match in Phase 2
        # For Phase 1, we require explicit mapping or exact matches
        # Build basic mapping from exact matches
        column_mapping = {}
        for target in [
            "loc_id", "lat", "lon", "housing_class", "floor_area_m2",
            "cost_per_m2_kes", "tiv_kes", "synthetic", "source",
            "hazard_score_common", "hazard_score_occasional",
            "hazard_score_moderate", "hazard_score_severe", "hazard_score_extreme",
        ]:
            if target in df.columns:
                column_mapping[target] = target

        # Handle GeoJSON geometry columns
        if fmt == "geojson":
            if "_geometry_lat" in df.columns and "lat" not in column_mapping:
                column_mapping["_geometry_lat"] = "lat"
            if "_geometry_lon" in df.columns and "lon" not in column_mapping:
                column_mapping["_geometry_lon"] = "lon"

    # Convert to ColumnMappingEntry list (all exact matches for now)
    mappings = [
        ColumnMappingEntry(
            source_column=src,
            target_field=tgt,
            confidence=1.0,
            method="exact",
        )
        for src, tgt in column_mapping.items()
    ]

    # Step 4: Validate mapping confidence
    status, refusal_reasons, proposals = _validate_mapping_confidence(mappings, config.thresholds)
    if status == "refused":
        report = build_report(
            original_path=input_path,
            adapted_df=df,
            adapted_path=None,
            column_mappings=mappings,
            inferred_values=[],
            derived_values=[],
            flagged_rows=[],
            user_confirmations=[],
            transforms=[],
            model_info=[],
            status="refused",
            refusal_reasons=refusal_reasons,
            needs_confirmation_proposals=[],
            config=config,
            profile_dict=profile.to_dict(),
        )
        # Write report even for refused
        report_path.write_text(report.to_json())
        return AdaptationResult(
            status="refused",
            adapted_path=None,
            report_path=report_path,
            report=report,
            proposals=[],
        )

    # Step 5: Normalize values
    norm_result = normalize_values(df, column_mapping, config, synthetic_declared=synthetic)
    df_norm = norm_result.df

    # Initialize derived values list
    derived_values = []
    flagged_rows = []
    inferred_values = []

    # Add synthetic column if declared but not in CSV
    if synthetic is not None and "synthetic" not in df_norm.columns:
        df_norm["synthetic"] = synthetic
        # Add to derived values for report
        for i in range(len(df_norm)):
            derived_values.append(DerivedValueEntry(
                row=i,
                field="synthetic",
                value=synthetic,
                method="declared",
            ))

    # Step 6: Derive missing values (Phase 1: only loc_id generation, hazard scores passed through)

    # Generate loc_id if missing
    if "loc_id" not in column_mapping.values():
        if "loc_id" not in df_norm.columns:
            # Add sequential loc_id
            df_norm["loc_id"] = [f"ADAPT-{i:04d}" for i in range(len(df_norm))]
            for i in range(len(df_norm)):
                derived_values.append(DerivedValueEntry(
                    row=i,
                    field="loc_id",
                    value=f"ADAPT-{i:04d}",
                    method="sequential",
                ))

    # Flag rows outside Nairobi extent
    if "lat" in df_norm.columns and "lon" in df_norm.columns:
        lat_num = pd.to_numeric(df_norm["lat"], errors="coerce")
        lon_num = pd.to_numeric(df_norm["lon"], errors="coerce")
        outside = ~(
            (lat_num >= -1.45) & (lat_num <= -1.10) &
            (lon_num >= 36.60) & (lon_num <= 37.00)
        )
        for idx in df_norm.index[outside]:
            flagged_rows.append(FlaggedRowEntry(
                row=int(idx),
                reason="outside_nairobi_extent",
                details={"lat": float(lat_num.iloc[idx]) if pd.notna(lat_num.iloc[idx]) else None,
                         "lon": float(lon_num.iloc[idx]) if pd.notna(lon_num.iloc[idx]) else None}
            ))

    # Step 7: Validate
    # Required fields
    required = [
        "loc_id", "lat", "lon", "housing_class", "floor_area_m2",
        "cost_per_m2_kes", "tiv_kes", "synthetic", "source",
    ]
    refusal_reasons.extend(_validate_required_fields(mappings, required, synthetic))
    refusal_reasons.extend(_validate_tiv_present(mappings))
    refusal_reasons.extend(_validate_synthetic_declared(mappings, synthetic))
    refusal_reasons.extend(_validate_outside_nairobi(flagged_rows, config.max_outside_nairobi_pct, len(df_norm)))
    refusal_reasons.extend(_validate_duplicates(df_norm))

    # V7 order check on hazard scores (if present)
    hazard_cols = [f"hazard_score_{t}" for t in ("common", "occasional", "moderate", "severe", "extreme")]
    if all(c in df_norm.columns for c in hazard_cols):
        v7_violations = verify_v7_order(df_norm)
        if v7_violations:
            proposals.extend([{
                "type": "v7_order",
                "violations": v7_violations[:20],
                "message": "Hazard scores violate V7 ordering (common >= occasional >= moderate >= severe >= extreme). "
                           "Adapter will not reorder silently."
            }])

    # Final status determination
    if refusal_reasons:
        status = "refused"
    elif proposals and not accept_uncertain:
        status = "needs_confirmation"
    else:
        status = "accepted"

    # Step 8: Build report
    # Convert NormalizeResult transforms to TransformEntry
    transform_entries = [
        TransformEntry(
            field=t.field,
            operation=t.operation,
            before_sample=t.before_sample,
            after_sample=t.after_sample,
            parameters=t.parameters,
            rows_affected=t.rows_affected,
        )
        for t in norm_result.transforms
    ]

    report = build_report(
        original_path=input_path,
        adapted_df=df_norm,
        adapted_path=adapted_path,
        column_mappings=mappings,
        inferred_values=inferred_values,
        derived_values=derived_values,
        flagged_rows=flagged_rows,
        user_confirmations=[],
        transforms=transform_entries,
        model_info=[],
        status=status,
        refusal_reasons=refusal_reasons,
        needs_confirmation_proposals=proposals,
        config=config,
        profile_dict=profile.to_dict(),
    )

    # Step 9: Write outputs
    if status != "refused":
        # Rename columns from source names to target field names
        rename_map = {src: tgt for src, tgt in column_mapping.items() if src in df_norm.columns}
        df_out = df_norm.rename(columns=rename_map)

        # Ensure canonical column order
        canonical_order = [
            "loc_id", "lat", "lon", "housing_class", "floor_area_m2",
            "cost_per_m2_kes", "tiv_kes", "synthetic", "source",
            "hazard_score_common", "hazard_score_occasional",
            "hazard_score_moderate", "hazard_score_severe", "hazard_score_extreme",
        ]
        # Add any extra columns at the end
        extra_cols = [c for c in df_out.columns if c not in canonical_order]
        # Only include canonical columns that exist
        final_order = [c for c in canonical_order if c in df_out.columns] + extra_cols
        df_out = df_out[final_order]
        df_out.to_csv(adapted_path, index=False, lineterminator="\n")

    report_path.write_text(report.to_json())

    return AdaptationResult(
        status=status,
        adapted_path=adapted_path if status != "refused" else None,
        report_path=report_path,
        report=report,
        proposals=proposals,
    )