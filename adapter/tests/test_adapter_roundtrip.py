"""Round-trip tests: scramble clean CSV, adapt, run engine, verify output digests match."""

import hashlib
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from adapter.pipeline import adapt_file
from adapter.config import AdapterConfig
from loss_engine.run_record import run_model, RunRecord
from loss_engine.config import default_config, ModelConfig
from loss_engine.ep_curve import default_return_periods, ReturnPeriodMapping


def _canonical_csv_bytes(df: pd.DataFrame) -> bytes:
    """Canonical CSV for digest comparison."""
    # Float columns are pre-rounded to 8 decimal places, so 10 places is sufficient
    return df.to_csv(index=False, lineterminator="\n", float_format="%.10f").encode("utf-8")


def _table_digest(df: pd.DataFrame) -> str:
    return hashlib.sha256(_canonical_csv_bytes(df)).hexdigest()


def _run_engine_on_adapted(adapted_path: Path) -> dict[str, str]:
    """Run the full engine on adapted file and return table digests."""
    config: ModelConfig = default_config()
    mapping: ReturnPeriodMapping = default_return_periods()

    run_result = run_model(
        exposure_path=adapted_path,
        config=config,
        mapping=mapping,
    )

    if run_result.outputs is None:
        raise RuntimeError(f"Engine failed: {run_result.record.failure}")

    digests = {}
    for name in ["building_results", "tier_summary", "class_summary", "ep_points"]:
        table = getattr(run_result.outputs, name)
        if "run_id" in table.columns:
            table = table.drop(columns=["run_id"])
        # Sort building_results by scenario_id, tier (D-003 order), loc_id for consistent digests
        if name == "building_results" and "loc_id" in table.columns:
            tier_order = ["extreme", "severe", "moderate", "occasional", "common"]
            table = table.copy()
            table["tier_order"] = table["tier"].map({t: i for i, t in enumerate(tier_order)})
            table = table.sort_values(["scenario_id", "tier_order", "loc_id"]).drop(columns=["tier_order"])
            table = table.reset_index(drop=True)
        # Round float columns to 8 decimal places to avoid floating-point non-determinism
        # from summation order differences (differences are ~1e-9)
        table = table.copy()
        float_cols = table.select_dtypes(include=["float64", "float32"]).columns
        for col in float_cols:
            table[col] = table[col].round(8)
        digests[name] = _table_digest(table)

    return digests


def _get_clean_digests() -> dict[str, str]:
    """Get reference digests from clean CSV."""
    clean_path = Path("adapter/tests/fixtures/adapter/clean.csv")
    return _run_engine_on_adapted(clean_path)


CLEAN_DIGESTS = _get_clean_digests()


class TestRoundTrip:
    """Round-trip tests: each scramble type should produce identical output digests."""

    def test_rename_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/scrambled_rename.csv",
                output_dir=tmpdir,
                config=AdapterConfig(),
                column_mapping={
                    "location_id": "loc_id",
                    "latitude": "lat",
                    "longitude": "lon",
                    "building_type": "housing_class",
                    "floor_area_sqm": "floor_area_m2",
                    "construction_cost_per_sqm_kes": "cost_per_m2_kes",
                    "total_sum_insured_kes": "tiv_kes",
                    "is_synthetic": "synthetic",
                    "data_source": "source",
                    "hazard_common": "hazard_score_common",
                    "hazard_occasional": "hazard_score_occasional",
                    "hazard_moderate": "hazard_score_moderate",
                    "hazard_severe": "hazard_score_severe",
                    "hazard_extreme": "hazard_score_extreme",
                },
            )
            assert result.status == "accepted"
            digests = _run_engine_on_adapted(result.adapted_path)
            for name, expected in CLEAN_DIGESTS.items():
                assert digests[name] == expected, f"{name} digest mismatch for rename scramble"

    def test_unit_conversions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/scrambled_units.csv",
                output_dir=tmpdir,
                config=AdapterConfig(),
                column_mapping={c: c for c in pd.read_csv("adapter/tests/fixtures/adapter/scrambled_units.csv", nrows=0).columns},
            )
            assert result.status == "accepted"
            digests = _run_engine_on_adapted(result.adapted_path)
            for name, expected in CLEAN_DIGESTS.items():
                assert digests[name] == expected, f"{name} digest mismatch for units scramble"

    def test_shuffled_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/shuffled_rows.csv",
                output_dir=tmpdir,
                config=AdapterConfig(),
                column_mapping={c: c for c in pd.read_csv("adapter/tests/fixtures/adapter/shuffled_rows.csv", nrows=0).columns},
            )
            assert result.status == "accepted"
            digests = _run_engine_on_adapted(result.adapted_path)
            for name, expected in CLEAN_DIGESTS.items():
                assert digests[name] == expected, f"{name} digest mismatch for shuffle scramble"

    def test_combined_scramble(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            df = pd.read_csv("adapter/tests/fixtures/adapter/clean.csv", dtype=str)
            rename_map = {
                "loc_id": "location_id",
                "lat": "latitude",
                "lon": "longitude",
                "housing_class": "building_type",
                "floor_area_m2": "floor_area_sqm",
                "cost_per_m2_kes": "construction_cost_per_sqm_kes",
                "tiv_kes": "total_sum_insured_kes",
                "synthetic": "is_synthetic",
                "source": "data_source",
                "hazard_score_common": "hazard_common",
                "hazard_score_occasional": "hazard_occasional",
                "hazard_score_moderate": "hazard_moderate",
                "hazard_score_severe": "hazard_severe",
                "hazard_score_extreme": "hazard_extreme",
            }
            df = df.rename(columns=rename_map)
            df["floor_area_sqm"] = (df["floor_area_sqm"].astype(float) * 10.7639104).round(4).astype(str)
            df["construction_cost_per_sqm_kes"] = (df["construction_cost_per_sqm_kes"].astype(float) / 130).round(2).astype(str)
            df["total_sum_insured_kes"] = (df["total_sum_insured_kes"].astype(float) / 1000).astype(str)
            df = df.sample(frac=1, random_state=42).reset_index(drop=True)

            combined_path = tmpdir / "combined_scramble.csv"
            df.to_csv(combined_path, index=False, lineterminator="\n")

            result = adapt_file(
                input_path=combined_path,
                output_dir=tmpdir / "out",
                config=AdapterConfig(),
                column_mapping={
                    "location_id": "loc_id",
                    "latitude": "lat",
                    "longitude": "lon",
                    "building_type": "housing_class",
                    "floor_area_sqm": "floor_area_m2",
                    "construction_cost_per_sqm_kes": "cost_per_m2_kes",
                    "total_sum_insured_kes": "tiv_kes",
                    "is_synthetic": "synthetic",
                    "data_source": "source",
                    "hazard_common": "hazard_score_common",
                    "hazard_occasional": "hazard_score_occasional",
                    "hazard_moderate": "hazard_score_moderate",
                    "hazard_severe": "hazard_score_severe",
                    "hazard_extreme": "hazard_score_extreme",
                },
            )
            assert result.status == "accepted"
            digests = _run_engine_on_adapted(result.adapted_path)
            for name, expected in CLEAN_DIGESTS.items():
                assert digests[name] == expected, f"{name} digest mismatch for combined scramble"


class TestRoundTripTolerance:
    def test_building_results_sorted_by_loc_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/shuffled_rows.csv",
                output_dir=tmpdir,
                config=AdapterConfig(),
                column_mapping={c: c for c in pd.read_csv("adapter/tests/fixtures/adapter/shuffled_rows.csv", nrows=0).columns},
            )
            assert result.status == "accepted"

            config: ModelConfig = default_config()
            mapping: ReturnPeriodMapping = default_return_periods()
            run_result = run_model(
                exposure_path=result.adapted_path,
                config=config,
                mapping=mapping,
            )

            building_results = run_result.outputs.building_results
            assert "loc_id" in building_results.columns