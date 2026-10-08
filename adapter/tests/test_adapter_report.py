"""Tests for adapter.report module."""

import json
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from adapter.report import (
    AdaptationReport,
    ColumnMappingEntry,
    InferredValueEntry,
    DerivedValueEntry,
    FlaggedRowEntry,
    UserConfirmationEntry,
    TransformEntry,
    ModelInfo,
    build_report,
    _sha256_file,
)
from adapter.config import AdapterConfig


class TestAdaptationReport:
    def test_to_dict(self):
        report = AdaptationReport(
            column_mappings=[
                ColumnMappingEntry("src", "tgt", 0.9, "ml"),
            ],
            inferred_values=[
                InferredValueEntry(0, "field", "value", 0.8, "ml"),
            ],
        )
        d = report.to_dict()
        assert d["column_mappings"][0]["source_column"] == "src"
        assert d["inferred_values"][0]["row"] == 0

    def test_to_json(self):
        report = AdaptationReport()
        json_str = report.to_json()
        assert "adapter_version" in json_str
        assert json_str.endswith("\n")

    def test_from_json(self):
        report = AdaptationReport(status="accepted")
        json_str = report.to_json()
        loaded = AdaptationReport.from_json(json_str)
        assert loaded.status == "accepted"


class TestBuildReport:
    def test_build_basic_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            original = tmpdir / "original.csv"
            adapted = tmpdir / "adapted.csv"
            
            df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
            df.to_csv(original, index=False)
            df.to_csv(adapted, index=False)

            config = AdapterConfig()
            report = build_report(
                original_path=original,
                adapted_df=df,
                adapted_path=adapted,
                column_mappings=[],
                inferred_values=[],
                derived_values=[],
                flagged_rows=[],
                user_confirmations=[],
                transforms=[],
                model_info=[],
                status="accepted",
                refusal_reasons=[],
                needs_confirmation_proposals=[],
                config=config,
                profile_dict={"format": "csv", "n_rows": 2},
            )

            assert report.status == "accepted"
            assert report.original_file["sha256"] == _sha256_file(original)
            assert report.adapted_file["sha256"] == _sha256_file(adapted)
            assert report.config["fx_rate_kes_usd"] == 130.0

    def test_report_includes_model_info(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            original = tmpdir / "original.csv"
            adapted = tmpdir / "adapted.csv"
            df = pd.DataFrame({"a": [1]})
            df.to_csv(original, index=False)
            df.to_csv(adapted, index=False)

            model_info = [
                ModelInfo(
                    name="column_mapper",
                    version="1.0.0",
                    sha256="abc123",
                    sklearn_version="1.5.0",
                    python_version="3.13.0",
                    training_date="2026-01-01",
                    caveats=["test caveat"],
                )
            ]
            config = AdapterConfig()
            report = build_report(
                original_path=original,
                adapted_df=df,
                adapted_path=adapted,
                column_mappings=[],
                inferred_values=[],
                derived_values=[],
                flagged_rows=[],
                user_confirmations=[],
                transforms=[],
                model_info=model_info,
                status="accepted",
                refusal_reasons=[],
                needs_confirmation_proposals=[],
                config=config,
                profile_dict={},
            )

            assert len(report.model_info) == 1
            assert report.model_info[0].name == "column_mapper"
            assert report.model_info[0].caveats == ["test caveat"]