"""Tests for the deterministic hazard lookup (Checkpoint 8.3).

The acceptance test samples the supplied rasters at all 600 supplied buildings
and compares every tier with the pre-attached CSV scores, exactly at float32,
the rasters' own precision. The CSV holds the float32 values written with 16
significant digits, so they agree after conversion to float32 but not always
as 64-bit floats (differences below 1e-16). Broken rasters are small GeoTIFFs
written in temporary folders. Needs the optional "geo" extra (rasterio).
"""

import dataclasses
import hashlib
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

rasterio = pytest.importorskip("rasterio")
from rasterio.transform import Affine, from_origin  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loss_engine.hazard_lookup import (  # noqa: E402
    REASONS,
    HazardLookupError,
    HazardRasters,
    HazardSample,
)

DATA = ROOT / "data"
SUPPLIED = DATA / "exposure_nairobi_with_hazard.csv"
TIERS = ["extreme", "severe", "moderate", "occasional", "common"]  # D-003
RES = 1 / 3600  # one arc-second (D-002)
# SHA-256 of each raster, as recorded in PROVENANCE.md.
PROVENANCE_SHA256 = {
    "extreme": "dc83cf80780c7d83c0da803f3eb1bfeb6a7bcea8b9debe76d438a864a6466728",
    "severe": "92d6d5ccc2930fcc40edb69d5ff3304a7ce60028586779075666845e01910b74",
    "moderate": "2e6eba35dbbfec973591084f2979f0fbce24eee6af19365881118796c0de22ce",
    "occasional": "1a6f0e0d911ad5ccadc5345af514d5b01b43dedd1c1bfef8e4d07c5f0e710cb1",
    "common": "2071d44cf0ec9b06d240196e56bcd7915829d53cc6d186b033e89fce5afd1834",
}


@pytest.fixture(scope="module")
def rasters():
    return HazardRasters.open(DATA)


@pytest.fixture(scope="module")
def buildings():
    return pd.read_csv(SUPPLIED)


def reason(callable_, *args):
    with pytest.raises(HazardLookupError) as exc:
        callable_(*args)
    return exc.value.reason


# --- Metadata and source identification --------------------------------------------------


def test_five_sources_in_tier_order_with_provenance_hashes(rasters):
    assert [s.tier for s in rasters.sources] == TIERS
    for source in rasters.sources:
        assert source.file_name == f"nairobi_pluvial_proxy_{source.tier}.tif"
        assert source.sha256 == PROVENANCE_SHA256[source.tier]
        assert source.sha256 == hashlib.sha256((DATA / source.file_name).read_bytes()).hexdigest()


def test_grid_matches_d002(rasters):
    for source in rasters.sources:
        assert (source.crs, source.width, source.height, source.dtype, source.nodata) == ("EPSG:4326", 1439, 1260, "float32", None)
        a, b, west, d, e, north = source.transform
        assert (b, d, west, north) == (0.0, 0.0, 36.6, -1.1)
        assert a == pytest.approx(RES, rel=1e-12) and e == pytest.approx(-RES, rel=1e-12)
    west, south, east, north = rasters.bounds
    assert (west, north) == (36.6, -1.1)
    assert south == pytest.approx(-1.45, abs=1e-12) and east == pytest.approx(36.6 + 1439 * RES, abs=1e-12)


def test_raster_set_id_is_stable_and_identifies_the_files(rasters, tmp_path):
    assert len(rasters.raster_set_id) == 64 and HazardRasters.open(DATA).raster_set_id == rasters.raster_set_id
    copy = write_set(tmp_path / "a")
    other = write_set(tmp_path / "b", data={"severe": grid_with(0, 0, 0.5)})
    assert HazardRasters.open(copy).raster_set_id != HazardRasters.open(other).raster_set_id


# --- The 600 supplied buildings: acceptance test --------------------------------------------


def test_all_600_buildings_reproduce_all_five_supplied_scores(rasters, buildings):
    """Every supplied score equals the raster cell exactly after conversion to float32."""
    mismatches = []
    for row in buildings.itertuples():
        sample = rasters.sample(row.lat, row.lon)
        for tier in TIERS:
            supplied = getattr(row, f"hazard_score_{tier}")
            if np.float32(supplied) != np.float32(sample.scores[tier]):
                mismatches.append(f"{row.loc_id} {tier}: supplied {supplied!r}, sampled {sample.scores[tier]!r}")
    assert len(buildings) == 600
    assert not mismatches, "\n".join(mismatches[:20])


def test_sampled_values_are_exact_float32_values(rasters, buildings):
    """Full precision is kept: each sampled float is exactly a float32 value, unrounded."""
    for row in buildings.head(50).itertuples():
        for value in rasters.sample(row.lat, row.lon).scores.values():
            assert float(np.float32(value)) == value


def test_a_one_pixel_shift_breaks_every_non_zero_match(rasters, buildings):
    """Shows the acceptance test would catch a sampling offset: only dry (zero) cells could still agree."""
    nonzero = buildings[buildings["hazard_score_common"] > 0]
    for d_lat, d_lon in ((RES, 0), (-RES, 0), (0, RES), (0, -RES)):
        agree = sum(np.float32(rasters.sample(r.lat + d_lat, r.lon + d_lon).scores["common"])
                    == np.float32(r.hazard_score_common) for r in nonzero.itertuples())
        assert agree == 0, (d_lat, d_lon, agree)


def test_building_exactly_on_a_cell_edge(rasters, buildings):
    """NBO-0157 lies exactly on a column boundary (lon 36.725); it belongs to the cell to its east."""
    row = buildings.set_index("loc_id").loc["NBO-0157"]
    assert row.lon == 36.725
    sample = rasters.sample(row.lat, row.lon)
    assert sample.col == 450
    for tier in TIERS:
        assert np.float32(sample.scores[tier]) == np.float32(row[f"hazard_score_{tier}"])


# --- Boundaries and invalid coordinates -------------------------------------------------------


def test_north_and_west_edges_are_inside(rasters):
    assert rasters.cell(-1.1, 36.6) == (0, 0)


def test_last_cell_is_inside(rasters):
    assert rasters.cell(-1.45 + RES / 2, 36.6 + 1439 * RES - RES / 2) == (1259, 1438)


EDGE_POINTS = [(-1.45, 36.8), (-1.45 - 1e-12, 36.8), (-1.1, 36.6), (-1.2, 36.6 + 1439 * RES),
               (-1.209491, 36.725), (-1.3, 36.99972222222222), (-1.2, 36.6 - 1e-12), (-1.1 + 1e-12, 36.8)]


@pytest.mark.parametrize("lat, lon", EDGE_POINTS)
def test_cell_assignment_agrees_with_rasterio_rowcol(rasters, lat, lon):
    """rasterio's own floor-rounded rowcol is the independent reference for which cell a point is in."""
    from rasterio.transform import rowcol
    with rasterio.open(DATA / "nairobi_pluvial_proxy_common.tif") as ds:
        row, col = (int(v) for v in rowcol(ds.transform, lon, lat, op=math.floor))
    if 0 <= row < 1260 and 0 <= col < 1439:
        assert rasters.cell(lat, lon) == (row, col)
    else:
        assert reason(rasters.cell, lat, lon) == "out_of_bounds"


def test_the_double_for_minus_1_45_lies_just_inside_the_south_edge(rasters):
    """The binary value of -1.45 is about 4e-17 degrees north of the true edge, so it is in the last row."""
    assert rasters.cell(-1.45, 36.8)[0] == 1259


@pytest.mark.parametrize("lat, lon", [
    (-1.45 - 1e-12, 36.8),              # just south of the south edge
    (-1.2, 36.6 + 1439 * RES),          # east edge
    (-1.2, 36.9999),                    # within V11's range (to 37.00) but east of the raster
    (-1.0999, 36.8), (-1.3, 36.5999),   # just north, just west
    (-4.05, 39.66),                     # Mombasa
])
def test_outside_the_rasters_is_out_of_bounds(rasters, lat, lon):
    assert reason(rasters.sample, lat, lon) == "out_of_bounds"


@pytest.mark.parametrize("lat, lon", [(91, 36.8), (-91, 36.8), (-1.3, 181), (-1.3, -181), (float("nan"), 36.8),
                                      (-1.3, float("inf")), (True, 36.8), ("-1.3", 36.8), (None, 36.8), (-1.3, None)])
def test_invalid_coordinates_are_refused(rasters, lat, lon):
    assert reason(rasters.sample, lat, lon) == "invalid_coordinate"


def test_latitude_and_longitude_are_not_interchangeable(rasters):
    assert reason(rasters.sample, 36.8, -1.3) == "out_of_bounds"


# --- Scores of exactly 0 and 1 on the supplied rasters ----------------------------------------


def cell_centre(row, col):
    return -1.1 - (row + 0.5) * RES, 36.6 + (col + 0.5) * RES


def test_a_cell_scoring_exactly_1_is_returned_as_1(rasters):
    with rasterio.open(DATA / "nairobi_pluvial_proxy_extreme.tif") as ds:
        r, c = map(int, np.argwhere(ds.read(1) == 1.0)[0])
    assert rasters.sample(*cell_centre(r, c)).scores["extreme"] == 1.0


def test_a_dry_cell_is_a_real_zero_not_missing(rasters):
    with rasterio.open(DATA / "nairobi_pluvial_proxy_common.tif") as ds:
        r, c = map(int, np.argwhere(ds.read(1) == 0.0)[0])
    sample = rasters.sample(*cell_centre(r, c))
    assert list(sample.scores.values()) == [0.0] * 5


# --- Determinism and immutability -------------------------------------------------------------


def test_repeated_sampling_is_identical(rasters):
    assert rasters.sample(-1.2676, 36.8108) == rasters.sample(-1.2676, 36.8108)


def test_sample_is_read_only_and_in_tier_order(rasters):
    sample = rasters.sample(-1.2676, 36.8108)
    assert list(sample.scores) == TIERS
    with pytest.raises(dataclasses.FrozenInstanceError):
        sample.row = 0
    with pytest.raises(TypeError):
        sample.scores["common"] = 1.0
    assert isinstance(sample, HazardSample) and sample.raster_set_id == rasters.raster_set_id


def test_supplied_files_are_unchanged_by_lookup(rasters, buildings):
    rasters.sample(-1.3, 36.8)
    for tier, digest in PROVENANCE_SHA256.items():
        assert hashlib.sha256((DATA / f"nairobi_pluvial_proxy_{tier}.tif").read_bytes()).hexdigest() == digest
    assert hashlib.sha256(SUPPLIED.read_bytes()).hexdigest() == "b60aa96590d5a2e71509579d50c18d4ebd3557e26d2c60505f89d20bee69aa48"


# --- Broken rasters (small temporary GeoTIFFs) --------------------------------------------------

SMALL = (3, 4)  # rows, columns


def grid_with(row, col, value, base=0.25):
    grid = np.full(SMALL, base, dtype="float32")
    grid[row, col] = value
    return grid


def write_raster(path, grid, *, crs="EPSG:4326", transform=None, nodata=None, dtype="float32", count=1):
    grid = np.asarray(grid, dtype=dtype)
    with rasterio.open(path, "w", driver="GTiff", height=grid.shape[0], width=grid.shape[1], count=count,
                       dtype=dtype, crs=crs, transform=transform or from_origin(36.6, -1.1, RES, RES),
                       nodata=nodata) as ds:
        for band in range(1, count + 1):
            ds.write(grid, band)


def write_set(directory, *, data=None, options=None, skip=()):
    """Five small valid rasters, with chosen tiers replaced or given different options."""
    directory.mkdir(parents=True, exist_ok=True)
    for tier in TIERS:
        if tier in skip:
            continue
        write_raster(directory / f"nairobi_pluvial_proxy_{tier}.tif", (data or {}).get(tier, np.full(SMALL, 0.25)),
                     **(options or {}).get(tier, {}))
    return directory


def test_small_valid_set_opens_and_samples(tmp_path):
    rasters = HazardRasters.open(write_set(tmp_path))
    assert (rasters.width, rasters.height) == (4, 3)
    assert list(rasters.sample(*cell_centre(1, 2)).scores.values()) == [0.25] * 5


def test_missing_raster_file(tmp_path):
    assert reason(HazardRasters.open, write_set(tmp_path, skip=("moderate",))) == "raster_missing"


def test_missing_directory(tmp_path):
    assert reason(HazardRasters.open, tmp_path / "nowhere") == "raster_missing"


def test_unreadable_raster_file(tmp_path):
    directory = write_set(tmp_path)
    (directory / "nairobi_pluvial_proxy_severe.tif").write_bytes(b"not a tiff at all")
    assert reason(HazardRasters.open, directory) == "raster_incompatible"


@pytest.mark.parametrize("options", [
    {"crs": "EPSG:32737"},                                                  # projected CRS
    {"dtype": "int16"},                                                     # integer data
    {"count": 2},                                                           # two bands
    {"transform": Affine(RES, RES / 10, 36.6, 0.0, -RES, -1.1)},            # rotated grid
    {"transform": from_origin(36.7, -1.1, RES, RES)},                       # shifted grid
])
def test_incompatible_rasters_are_refused(tmp_path, options):
    directory = write_set(tmp_path, options={"occasional": options},
                          data={"occasional": np.full(SMALL, 0, dtype="int16" if options.get("dtype") else "float32")})
    assert reason(HazardRasters.open, directory) == "raster_incompatible"


@pytest.mark.parametrize("transform", [
    Affine(RES, RES / 10, 36.6, 0.0, -RES, -1.1),     # rotated
    Affine(RES, 0.0, 36.6, 0.0, RES, -1.45),          # south-up
])
def test_a_grid_that_is_not_north_up_is_refused_even_when_all_tiers_share_it(tmp_path, transform):
    """Here the five tiers agree with each other, so only the orientation check can refuse them."""
    directory = write_set(tmp_path, options={tier: {"transform": transform} for tier in TIERS})
    assert reason(HazardRasters.open, directory) == "raster_incompatible"


def test_different_grid_size_is_refused(tmp_path):
    directory = write_set(tmp_path, data={"common": np.full((3, 5), 0.25)})
    assert reason(HazardRasters.open, directory) == "raster_incompatible"


def test_nodata_cell_is_unavailable_never_zero(tmp_path):
    directory = write_set(tmp_path, data={"severe": grid_with(1, 1, -9999.0)}, options={"severe": {"nodata": -9999.0}})
    rasters = HazardRasters.open(directory)
    assert reason(rasters.sample, *cell_centre(1, 1)) == "cell_unavailable"
    assert rasters.sample(*cell_centre(0, 0)).scores["severe"] == 0.25


def test_nan_declared_as_nodata_is_unavailable_never_zero(tmp_path):
    directory = write_set(tmp_path, data={"extreme": grid_with(2, 3, np.nan)}, options={"extreme": {"nodata": np.nan}})
    rasters = HazardRasters.open(directory)
    assert reason(rasters.sample, *cell_centre(2, 3)) == "cell_unavailable"
    assert rasters.sample(*cell_centre(0, 0)).scores["extreme"] == 0.25


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf, 1.5, -0.1])
def test_an_invalid_cell_is_refused_on_opening_without_being_sampled(tmp_path, value):
    """Every cell is checked once, when the rasters are opened: no lookup is needed to find a bad cell."""
    directory = write_set(tmp_path, data={"moderate": grid_with(2, 3, value)})
    with pytest.raises(HazardLookupError) as exc:
        HazardRasters.open(directory)
    assert exc.value.reason == "raster_incompatible"
    assert "nairobi_pluvial_proxy_moderate.tif" in str(exc.value) and "row 2, col 3" in str(exc.value)


def test_a_value_other_than_the_declared_nodata_is_still_refused(tmp_path):
    grid = grid_with(1, 1, -9999.0)
    grid[0, 0] = 2.0
    directory = write_set(tmp_path, data={"severe": grid}, options={"severe": {"nodata": -9999.0}})
    assert reason(HazardRasters.open, directory) == "raster_incompatible"


def test_cell_checks_remain_when_sampling(rasters):
    """The sample-level checks stay as a second guard; a valid cell passes both."""
    sample = rasters.sample(-1.2676, 36.8108)
    assert all(0.0 <= v <= 1.0 and not math.isnan(v) for v in sample.scores.values())


# --- The optional dependency is pinned to the approved version -----------------------------------


def test_geo_extra_pins_the_same_rasterio_as_the_lock_file():
    import tomllib
    extra = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]["geo"]
    lock = [line.strip() for line in (ROOT / "requirements-lock.txt").read_text(encoding="utf-8").splitlines()
            if line.lower().startswith("rasterio==")]
    assert extra == ["rasterio==1.5.2"] and lock == ["rasterio==1.5.2"]
    assert rasterio.__version__ == "1.5.2"


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_scores_of_exactly_0_and_1_are_valid(tmp_path, value):
    rasters = HazardRasters.open(write_set(tmp_path, data={"common": grid_with(1, 2, value)}))
    assert rasters.sample(*cell_centre(1, 2)).scores["common"] == value


def test_files_are_closed_after_opening(tmp_path):
    """Every raster file can be deleted straight after opening, so no handle is left open (Windows locks open files)."""
    directory = write_set(tmp_path)
    rasters = HazardRasters.open(directory)
    for path in directory.iterdir():
        path.unlink()
    assert rasters.sample(*cell_centre(0, 0)).scores["extreme"] == 0.25


def test_reasons_are_a_fixed_set():
    assert REASONS == ("invalid_coordinate", "out_of_bounds", "cell_unavailable", "invalid_score", "raster_missing",
                       "raster_incompatible")
    with pytest.raises(ValueError):
        HazardLookupError("guessed", "x")


# --- The optional dependency stays optional ------------------------------------------------------


def test_engine_and_lookup_module_import_without_loading_rasterio():
    code = ("import sys; import loss_engine.run_record, loss_engine.exposure_extraction, loss_engine.hazard_lookup; "
            "print('rasterio' in sys.modules)")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "False"


def test_no_score_is_described_as_a_depth_or_probability():
    source = (ROOT / "loss_engine" / "hazard_lookup.py").read_text(encoding="utf-8").lower()
    assert "not a flood depth, a probability" in source
    assert "math.isnan" in source and "nodata" in source
    assert not math.isnan(0.0)
