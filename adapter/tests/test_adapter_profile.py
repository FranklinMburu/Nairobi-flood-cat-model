"""Tests for adapter.profile module."""

import json
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from adapter.profile import profile_file, read_file, FileProfile


class TestReadFile:
    def test_read_csv(self):
        df, fmt = read_file("adapter/tests/fixtures/adapter/clean.csv")
        assert fmt == "csv"
        assert len(df) == 20
        assert "loc_id" in df.columns

    def test_read_xlsx(self):
        df, fmt = read_file("adapter/tests/fixtures/adapter/xlsx_input.xlsx")
        assert fmt == "xlsx"
        assert len(df) == 20
        assert "loc_id" in df.columns

    def test_read_geojson_points(self):
        df, fmt = read_file("adapter/tests/fixtures/adapter/geojson_points.geojson")
        assert fmt == "geojson"
        assert len(df) == 20
        assert "loc_id" in df.columns
        assert "_geometry_lon" in df.columns
        assert "_geometry_lat" in df.columns

    def test_read_geojson_polygons(self):
        df, fmt = read_file("adapter/tests/fixtures/adapter/geojson_polygons.geojson")
        assert fmt == "geojson"
        assert len(df) == 20
        assert "_geometry_polygon" in df.columns


class TestProfileFile:
    def test_profile_clean_csv(self):
        profile = profile_file("adapter/tests/fixtures/adapter/clean.csv")
        assert isinstance(profile, FileProfile)
        assert profile.format == "csv"
        assert profile.n_rows == 20
        assert profile.n_cols == 14
        # SHA256 of the 20-row subset, not the full 600-row file
        assert len(profile.sha256) == 64
        assert "loc_id" in profile.headers
        assert "lat" in profile.coord_candidates
        assert "lon" in profile.coord_candidates
        assert "housing_class" in profile.class_candidates
        assert "tiv_kes" in profile.tiv_candidates
        assert "synthetic" in profile.synthetic_candidates
        assert len(profile.hazard_candidates) == 5

    def test_profile_xlsx(self):
        profile = profile_file("adapter/tests/fixtures/adapter/xlsx_input.xlsx")
        assert profile.format == "xlsx"
        assert profile.n_rows == 20

    def test_profile_geojson_points(self):
        profile = profile_file("adapter/tests/fixtures/adapter/geojson_points.geojson")
        assert profile.format == "geojson"
        assert profile.n_rows == 20
        assert profile.geojson_geometry_types == ["Point"]
        assert profile.geojson_point_count == 20
        assert profile.geojson_polygon_count == 0
        assert profile.geojson_crs == "EPSG:4326"

    def test_profile_geojson_polygons(self):
        profile = profile_file("adapter/tests/fixtures/adapter/geojson_polygons.geojson")
        assert profile.format == "geojson"
        assert profile.geojson_geometry_types == ["Polygon"]
        assert profile.geojson_polygon_count == 20
        assert profile.geojson_point_count == 0
        # Warning about polygons
        assert any("polygon" in w.lower() for w in profile.warnings)

    def test_profile_messy_headers(self):
        profile = profile_file("adapter/tests/fixtures/adapter/messy_headers.csv")
        assert profile.n_cols == 17  # 14 + 3 junk
        assert "Loc ID" in profile.headers
        assert "junk_col_1" in profile.headers

    def test_profile_to_dict(self):
        profile = profile_file("adapter/tests/fixtures/adapter/clean.csv")
        d = profile.to_dict()
        assert d["format"] == "csv"
        assert d["n_rows"] == 20
        assert "loc_id" in d["headers"]


class TestProfileMissingColumns:
    def test_missing_class(self):
        profile = profile_file("adapter/tests/fixtures/adapter/missing_class.csv")
        assert "housing_class" not in profile.headers
        assert "housing_class" not in profile.class_candidates

    def test_missing_hazard(self):
        profile = profile_file("adapter/tests/fixtures/adapter/missing_hazard.csv")
        assert len(profile.hazard_candidates) == 0

    def test_missing_tiv(self):
        profile = profile_file("adapter/tests/fixtures/adapter/missing_tiv.csv")
        assert "tiv_kes" not in profile.headers
        assert "tiv_kes" not in profile.tiv_candidates

    def test_undeclared_synthetic(self):
        profile = profile_file("adapter/tests/fixtures/adapter/undeclared_synthetic.csv")
        assert "synthetic" not in profile.headers
        assert "synthetic" not in profile.synthetic_candidates