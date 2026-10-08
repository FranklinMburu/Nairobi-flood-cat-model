"""Input loading and validation for the deterministic loss engine.

Implements rules V1 to V11 of section 5.1 of
`claude/07 - Deterministic Loss Engine Specification` (revision 2).

This module reads the exposure file, checks it, and either returns the data
with a full validation report or raises ValidationError. It never repairs,
reorders, clips or fills in data. It contains no loss calculation.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

# --- The input contract, taken from the frozen specification -----------------

EXPECTED_SHA256 = "b60aa96590d5a2e71509579d50c18d4ebd3557e26d2c60505f89d20bee69aa48"
EXPECTED_ROWS = 600
EXPECTED_TOTAL_TIV = Decimal("63635075000")

HOUSING_CLASSES = (
    "informal_iron_sheet",
    "semi_permanent",
    "permanent_masonry",
    "concrete_rcc",
)

# Widest footprint first. V7 requires scores to be non-increasing in this order.
TIERS_WIDEST_FIRST = ("common", "occasional", "moderate", "severe", "extreme")
SCORE_COLUMNS = tuple(f"hazard_score_{tier}" for tier in TIERS_WIDEST_FIRST)

REQUIRED_COLUMNS = (
    "loc_id",
    "lat",
    "lon",
    "housing_class",
    "floor_area_m2",
    "cost_per_m2_kes",
    "tiv_kes",
    "synthetic",
    "source",
) + SCORE_COLUMNS

# V2: fields that may not be blank.
NON_MISSING_COLUMNS = ("loc_id", "housing_class", "tiv_kes") + SCORE_COLUMNS

TIER_TOLERANCE = 1e-9
LAT_RANGE = (-1.45, -1.10)
LON_RANGE = (36.60, 37.00)

# The action each rule takes when it is violated (specification 5.1).
RULES = {
    "V1": ("fail", "All required columns are present"),
    "V2": ("fail", "No missing value in loc_id, housing_class, tiv_kes or any hazard score"),
    "V3": ("fail", "loc_id is unique"),
    "V4": ("fail", "housing_class is one of the four configured classes"),
    "V5": ("fail", "tiv_kes is a finite number above 0"),
    "V6": ("fail", "Every hazard score is a finite number from 0 to 1 inclusive"),
    "V7": ("fail", "common >= occasional >= moderate >= severe >= extreme, tolerance 1e-9"),
    "V8": ("warn", "synthetic is True on every row"),
    "V9": ("warn", "File SHA-256 matches the supplied file"),
    "V10": ("fail", "When V9 passes: 600 rows and total TIV of exactly 63,635,075,000"),
    "V11": ("warn", "lat and lon lie inside the raster extent"),
}


# --- Result types ------------------------------------------------------------


@dataclass(frozen=True)
class RuleResult:
    """Outcome of one rule. status is pass, fail, warn or not_run."""

    rule: str
    description: str
    action: str
    status: str
    detail: str = ""


@dataclass
class ValidationReport:
    """Every rule's outcome, in rule order. This feeds the run record later."""

    results: list[RuleResult] = field(default_factory=list)

    @property
    def failures(self) -> list[RuleResult]:
        return [r for r in self.results if r.status == "fail"]

    @property
    def warnings(self) -> list[RuleResult]:
        return [r for r in self.results if r.status == "warn"]

    @property
    def passed(self) -> bool:
        return not self.failures

    def status_of(self, rule: str) -> str:
        return next(r.status for r in self.results if r.rule == rule)


@dataclass
class LoadedExposure:
    """A validated exposure file plus the facts the run record will need."""

    data: pd.DataFrame
    filename: str
    sha256: str
    row_count: int
    total_tiv: Decimal
    report: ValidationReport


class ValidationError(Exception):
    """Raised when at least one fail rule is violated. No loss may be computed."""

    def __init__(self, report: ValidationReport, filename: str, sha256: str):
        self.report = report
        self.filename = filename
        self.sha256 = sha256
        lines = [f"{r.rule}: {r.description}. {r.detail}" for r in report.failures]
        super().__init__(
            f"Input validation failed for {filename} "
            f"({len(lines)} rule(s)):\n  " + "\n  ".join(lines)
        )


# --- Helpers -----------------------------------------------------------------


def _where(df: pd.DataFrame, bad: np.ndarray, limit: int = 5) -> str:
    """Describe offending rows by CSV line number and loc_id, without changing them."""
    idx = np.flatnonzero(bad)
    shown = []
    for i in idx[:limit]:
        loc = df["loc_id"].iloc[i] if "loc_id" in df.columns else ""
        shown.append(f"line {i + 2} ({loc or 'no loc_id'})")
    more = f" and {len(idx) - limit} more" if len(idx) > limit else ""
    return f"{len(idx)} row(s): " + ", ".join(shown) + more


def _numeric(series: pd.Series) -> np.ndarray:
    """Text to float64. Anything that is not a number becomes NaN, which fails later."""
    return pd.to_numeric(series.str.strip(), errors="coerce").to_numpy(dtype="float64")


# --- The validator -----------------------------------------------------------


def load_and_validate_exposure(
    path: str | Path, expected_sha256: str = EXPECTED_SHA256
) -> LoadedExposure:
    """Read the exposure file, apply V1 to V11, and return it or raise ValidationError."""
    path = Path(path)

    # Read the bytes once. The hash and the parsed table come from the same
    # bytes, so the file that was hashed is provably the file that was used.
    raw = path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()

    report = ValidationReport()

    def record(rule: str, ok: bool, detail: str = "") -> bool:
        action, description = RULES[rule]
        status = "pass" if ok else action
        report.results.append(RuleResult(rule, description, action, status, detail))
        return ok

    def not_run(rule: str, reason: str) -> None:
        action, description = RULES[rule]
        report.results.append(RuleResult(rule, description, action, "not_run", reason))

    def finish(df: pd.DataFrame | None, total_tiv: Decimal | None) -> LoadedExposure:
        report.results.sort(key=lambda r: int(r.rule[1:]))
        if not report.passed:
            raise ValidationError(report, path.name, sha256)
        return LoadedExposure(df, path.name, sha256, len(df), total_tiv, report)

    # Every cell is read as text. pandas is not allowed to guess types or to
    # turn blanks into NaN, so nothing is converted before it has been checked.
    try:
        text = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        record("V1", False, f"File could not be read as CSV: {exc}")
        for rule in list(RULES)[1:]:
            not_run(rule, "V1 failed")
        return finish(None, None)

    # V1: required columns. Without them no other rule can be evaluated.
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in text.columns]
    if not record("V1", not missing_cols, f"Missing: {', '.join(missing_cols)}" if missing_cols else ""):
        for rule in list(RULES)[1:]:
            not_run(rule, "V1 failed")
        return finish(None, None)

    # V2: no blank identifying, class, value or score field.
    blank = np.zeros(len(text), dtype=bool)
    blank_cols = []
    for col in NON_MISSING_COLUMNS:
        col_blank = (text[col].str.strip() == "").to_numpy()
        if col_blank.any():
            blank_cols.append(col)
        blank |= col_blank
    record("V2", not blank.any(), f"Blank in {', '.join(blank_cols)}; {_where(text, blank)}" if blank.any() else "")

    # V3: loc_id unique.
    dup = text["loc_id"].duplicated(keep=False).to_numpy()
    record("V3", not dup.any(), _where(text, dup) if dup.any() else "")

    # V4: housing class is one of the four. No default class is ever applied.
    bad_class = (~text["housing_class"].isin(HOUSING_CLASSES)).to_numpy()
    record("V4", not bad_class.any(), _where(text, bad_class) if bad_class.any() else "")

    # V5: TIV is a finite number above 0.
    tiv = _numeric(text["tiv_kes"])
    bad_tiv = ~(np.isfinite(tiv) & (tiv > 0))
    v5_ok = record("V5", not bad_tiv.any(), _where(text, bad_tiv) if bad_tiv.any() else "")

    # V6: every hazard score is a finite number in [0, 1].
    scores = np.column_stack([_numeric(text[c]) for c in SCORE_COLUMNS])
    bad_score = ~(np.isfinite(scores) & (scores >= 0) & (scores <= 1))
    bad_score_rows = bad_score.any(axis=1)
    v6_ok = record("V6", not bad_score_rows.any(), _where(text, bad_score_rows) if bad_score_rows.any() else "")

    # V7: tier ordering. Each adjacent pair passes when left >= right - 1e-9.
    if v6_ok:
        out_of_order = (scores[:, :-1] < scores[:, 1:] - TIER_TOLERANCE).any(axis=1)
        record("V7", not out_of_order.any(), _where(text, out_of_order) if out_of_order.any() else "")
    else:
        not_run("V7", "V6 failed, so scores cannot be compared")

    # V8 (warn): synthetic is True on every row.
    synthetic = text["synthetic"].str.strip().str.lower()
    not_synth = (synthetic != "true").to_numpy()
    record("V8", not not_synth.any(), _where(text, not_synth) if not_synth.any() else "")

    # V9 (warn): the file is byte-for-byte the supplied file.
    v9_ok = record("V9", sha256 == expected_sha256, "" if sha256 == expected_sha256 else f"Got {sha256}")

    # V10: only for the supplied file. Exact total, so it is summed in Decimal.
    total_tiv = sum((Decimal(v.strip()) for v in text["tiv_kes"]), Decimal(0)) if v5_ok else None
    if not v9_ok:
        not_run("V10", "V9 did not pass, so this is not the supplied file")
    elif not v5_ok:
        record("V10", False, f"{len(text)} rows; total TIV not computable because V5 failed")
    else:
        ok = len(text) == EXPECTED_ROWS and total_tiv == EXPECTED_TOTAL_TIV
        record("V10", ok, "" if ok else f"Got {len(text)} rows and total TIV {total_tiv:,}")

    # V11 (warn): coordinates inside the raster extent.
    lat, lon = _numeric(text["lat"]), _numeric(text["lon"])
    outside = ~(
        (lat >= LAT_RANGE[0]) & (lat <= LAT_RANGE[1]) & (lon >= LON_RANGE[0]) & (lon <= LON_RANGE[1])
    )
    record("V11", not outside.any(), _where(text, outside) if outside.any() else "")

    report.results.sort(key=lambda r: int(r.rule[1:]))
    if not report.passed:
        raise ValidationError(report, path.name, sha256)

    # Only now, with every fail rule passed, are typed columns produced.
    # Columns the calculation never uses are carried through exactly as read.
    data = text.copy()
    data["tiv_kes"] = tiv
    for i, col in enumerate(SCORE_COLUMNS):
        data[col] = scores[:, i]
    data["lat"], data["lon"] = lat, lon
    data["synthetic"] = synthetic.map({"true": True, "false": False}).astype("boolean")

    return LoadedExposure(data, path.name, sha256, len(data), total_tiv, report)
