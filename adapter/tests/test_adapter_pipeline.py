"""Tests for adapter.pipeline module."""

import importlib.util
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from adapter.pipeline import adapt_file, AdaptationResult
from adapter.config import AdapterConfig
from loss_engine.validation import load_and_validate_exposure, ValidationError

# .xlsx input needs the optional "adapter" extra (openpyxl).
needs_openpyxl = pytest.mark.skipif(importlib.util.find_spec("openpyxl") is None,
                                    reason="openpyxl (the 'adapter' extra) is not installed")


class TestAdaptFile:
    def test_clean_csv_accepted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/clean.csv",
                output_dir=tmpdir,
                config=AdapterConfig(),
            )
            assert result.status == "accepted"
            assert result.adapted_path is not None
            assert result.adapted_path.exists()
            assert result.report_path.exists()

            # Check adapted CSV has canonical columns
            df = pd.read_csv(result.adapted_path)
            expected_cols = [
                "loc_id", "lat", "lon", "housing_class", "floor_area_m2",
                "cost_per_m2_kes", "tiv_kes", "synthetic", "source",
                "hazard_score_common", "hazard_score_occasional",
                "hazard_score_moderate", "hazard_score_severe", "hazard_score_extreme",
            ]
            for c in expected_cols:
                assert c in df.columns

    def test_clean_csv_passes_validator(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/clean.csv",
                output_dir=tmpdir,
                config=AdapterConfig(),
            )
            assert result.status == "accepted"

            # Should pass existing validator (V9 warns, V10 not run)
            # This will raise ValidationError if V1-V8 fail
            try:
                exposure = load_and_validate_exposure(result.adapted_path)
                # V9 should warn (not the original file)
                assert any(r.rule == "V9" and r.status == "warn" for r in exposure.report.results)
                # V10 should be not_run
                assert any(r.rule == "V10" and r.status == "not_run" for r in exposure.report.results)
            except ValidationError as e:
                pytest.fail(f"Validator failed: {e}")

    @needs_openpyxl
    def test_xlsx_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/xlsx_input.xlsx",
                output_dir=tmpdir,
                config=AdapterConfig(),
            )
            assert result.status == "accepted"
            assert result.adapted_path.exists()

    def test_geojson_points(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/geojson_points.geojson",
                output_dir=tmpdir,
                config=AdapterConfig(),
            )
            assert result.status == "accepted"
            assert result.adapted_path.exists()

    def test_missing_tiv_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/missing_tiv.csv",
                output_dir=tmpdir,
                config=AdapterConfig(),
            )
            assert result.status == "refused"
            assert any("tiv_kes is required" in r for r in result.report.refusal_reasons)

    def test_undeclared_synthetic_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/undeclared_synthetic.csv",
                output_dir=tmpdir,
                config=AdapterConfig(),
            )
            assert result.status == "refused"
            assert any("synthetic" in r.lower() for r in result.report.refusal_reasons)

    def test_synthetic_declared_accepted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/undeclared_synthetic.csv",
                output_dir=tmpdir,
                config=AdapterConfig(),
                synthetic=True,  # Explicitly declare
            )
            assert result.status == "accepted"

    def test_messy_headers_needs_confirmation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            # Messy headers won't match exactly, so need mapping
            # For Phase 1, exact mapping only - so this should refuse or need confirmation
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/messy_headers.csv",
                output_dir=tmpdir,
                config=AdapterConfig(),
            )
            # With exact mapping only, required fields missing -> refused
            assert result.status in ("refused", "needs_confirmation")

    def test_swapped_coords_accepted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/swapped_coords.csv",
                output_dir=tmpdir,
                config=AdapterConfig(),
            )
            assert result.status == "accepted"
            # Check lat/lon were swapped back
            df = pd.read_csv(result.adapted_path)
            assert df["lat"].iloc[0] < -1.1  # Nairobi latitude range
            assert df["lon"].iloc[0] > 36.6  # Nairobi longitude range

    def test_report_written_even_on_refuse(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/missing_tiv.csv",
                output_dir=tmpdir,
                config=AdapterConfig(),
            )
            assert result.status == "refused"
            assert result.report_path.exists()
            # Report should have refusal reasons
            report_json = result.report_path.read_text()
            assert "refused" in report_json
            assert "tiv_kes is required" in report_json

    def test_adaptation_report_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/clean.csv",
                output_dir=tmpdir,
                config=AdapterConfig(),
            )
            report = result.report
            assert report.adapter_version == "1.0.0"
            assert "original_file" in report.to_dict()
            assert "adapted_file" in report.to_dict()
            assert "config" in report.to_dict()
            assert "column_mappings" in report.to_dict()
            assert "transforms" in report.to_dict()

    def test_accept_uncertain_overrides_needs_confirmation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            # Create a case that would need confirmation (confidence between thresholds)
            # For Phase 1, we only have exact matches (confidence 1.0) so this is hard to test
            # This test is a placeholder for Phase 2
            pass


class TestAdaptationResult:
    def test_result_attributes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = adapt_file(
                input_path="adapter/tests/fixtures/adapter/clean.csv",
                output_dir=tmpdir,
                config=AdapterConfig(),
            )
            assert isinstance(result, AdaptationResult)
            assert result.status in ("accepted", "needs_confirmation", "refused")
            assert isinstance(result.proposals, list)