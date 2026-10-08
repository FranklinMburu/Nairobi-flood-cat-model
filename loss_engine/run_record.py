"""Run record and provenance for the deterministic loss engine.

Implements the run record of section 4.4 and the `run_id` on every output
table (section 4) of
`docs/specifications/07 - Deterministic Loss Engine Specification (Revision 2).md`.

`run_model` executes the existing chain (Checkpoints 1 to 6) unchanged and
records what produced the result: the input file and its hash, the model and
code versions, every parameter with its source tag, the return-period mapping,
every validation result and a digest of every output table. A run stopped by a
failed rule still returns its record, with the failing rule and no loss outputs.

Boundary: ModelConfig and ReturnPeriodMapping check themselves (P1 to P9, RP1
to RP3) when they are built, so an invalid configuration or mapping never
reaches `run_model`. Their results are recorded in every run that starts.

Identity:
- `run_id` and `timestamp` identify one execution and differ on every run
  (specification 7.7). They are not part of any deterministic hash.
- What a run computed is identified by input_sha256, parameter_set_id,
  mapping_id, model_version and code_version. Two runs that share them have
  identical output digests.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import re
import secrets
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pandas as pd

from .aggregation import OutputCheckError, aggregate
from .config import ModelConfig
from .ep_curve import EPCurveError, ReturnPeriodMapping, ep_points
from .validation import ValidationError, ValidationReport, load_and_validate_exposure

RECORD_FORMAT = "run-record-1"

# The methodology this code implements, and the frozen documents that define it.
MODEL_VERSION = "spec-07-rev2/D-001..D-005"
SPECIFICATION_PATH = "docs/specifications/07 - Deterministic Loss Engine Specification (Revision 2).md"
SPECIFICATION_SHA256 = "8d69563a797fd09c290f3220e770f9a0dcc3714b4311f64a9096b0c45e046e1a"
DECISION_RECORD_PATH = "docs/decisions/06 - Decision Record.md"
DECISION_RECORD_SHA256 = "b39ff2e93009415e05490959b9676ed03113df847fbfdb430f4ec6657c3a2184"

PACKAGE_NAME = "nairobi-flood-cat-model"

SYNTHETIC_PROXY_STATEMENT = (
    "The portfolio is synthetic: the buildings and their tiv_kes values were supplied by the hackathon "
    "organizers and are used exactly as supplied (D-001). The hazard is a proxy: the five tiers are cuts "
    "through one relative susceptibility score, not flood depths, probabilities or return periods (D-002)."
)

ASSUMPTION_STATEMENTS = (
    "curve_position = H × hazard score is a scenario-derived severity coordinate on the JRC curve's axis, "
    "not a measured, observed or modelled flood depth for Nairobi (specification 3.3; D-005).",
    "H and the structural damage ceilings are team assumptions, not Kenya-calibrated values; "
    "one JRC Africa residential curve is used for every housing class (D-005).",
    "The return periods are PROVISIONAL assumptions from the organizers' reference dashboard, "
    "not observed event frequencies (D-004).",
    "Losses are structure-only ground-up losses: no contents, business interruption, deductibles, "
    "limits or reinsurance (specification 1).",
    "The EP / loss points are five deterministic scenario points, not a calibrated or stochastic EP curve; "
    "EAL, PML and TVaR are not computed.",
)

OUTPUT_TABLES = ("building_results", "tier_summary", "class_summary", "ep_points")

RUN_ID_PATTERN = re.compile(r"^run-\d{8}T\d{6}Z-[0-9a-f]{8}$")


# --- Small helpers -------------------------------------------------------------


def _freeze(value):
    """Nested dicts and lists as read-only mappings and tuples."""
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value):
    """The reverse of _freeze, for writing JSON."""
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


def _plain(value):
    """A table cell as a JSON value: numpy scalars as Python ones, an empty (NaN) cell as null."""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _rows(table: pd.DataFrame) -> tuple:
    return tuple(
        {column: _plain(value) for column, value in zip(table.columns, row)}
        for row in table.itertuples(index=False, name=None)
    )


def table_digest(table: pd.DataFrame) -> str:
    """SHA-256 of a table's canonical CSV text.

    Canonical CSV: pandas `to_csv` with no index, a header row, "," between
    fields, "\\n" line endings and UTF-8. Floats are written in Python's
    shortest exact form, an empty cell as nothing. The digest changes with any
    value, column, column order or row order. The table is not modified.

    run_model applies it to the deterministic Checkpoint 4 to 6 calculation
    tables before `run_id` is added, so `run_id` and `timestamp` are never
    hashed. A saved CSV carries `run_id` as its first column; removing that
    column and hashing the rest reproduces the recorded digest.
    """
    return hashlib.sha256(table.to_csv(index=False, lineterminator="\n").encode("utf-8")).hexdigest()


def _utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)


def new_run_id(started: datetime) -> str:
    """run-<UTC start, to the second>-<8 random hex characters>. Unique per execution, by design."""
    return f"run-{started:%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"


# --- Versions ------------------------------------------------------------------


@dataclass(frozen=True)
class CodeVersion:
    """The implementation that ran. A value Git cannot supply is None, never guessed."""

    package_version: str | None
    git_commit: str | None
    git_dirty: bool | None


def code_version() -> CodeVersion:
    try:
        package_version = importlib.metadata.version(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        package_version = None

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", *args], cwd=Path(__file__).resolve().parent,
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain")
    return CodeVersion(
        package_version=package_version,
        git_commit=commit.strip() if commit else None,
        git_dirty=None if commit is None or status is None else bool(status.strip()),
    )


# --- The record ------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationEntry:
    """One rule's outcome. stage is input_validation, configuration, return_period_mapping, output_checks or ep_points."""

    stage: str
    rule: str
    action: str
    status: str
    detail: str


@dataclass(frozen=True)
class RunRecord:
    """The provenance of one execution (specification 4.4). Read-only.

    The parameters are held once, as the parsed canonical form of ModelConfig,
    which is exactly what parameter_set_id is the hash of. The 4.4 views
    `parameter_values`, `parameter_source_tags` and `scenario_definitions` are
    read from it, not stored separately.
    """

    record_format: str
    run_id: str
    timestamp: str
    status: str
    failed_at: str | None
    failure: str | None
    model_version: str
    specification_sha256: str
    decision_record_sha256: str
    code_version: CodeVersion
    input_filename: str
    input_sha256: str
    row_count: int | None
    total_tiv_kes: Decimal | None
    parameter_set_id: str
    parameters: Mapping
    mapping_id: str
    return_period_mapping: Mapping
    validation_results: tuple[ValidationEntry, ...]
    synthetic_proxy_statement: str
    assumption_statements: tuple[str, ...]
    output_digests: Mapping
    tier_summary: tuple
    ep_points: tuple

    def __post_init__(self):
        for name in ("parameters", "return_period_mapping", "output_digests", "tier_summary", "ep_points",
                     "assumption_statements"):
            object.__setattr__(self, name, _freeze(getattr(self, name)))
        object.__setattr__(self, "validation_results", tuple(self.validation_results))

    # Specification 4.4 views of the one parameter block.

    @property
    def parameter_values(self) -> dict:
        p = self.parameters
        return {
            "tiers": list(p["tiers"]["values"]),
            "curve": {k: _thaw(p["curve"][k]) for k in ("name", "depths", "damage_factors")},
        }

    @property
    def parameter_source_tags(self) -> dict:
        p = self.parameters
        return {
            "tiers": p["tiers"]["tag"],
            "curve": p["curve"]["tag"],
            "scenarios": {
                s["scenario_id"]: {"h": s["h_tag"], "ceilings": {c: v["tag"] for c, v in s["ceilings"].items()}}
                for s in p["scenarios"]
            },
        }

    @property
    def scenario_definitions(self) -> list:
        return [
            {"scenario_id": s["scenario_id"], "label": s["label"], "h": s["h"],
             "ceilings": {c: v["value"] for c, v in s["ceilings"].items()}}
            for s in self.parameters["scenarios"]
        ]

    # Serialisation.

    def to_dict(self) -> dict:
        data = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "code_version":
                value = {"package_version": value.package_version, "git_commit": value.git_commit,
                         "git_dirty": value.git_dirty}
            elif f.name == "total_tiv_kes":
                value = None if value is None else str(value)  # exact, never a float
            elif f.name == "validation_results":
                value = [{"stage": v.stage, "rule": v.rule, "action": v.action, "status": v.status,
                          "detail": v.detail} for v in value]
            else:
                value = _thaw(value)
            data[f.name] = value
        return data

    def to_json(self) -> str:
        """Sorted keys, two-space indent, UTF-8 text; no NaN. The same record always gives the same text."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"

    @classmethod
    def from_dict(cls, data: dict) -> "RunRecord":
        values = dict(data)
        values["code_version"] = CodeVersion(**data["code_version"])
        values["total_tiv_kes"] = None if data["total_tiv_kes"] is None else Decimal(data["total_tiv_kes"])
        values["validation_results"] = tuple(ValidationEntry(**v) for v in data["validation_results"])
        return cls(**values)


@dataclass(frozen=True, eq=False)
class RunOutputs:
    """The four output tables of a completed run, each a copy with `run_id` as its first column."""

    building_results: pd.DataFrame
    tier_summary: pd.DataFrame
    class_summary: pd.DataFrame
    ep_points: pd.DataFrame


@dataclass(frozen=True, eq=False)
class RunResult:
    """A run record, and the outputs if the run completed (None if it stopped on a failed rule)."""

    record: RunRecord
    outputs: RunOutputs | None


# --- Running -------------------------------------------------------------------------


def _entries(stage: str, report: ValidationReport) -> list[ValidationEntry]:
    return [ValidationEntry(stage, r.rule, r.action, r.status, r.detail) for r in report.results]


def _with_run_id(table: pd.DataFrame, run_id: str) -> pd.DataFrame:
    copy = table.copy()
    copy.insert(0, "run_id", run_id)
    return copy


def run_model(
    exposure_path: str | Path,
    config: ModelConfig,
    mapping: ReturnPeriodMapping,
    *,
    run_id: str | None = None,
    now: datetime | None = None,
) -> RunResult:
    """Run Checkpoints 1 to 6 on one exposure file and record the run.

    `run_id` and `now` may be supplied for tests; otherwise a new run_id is
    made and the clock is read. A failed rule (V1 to V11, C1 to C8, or the C5
    re-check before the EP points) stops the run: the result has a record with
    status "failed" and no outputs. Nothing after the failure is computed.
    """
    started = _utc(now)
    if run_id is None:
        run_id = new_run_id(started)
    elif not RUN_ID_PATTERN.match(run_id):
        raise ValueError(f"run_id {run_id!r} does not match run-<YYYYMMDDTHHMMSSZ>-<8 hex characters>")

    record = dict(
        record_format=RECORD_FORMAT,
        run_id=run_id,
        timestamp=started.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        model_version=MODEL_VERSION,
        specification_sha256=SPECIFICATION_SHA256,
        decision_record_sha256=DECISION_RECORD_SHA256,
        code_version=code_version(),
        parameter_set_id=config.parameter_set_id,
        parameters=json.loads(config.canonical_json()),
        mapping_id=mapping.mapping_id,
        return_period_mapping=json.loads(mapping.canonical_json()),
        synthetic_proxy_statement=SYNTHETIC_PROXY_STATEMENT,
        assumption_statements=ASSUMPTION_STATEMENTS,
    )
    parameter_checks = _entries("configuration", config.report) + _entries("return_period_mapping", mapping.report)

    def stopped(stage: str, error: Exception, validation: list, **input_facts) -> RunResult:
        return RunResult(RunRecord(
            **record, **input_facts, status="failed", failed_at=stage, failure=str(error),
            validation_results=validation, output_digests={}, tier_summary=(), ep_points=(),
        ), None)

    # Checkpoint 1: the input file. A failure still identifies the file that was read.
    try:
        exposure = load_and_validate_exposure(exposure_path)
    except ValidationError as error:
        return stopped("input_validation", error, _entries("input_validation", error.report) + parameter_checks,
                       input_filename=error.filename, input_sha256=error.sha256, row_count=None, total_tiv_kes=None)

    input_facts = dict(input_filename=exposure.filename, input_sha256=exposure.sha256,
                       row_count=exposure.row_count, total_tiv_kes=exposure.total_tiv)
    validation = _entries("input_validation", exposure.report) + parameter_checks

    # Checkpoints 3 to 5, with output checks C1 to C8.
    try:
        portfolio = aggregate(config, exposure)
    except OutputCheckError as error:
        return stopped("output_checks", error, validation + _entries("output_checks", error.report), **input_facts)
    validation += _entries("output_checks", portfolio.output_checks)

    # Checkpoint 6.
    try:
        points = ep_points(portfolio.tier_summary, mapping)
    except EPCurveError as error:
        if str(error).startswith("C5:"):  # the C5 re-check in ep_points; record the failed rule itself
            validation.append(ValidationEntry("ep_points", "C5", "fail", "fail", str(error)))
        return stopped("ep_points", error, validation, **input_facts)

    tables = {
        "building_results": portfolio.building_results,
        "tier_summary": portfolio.tier_summary,
        "class_summary": portfolio.class_summary,
        "ep_points": points,
    }
    run_record = RunRecord(
        **record, **input_facts, status="completed", failed_at=None, failure=None,
        validation_results=validation,
        output_digests={name: table_digest(table) for name, table in tables.items()},
        tier_summary=_rows(portfolio.tier_summary),
        ep_points=_rows(points),
    )
    outputs = RunOutputs(**{name: _with_run_id(table, run_id) for name, table in tables.items()})
    return RunResult(run_record, outputs)


# --- Persistence ---------------------------------------------------------------------


def write_run(result: RunResult, directory: str | Path = "outputs") -> str:
    """Write `<directory>/<run_id>/run_record.json` and, for a completed run, one CSV per output table.

    Returns record_sha256, the SHA-256 of run_record.json as written. It is
    computed after the file is written and is not stored inside it. An
    existing run directory is never overwritten.
    """
    run_dir = Path(directory) / result.record.run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    record_path = run_dir / "run_record.json"
    record_path.write_bytes(result.record.to_json().encode("utf-8"))
    if result.outputs is not None:
        for name in OUTPUT_TABLES:
            text = getattr(result.outputs, name).to_csv(index=False, lineterminator="\n")
            (run_dir / f"{name}.csv").write_bytes(text.encode("utf-8"))
    return hashlib.sha256(record_path.read_bytes()).hexdigest()


def read_run_record(path: str | Path) -> RunRecord:
    """Read a run_record.json back into a RunRecord, without changing any value."""
    return RunRecord.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
