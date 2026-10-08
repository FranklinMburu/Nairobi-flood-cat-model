"""Tests for adapter.normalize module."""

import pandas as pd
import pytest

from adapter.normalize import (
    normalize_values,
    verify_v7_order,
    _detect_and_convert_area,
    _detect_and_convert_cost,
    _detect_and_convert_tiv,
    _normalize_boolean,
    _maybe_swap_lon_lat,
)
from adapter.config import AdapterConfig


class TestDetectAndConvertArea:
    def test_sqft_to_sqm(self):
        s = pd.Series(["10000", "20000", "30000"])
        converted, record = _detect_and_convert_area(s, "floor_area_m2")
        assert record is not None
        assert record.operation == "sqft_to_sqm"
        assert abs(converted.iloc[0] - 929.03) < 1

    def test_already_sqm(self):
        s = pd.Series(["50", "100", "200"])
        converted, record = _detect_and_convert_area(s, "floor_area_m2")
        assert record is None
        assert converted.iloc[0] == 50.0


class TestDetectAndConvertCost:
    def test_usd_to_kes(self):
        s = pd.Series(["50", "100", "200"])
        converted, record = _detect_and_convert_cost(s, "cost_per_m2_kes", 130.0)
        assert record is not None
        assert record.operation == "usd_to_kes"
        assert abs(converted.iloc[0] - 6500.0) < 1

    def test_thousands_to_raw(self):
        s = pd.Series(["5.5", "13.6", "20.0"])
        converted, record = _detect_and_convert_cost(s, "cost_per_m2_kes", 130.0)
        assert record is not None
        assert record.operation == "thousands_to_raw"
        assert converted.iloc[0] == 5500.0

    def test_already_kes(self):
        s = pd.Series(["5000", "10000", "20000"])
        converted, record = _detect_and_convert_cost(s, "cost_per_m2_kes", 130.0)
        assert record is None
        assert converted.iloc[0] == 5000.0


class TestDetectAndConvertTIV:
    def test_thousands_to_raw(self):
        s = pd.Series(["5000", "10000", "20000"])
        converted, record = _detect_and_convert_tiv(s, "tiv_kes")
        assert record is not None
        assert record.operation == "thousands_to_raw"
        assert converted.iloc[0] == 5_000_000

    def test_millions_to_raw(self):
        s = pd.Series(["5", "10", "20"])
        converted, record = _detect_and_convert_tiv(s, "tiv_kes")
        assert record is not None
        assert record.operation == "millions_to_raw"
        assert converted.iloc[0] == 5_000_000

    def test_already_raw(self):
        s = pd.Series(["5000000", "10000000", "20000000"])
        converted, record = _detect_and_convert_tiv(s, "tiv_kes")
        assert record is None


class TestNormalizeBoolean:
    def test_various_true(self):
        s = pd.Series(["True", "true", "1", "YES", "Y", "T"])
        converted, record = _normalize_boolean(s, "synthetic")
        assert record is not None
        assert all(converted.dropna())

    def test_various_false(self):
        s = pd.Series(["False", "false", "0", "NO", "N", "F"])
        converted, record = _normalize_boolean(s, "synthetic")
        assert record is not None
        assert not any(converted.dropna())

    def test_mixed(self):
        s = pd.Series(["True", "False", "1", "0"])
        converted, record = _normalize_boolean(s, "synthetic")
        assert converted.iloc[0] == True
        assert converted.iloc[1] == False
        assert converted.iloc[2] == True
        assert converted.iloc[3] == False


class TestMaybeSwapLonLat:
    def test_no_swap_needed(self):
        lat = pd.Series(["-1.3", "-1.2"])
        lon = pd.Series(["36.9", "36.8"])
        lat_out, lon_out, record = _maybe_swap_lon_lat(lat, lon)
        assert record is None
        assert lat_out.iloc[0] == -1.3
        assert lon_out.iloc[0] == 36.9

    def test_swap_needed(self):
        lat = pd.Series(["36.9", "36.8"])
        lon = pd.Series(["-1.3", "-1.2"])
        lat_out, lon_out, record = _maybe_swap_lon_lat(lat, lon)
        assert record is not None
        assert record.operation == "swap_lon_lat"
        assert lat_out.iloc[0] == -1.3
        assert lon_out.iloc[0] == 36.9


class TestNormalizeValues:
    def test_clean_passthrough(self):
        df = pd.read_csv("adapter/tests/fixtures/adapter/clean.csv", dtype=str)
        mapping = {c: c for c in df.columns}
        config = AdapterConfig()
        result = normalize_values(df, mapping, config)
        # Clean CSV still needs: synthetic boolean conversion, hazard scores to numeric
        assert len(result.transforms) >= 6  # synthetic + 5 hazard scores
        assert result.df.shape == df.shape
        # Verify synthetic was converted
        synth_transforms = [t for t in result.transforms if t.field == "synthetic"]
        assert len(synth_transforms) == 1
        assert synth_transforms[0].operation == "normalize_boolean"
        # Verify hazard scores converted to numeric
        hazard_transforms = [t for t in result.transforms if t.field.startswith("hazard_score_")]
        assert len(hazard_transforms) == 5

    def test_sqft_conversion(self):
        df = pd.read_csv("adapter/tests/fixtures/adapter/scrambled_units.csv", dtype=str)
        mapping = {c: c for c in df.columns}
        config = AdapterConfig()
        result = normalize_values(df, mapping, config)
        area_transforms = [t for t in result.transforms if t.field == "floor_area_m2"]
        assert len(area_transforms) == 1
        assert area_transforms[0].operation == "sqft_to_sqm"

    def test_cost_usd_conversion(self):
        df = pd.read_csv("adapter/tests/fixtures/adapter/usd_cost.csv", dtype=str)
        mapping = {c: c for c in df.columns}
        config = AdapterConfig()
        result = normalize_values(df, mapping, config)
        cost_transforms = [t for t in result.transforms if t.field == "cost_per_m2_kes"]
        assert len(cost_transforms) == 1
        assert cost_transforms[0].operation == "usd_to_kes"

    def test_tiv_thousands_conversion(self):
        df = pd.read_csv("adapter/tests/fixtures/adapter/scrambled_units.csv", dtype=str)
        mapping = {c: c for c in df.columns}
        config = AdapterConfig()
        result = normalize_values(df, mapping, config)
        tiv_transforms = [t for t in result.transforms if t.field == "tiv_kes"]
        assert len(tiv_transforms) == 1
        assert tiv_transforms[0].operation == "thousands_to_raw"

    def test_lat_lon_swap(self):
        df = pd.read_csv("adapter/tests/fixtures/adapter/swapped_coords.csv", dtype=str)
        mapping = {c: c for c in df.columns}
        config = AdapterConfig()
        result = normalize_values(df, mapping, config)
        swap_transforms = [t for t in result.transforms if t.field == "lat/lon"]
        assert len(swap_transforms) == 1
        assert swap_transforms[0].operation == "swap_lon_lat"


class TestVerifyV7Order:
    def test_valid_order(self):
        df = pd.DataFrame({
            "hazard_score_common": [0.5, 0.3],
            "hazard_score_occasional": [0.4, 0.2],
            "hazard_score_moderate": [0.3, 0.1],
            "hazard_score_severe": [0.2, 0.05],
            "hazard_score_extreme": [0.1, 0.0],
        })
        violations = verify_v7_order(df)
        assert violations == []

    def test_invalid_order(self):
        df = pd.DataFrame({
            "hazard_score_common": [0.3],
            "hazard_score_occasional": [0.4],
            "hazard_score_moderate": [0.2],
            "hazard_score_severe": [0.1],
            "hazard_score_extreme": [0.05],
        })
        violations = verify_v7_order(df)
        assert len(violations) == 1
        assert "common" in violations[0] and "occasional" in violations[0]

    def test_missing_columns(self):
        df = pd.DataFrame({"hazard_score_common": [0.5]})
        violations = verify_v7_order(df)
        assert len(violations) == 1
        assert "Missing hazard columns" in violations[0]

    def test_nan_values(self):
        df = pd.DataFrame({
            "hazard_score_common": [0.5, None],
            "hazard_score_occasional": [0.4, 0.3],
            "hazard_score_moderate": [0.3, 0.2],
            "hazard_score_severe": [0.2, 0.1],
            "hazard_score_extreme": [0.1, 0.05],
        })
        violations = verify_v7_order(df)
        assert violations == []