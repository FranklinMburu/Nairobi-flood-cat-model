"""Deterministic hazard lookup at a stated location (Checkpoint 8.3).

Reads the five supplied Nairobi pluvial proxy rasters and returns, for one
latitude and longitude, the supplied score of each tier. A score is a
relative susceptibility score from 0 to 1: not a flood depth, a probability
or a return period (D-002; specification 3.3). Nothing here geocodes, adjusts
a score or calculates anything financial.

Grid (D-002, and checked when the rasters are opened): EPSG:4326, one band,
float32, all five tiers on the same axis-aligned grid. A point belongs to the
cell whose west and north edges it lies on or east and south of:

    col = floor((lon - west) / cell width)
    row = floor((north - lat) / cell height)

so a cell includes its west and north edges and excludes its east and south
edges. This reproduces all 3,000 pre-attached scores of the supplied exposure
file exactly at float32 precision (see tests/test_hazard_lookup.py).

rasterio is an optional dependency (the "geo" extra) and is imported only when
rasters are opened, so the rest of the engine runs without it.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .config import TIERS

RASTER_FILE = "nairobi_pluvial_proxy_{tier}.tif"
EXPECTED_CRS = "EPSG:4326"
EXPECTED_DTYPE = "float32"
REASONS = ("invalid_coordinate", "out_of_bounds", "cell_unavailable", "invalid_score", "raster_missing",
           "raster_incompatible")


class HazardLookupError(ValueError):
    """A lookup or raster that cannot give a valid score. `reason` is one of REASONS. Never a fallback to 0."""

    def __init__(self, reason: str, message: str):
        if reason not in REASONS:
            raise ValueError(f"unknown reason {reason!r}")
        self.reason = reason
        super().__init__(f"{reason}: {message}")


@dataclass(frozen=True)
class RasterSource:
    """One tier's raster file: what was read and the grid it describes."""

    tier: str
    file_name: str
    sha256: str
    crs: str
    width: int
    height: int
    transform: tuple[float, float, float, float, float, float]
    dtype: str
    nodata: float | None

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class HazardSample:
    """The five supplied scores at one location, in tier order (extreme to common).

    `scores` are the exact float32 cell values held as Python floats.
    `row` and `col` locate the cell; `raster_set_id` identifies the rasters read.
    """

    lat: float
    lon: float
    row: int
    col: int
    scores: Mapping
    raster_set_id: str

    def __post_init__(self):
        object.__setattr__(self, "scores", MappingProxyType(dict(self.scores)))


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


class HazardRasters:
    """The five tier rasters, checked once and held in memory. Use HazardRasters.open()."""

    def __init__(self, sources: tuple[RasterSource, ...], grids: Mapping):
        self.sources = sources
        self._grids = grids
        first = sources[0]
        self.width, self.height = first.width, first.height
        self._west, self._north = first.transform[2], first.transform[5]
        self._cell_width, self._cell_height = first.transform[0], -first.transform[4]
        text = json.dumps([s.to_dict() for s in sources], sort_keys=True, separators=(",", ":"))
        self._raster_set_id = hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def open(cls, data_dir: str | Path) -> "HazardRasters":
        """Read and check the five rasters in data_dir. Every file is closed before this returns."""
        try:
            import numpy as np
            import rasterio
            from rasterio.io import MemoryFile
        except ImportError as error:  # pragma: no cover - depends on the installation
            raise ImportError("hazard lookup needs the optional 'geo' extra: pip install -e .[geo]") from error

        sources, grids = [], {}
        for tier in TIERS:
            path = Path(data_dir) / RASTER_FILE.format(tier=tier)
            if not path.is_file():
                raise HazardLookupError("raster_missing", f"{path.name} not found in {Path(data_dir)}")
            # Read the bytes once: the hash and the grid come from the same bytes.
            data = path.read_bytes()
            try:
                with MemoryFile(data) as memory, memory.open() as dataset:
                    crs = dataset.crs.to_string() if dataset.crs is not None else None
                    count, dtype, nodata = dataset.count, dataset.dtypes[0], dataset.nodata
                    transform = tuple(float(v) for v in tuple(dataset.transform)[:6])
                    width, height = dataset.width, dataset.height
                    grid = dataset.read(1)
            except rasterio.errors.RasterioError as error:
                raise HazardLookupError("raster_incompatible", f"{path.name} could not be read as a raster: {error}")

            problems = []
            if count != 1:
                problems.append(f"{count} bands, expected 1")
            if crs != EXPECTED_CRS:
                problems.append(f"CRS {crs}, expected {EXPECTED_CRS}")
            if dtype != EXPECTED_DTYPE:
                problems.append(f"data type {dtype}, expected {EXPECTED_DTYPE}")
            a, b, _, d, e, _ = transform
            if not (a > 0 and e < 0 and b == 0 and d == 0):
                problems.append(f"transform {transform} is not a north-up grid")
            if problems:
                raise HazardLookupError("raster_incompatible", f"{path.name}: " + "; ".join(problems))

            source = RasterSource(tier, path.name, hashlib.sha256(data).hexdigest(), crs, width, height, transform,
                                  dtype, None if nodata is None else float(nodata))
            if sources and (width, height, transform) != (sources[0].width, sources[0].height, sources[0].transform):
                raise HazardLookupError("raster_incompatible",
                                        f"{path.name} is not on the same grid as {sources[0].file_name}")
            grid = np.array(grid, dtype=np.float32, copy=True)
            # Every cell, once: a score from 0 to 1, or the declared NoData value. Nothing is replaced.
            valid = np.isfinite(grid) & (grid >= 0) & (grid <= 1)
            if source.nodata is not None:
                valid |= np.isnan(grid) if math.isnan(source.nodata) else (grid == source.nodata)
            if not valid.all():
                row, col = (int(i) for i in np.argwhere(~valid)[0])
                raise HazardLookupError(
                    "raster_incompatible",
                    f"{path.name}: {int((~valid).sum())} cell(s) are neither a score from 0 to 1 nor the declared "
                    f"NoData value; the first is {float(grid[row, col])} at row {row}, col {col}")
            grid.setflags(write=False)
            sources.append(source)
            grids[tier] = grid
        return cls(tuple(sources), MappingProxyType(grids))

    @property
    def raster_set_id(self) -> str:
        """SHA-256 of the five sources (file names, hashes and grids) in a fixed JSON form."""
        return self._raster_set_id

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """west, south, east, north of the grid."""
        return (self._west, self._north - self.height * self._cell_height,
                self._west + self.width * self._cell_width, self._north)

    def cell(self, lat, lon) -> tuple[int, int]:
        """The row and column of the cell containing (lat, lon), or HazardLookupError."""
        for name, value, limit in (("lat", lat, 90), ("lon", lon, 180)):
            if not (_is_number(value) and -limit <= value <= limit):
                raise HazardLookupError("invalid_coordinate", f"{name} must be a finite number within ±{limit}, got {value!r}")
        col = math.floor((lon - self._west) / self._cell_width)
        row = math.floor((self._north - lat) / self._cell_height)
        if not (0 <= row < self.height and 0 <= col < self.width):
            west, south, east, north = self.bounds
            raise HazardLookupError("out_of_bounds",
                                    f"({lat}, {lon}) is outside the rasters (lat {south} to {north}, lon {west} to {east})")
        return row, col

    def sample(self, lat, lon) -> HazardSample:
        """The five supplied scores at (lat, lon). A missing or invalid cell raises; it is never read as 0."""
        row, col = self.cell(lat, lon)
        scores = {}
        for source in self.sources:
            value = float(self._grids[source.tier][row, col])
            if math.isnan(value) or (source.nodata is not None and value == source.nodata):
                raise HazardLookupError("cell_unavailable", f"{source.file_name} has no value at row {row}, col {col}")
            if not 0.0 <= value <= 1.0:
                raise HazardLookupError("invalid_score",
                                        f"{source.file_name} has {value} at row {row}, col {col}; scores run from 0 to 1")
            scores[source.tier] = value
        return HazardSample(lat, lon, row, col, scores, self.raster_set_id)
