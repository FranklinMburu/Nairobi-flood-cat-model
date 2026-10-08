"""Tests for the run record and provenance (specification rev 2, sections 4 and 4.4; tests of 7.7).

Expected values are frozen figures (specification 2.1, 2.2, 2.3, 7.3; D-001;
D-004), values written out by hand, or independent recomputation from files
on disk. None is produced by the code under test.
"""

import dataclasses
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import loss_engine.run_record as run_record_module  # noqa: E402
from loss_engine.aggregation import OUTPUT_RULES, OutputCheckError, aggregate  # noqa: E402
from loss_engine.config import default_config  # noqa: E402
from loss_engine.ep_curve import default_return_periods, ep_points  # noqa: E402
from loss_engine.run_record import (  # noqa: E402
    MODEL_VERSION,
    OUTPUT_TABLES,
    RunRecord,
    read_run_record,
    run_model,
    table_digest,
    write_run,
)
from loss_engine.validation import RuleResult, ValidationReport, load_and_validate_exposure  # noqa: E402

SUPPLIED = ROOT / "data" / "exposure_nairobi_with_hazard.csv"
SUPPLIED_SHA256 = "b60aa96590d5a2e71509579d50c18d4ebd3557e26d2c60505f89d20bee69aa48"  # specification 2.1
CONFIG = default_config()
MAPPING = default_return_periods()
T1 = datetime(2026, 10, 8, 12, 15, 30, 123456, tzinfo=timezone.utc)
T2 = T1 + timedelta(minutes=5)
RUN_1 = "run-20261008T121530Z-3f9a1c2e"
EXPECTED_RULES = (
    [("input_validation", f"V{i}") for i in range(1, 12)]
    + [("configuration", f"P{i}") for i in range(1, 10)]
    + [("return_period_mapping", f"RP{i}") for i in range(1, 4)]
    + [("output_checks", f"C{i}") for i in range(1, 9)]
)


@pytest.fixture(scope="module")
def run():
    return run_model(SUPPLIED, CONFIG, MAPPING, run_id=RUN_1, now=T1)


@pytest.fixture(scope="module")
def second_run():
    return run_model(SUPPLIED, CONFIG, MAPPING, now=T2)


@pytest.fixture(scope="module")
def portfolio():
    return aggregate(CONFIG, load_and_validate_exposure(SUPPLIED))


def edited_copy(tmp_path, change):
    """A copy of the supplied file with one hand-made change, read and written as text."""
    data = pd.read_csv(SUPPLIED, dtype=str, keep_default_na=False)
    change(data)
    path = tmp_path / "exposure_edited.csv"
    data.to_csv(path, index=False)
    return path


def without(record_dict, *keys):
    return {k: v for k, v in record_dict.items() if k not in keys}


# --- Run identity --------------------------------------------------------------


def test_default_run_id_format_and_start_time(second_run):
    assert re.fullmatch(r"run-20261008T122030Z-[0-9a-f]{8}", second_run.record.run_id)


def test_run_ids_are_unique_per_execution():
    ids = {run_model(SUPPLIED, CONFIG, MAPPING, now=T1).record.run_id for _ in range(3)}
    assert len(ids) == 3


def test_run_id_is_not_derived_from_the_input(run, second_run):
    for record in (run.record, second_run.record):
        assert SUPPLIED_SHA256[:8] not in record.run_id


def test_injected_run_id_is_used(run):
    assert run.record.run_id == RUN_1


@pytest.mark.parametrize("bad", ["run-1", "20261008T121530Z-3f9a1c2e", "run-20261008T121530Z-3F9A1C2E", ""])
def test_malformed_injected_run_id_is_refused(bad):
    with pytest.raises(ValueError, match="run_id"):
        run_model(SUPPLIED, CONFIG, MAPPING, run_id=bad, now=T1)


def test_timestamp_is_the_injected_start_in_utc(run):
    assert run.record.timestamp == "2026-10-08T12:15:30.123456Z"


def test_a_non_utc_clock_is_converted_to_utc():
    nairobi = timezone(timedelta(hours=3))
    record = run_model(SUPPLIED, CONFIG, MAPPING, now=datetime(2026, 10, 8, 15, 15, 30, tzinfo=nairobi)).record
    assert record.timestamp == "2026-10-08T12:15:30.000000Z"
    assert record.run_id.startswith("run-20261008T121530Z-")


def test_a_naive_clock_is_refused():
    with pytest.raises(ValueError, match="timezone-aware"):
        run_model(SUPPLIED, CONFIG, MAPPING, now=datetime(2026, 10, 8, 12, 0, 0))


# --- Determinism (specification 7.7) ------------------------------------------


def test_two_runs_differ_only_in_run_id_and_timestamp(run, second_run):
    first, second = run.record.to_dict(), second_run.record.to_dict()
    assert first["run_id"] != second["run_id"] and first["timestamp"] != second["timestamp"]
    assert without(first, "run_id", "timestamp") == without(second, "run_id", "timestamp")


def test_two_runs_have_identical_identities_and_output_digests(run, second_run):
    for name in ["parameter_set_id", "mapping_id", "input_sha256", "model_version", "code_version", "output_digests"]:
        assert getattr(run.record, name) == getattr(second_run.record, name)
    assert set(run.record.output_digests) == set(OUTPUT_TABLES)


# --- Specification 4.4 fields --------------------------------------------------


def test_every_specification_4_4_field_is_present_and_non_empty(run):
    record = run.record
    for value in [
        record.run_id, record.model_version, record.code_version.package_version, record.timestamp,
        record.parameter_set_id, record.input_filename, record.input_sha256, record.row_count,
        record.total_tiv_kes, record.parameter_values, record.parameter_source_tags,
        record.scenario_definitions, record.validation_results, record.synthetic_proxy_statement,
    ]:
        assert value not in (None, "", (), [], {})
    assert record.status == "completed" and record.failed_at is None and record.failure is None


def test_input_identity_matches_the_supplied_file(run):
    assert run.record.input_filename == "exposure_nairobi_with_hazard.csv"
    assert run.record.input_sha256 == SUPPLIED_SHA256
    assert run.record.input_sha256 == hashlib.sha256(SUPPLIED.read_bytes()).hexdigest()
    assert run.record.row_count == 600
    assert run.record.total_tiv_kes == Decimal("63635075000")  # D-001, exact


def test_every_output_row_carries_the_run_id(run):
    for name in OUTPUT_TABLES:
        table = getattr(run.outputs, name)
        assert table.columns[0] == "run_id"
        assert (table["run_id"] == RUN_1).all()


def test_synthetic_proxy_statement(run):
    statement = run.record.synthetic_proxy_statement
    assert "synthetic" in statement and "proxy" in statement and "D-001" in statement and "D-002" in statement


def test_assumption_statements_cite_their_decisions(run):
    text = " ".join(run.record.assumption_statements)
    for citation in ["D-004", "D-005", "PROVISIONAL", "not a measured", "EAL, PML and TVaR are not computed"]:
        assert citation in text


# --- Versions ------------------------------------------------------------------


def test_model_version():
    assert MODEL_VERSION == "spec-07-rev2/D-001..D-005"


def test_frozen_documents_match_the_recorded_hashes(run):
    """If the specification or decision record changes, this fails until model_version is reconsidered."""
    spec = ROOT / "docs" / "specifications" / "07 - Deterministic Loss Engine Specification (Revision 2).md"
    decisions = ROOT / "docs" / "decisions" / "06 - Decision Record.md"
    assert hashlib.sha256(spec.read_bytes()).hexdigest() == run.record.specification_sha256
    assert hashlib.sha256(decisions.read_bytes()).hexdigest() == run.record.decision_record_sha256
    assert run.record.specification_sha256 == "8d69563a797fd09c290f3220e770f9a0dcc3714b4311f64a9096b0c45e046e1a"
    assert run.record.decision_record_sha256 == "b39ff2e93009415e05490959b9676ed03113df847fbfdb430f4ec6657c3a2184"


def test_code_version_matches_git(run):
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True)
    assert run.record.code_version.git_commit == head.stdout.strip()
    assert run.record.code_version.git_dirty == bool(status.stdout.strip())
    assert run.record.code_version.package_version == "0.1.0"


def test_code_version_is_null_not_invented_without_git(monkeypatch):
    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")
    monkeypatch.setattr(run_record_module.subprocess, "run", no_git)
    version = run_record_module.code_version()
    assert version.git_commit is None and version.git_dirty is None


def test_package_version_is_null_when_not_installed(monkeypatch):
    def missing(name):
        raise run_record_module.importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(run_record_module.importlib.metadata, "version", missing)
    assert run_record_module.code_version().package_version is None


# --- Parameters ------------------------------------------------------------------


def test_recorded_parameters_round_trip_to_the_parameter_set_id(run):
    canonical = json.dumps(run.record.to_dict()["parameters"], sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == run.record.parameter_set_id == CONFIG.parameter_set_id


def test_parameter_values(run):
    values = run.record.parameter_values
    assert values["tiers"] == ["extreme", "severe", "moderate", "occasional", "common"]
    assert values["curve"]["name"] == "JRC Africa residential (Huizinga et al. 2017)"
    assert values["curve"]["depths"] == [0, 0.5, 1, 1.5, 2, 3, 4, 5, 6]
    assert values["curve"]["damage_factors"] == [0, 0.220, 0.378, 0.531, 0.636, 0.817, 0.903, 0.957, 1.000]


def test_parameter_source_tags(run):
    tags = run.record.parameter_source_tags
    assert tags["tiers"] == "[O][D]" and tags["curve"] == "[S]"
    assert tags["scenarios"]["reference"]["h"] == "[A]"
    assert tags["scenarios"]["reference"]["ceilings"] == {
        "informal_iron_sheet": "[A]", "semi_permanent": "[A]", "permanent_masonry": "[A]",
        "concrete_rcc": "[A], informed by [S]",
    }
    assert tags["scenarios"]["reference_rcc80"]["ceilings"]["concrete_rcc"] == "[A]"


def test_scenario_definitions(run):
    definitions = run.record.scenario_definitions
    assert [(s["scenario_id"], s["label"], s["h"], s["ceilings"]["concrete_rcc"]) for s in definitions] == [
        ("reference", "Central/reference scenario", 4, 0.65),
        ("low", "Low sensitivity scenario", 2, 0.65),
        ("high", "High sensitivity scenario", 6, 0.65),
        ("reference_rcc80", "Reference scenario, RCC ceiling 0.80", 4, 0.80),
    ]
    assert definitions[0]["ceilings"] == {
        "informal_iron_sheet": 0.95, "semi_permanent": 0.90, "permanent_masonry": 0.80, "concrete_rcc": 0.65,
    }


def test_a_changed_parameter_changes_the_recorded_identity(run):
    scenarios = tuple(dataclasses.replace(s, h=5.0) if s.scenario_id == "high" else s for s in CONFIG.scenarios)
    record = run_model(SUPPLIED, dataclasses.replace(CONFIG, scenarios=scenarios), MAPPING, now=T1).record
    assert record.parameter_set_id != run.record.parameter_set_id
    assert [s["h"] for s in record.scenario_definitions] == [4, 2, 5, 4]
    assert record.output_digests["building_results"] != run.record.output_digests["building_results"]


# --- Return-period mapping ---------------------------------------------------------


def test_mapping_is_recorded_with_its_status(run):
    mapping = run.record.return_period_mapping
    assert list(mapping["tiers"]) == ["extreme", "severe", "moderate", "occasional", "common"]
    assert list(mapping["return_periods_years"]) == [10, 25, 50, 100, 250]
    assert mapping["status"] == "PROVISIONAL"
    assert mapping["tag"] == "[O, as a stated assumption of the reference dashboard]"
    assert "D-004" in mapping["source"]


def test_recorded_mapping_round_trips_to_the_mapping_id(run):
    canonical = json.dumps(run.record.to_dict()["return_period_mapping"], sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == run.record.mapping_id
    assert run.record.mapping_id == "5756dc7ba9c1eee5e8036f3ebc93f32734d0ae288ca0d1a648b3dea7f26162b6"


def test_a_changed_mapping_changes_the_recorded_mapping_id(run):
    other = dataclasses.replace(MAPPING, return_periods_years=(5, 20, 50, 200, 1000))
    record = run_model(SUPPLIED, CONFIG, other, now=T1).record
    assert record.mapping_id != run.record.mapping_id
    assert [p["return_period_years"] for p in record.ep_points[:5]] == [5, 20, 50, 200, 1000]


# --- Validation results -------------------------------------------------------------


def test_every_rule_is_recorded_in_order(run):
    assert [(v.stage, v.rule) for v in run.record.validation_results] == EXPECTED_RULES
    assert all(v.status == "pass" for v in run.record.validation_results)


def test_validation_entries_keep_action_and_description_detail(run):
    v5 = next(v for v in run.record.validation_results if v.rule == "V5")
    assert (v5.action, v5.status, v5.detail) == ("fail", "pass", "")


def test_a_parameter_warning_is_recorded():
    scenarios = tuple(dataclasses.replace(s, h=8.0) if s.scenario_id == "high" else s for s in CONFIG.scenarios)
    record = run_model(SUPPLIED, dataclasses.replace(CONFIG, scenarios=scenarios), MAPPING, now=T1).record
    p5 = next(v for v in record.validation_results if v.rule == "P5")
    assert p5.status == "warn" and "high" in p5.detail
    assert record.status == "completed"


def test_an_input_warning_is_recorded(tmp_path):
    def not_synthetic(data):
        data.loc[0, "synthetic"] = "False"
    record = run_model(edited_copy(tmp_path, not_synthetic), CONFIG, MAPPING, now=T1).record
    statuses = {v.rule: v.status for v in record.validation_results}
    assert statuses["V8"] == "warn" and statuses["V9"] == "warn" and statuses["V10"] == "not_run"
    assert record.status == "completed"


def test_a_changed_input_changes_the_recorded_input_hash(tmp_path, run):
    copy = tmp_path / SUPPLIED.name
    copy.write_bytes(SUPPLIED.read_bytes() + b"\n")
    record = run_model(copy, CONFIG, MAPPING, now=T1).record
    assert record.input_sha256 == hashlib.sha256(copy.read_bytes()).hexdigest() != SUPPLIED_SHA256
    assert record.output_digests == run.record.output_digests  # same rows, same results


# --- Failed runs (specification 4.4 and 7.7) --------------------------------------------

FAILURES = {
    "V3": lambda data: data.__setitem__("loc_id", data["loc_id"].where(data.index != 1, data.loc[0, "loc_id"])),
    "V5": lambda data: data.__setitem__("tiv_kes", data["tiv_kes"].where(data.index != 2, "0")),
    "V6": lambda data: data.__setitem__("hazard_score_common", data["hazard_score_common"].where(data.index != 3, "1.5")),
}


@pytest.mark.parametrize("rule", FAILURES)
def test_input_failure_still_writes_a_record_without_outputs(tmp_path, rule):
    path = edited_copy(tmp_path, FAILURES[rule])
    result = run_model(path, CONFIG, MAPPING, now=T1)
    record = result.record
    assert result.outputs is None
    assert record.status == "failed" and record.failed_at == "input_validation"
    assert rule in record.failure
    assert next(v for v in record.validation_results if v.rule == rule).status == "fail"
    assert record.input_filename == path.name
    assert record.input_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert record.row_count is None and record.total_tiv_kes is None
    assert record.output_digests == {} and record.tier_summary == () and record.ep_points == ()
    assert {v.stage for v in record.validation_results} == {"input_validation", "configuration", "return_period_mapping"}


def test_output_check_failure_still_writes_a_record(monkeypatch):
    def failing_aggregate(config, exposure):
        action, description = OUTPUT_RULES["C6"]
        report = ValidationReport([RuleResult("C6", description, action, "fail", "reference/extreme: off by KES 2")])
        raise OutputCheckError(report)
    monkeypatch.setattr(run_record_module, "aggregate", failing_aggregate)
    result = run_model(SUPPLIED, CONFIG, MAPPING, now=T1)
    assert result.outputs is None
    assert result.record.failed_at == "output_checks" and "C6" in result.record.failure
    assert result.record.validation_results[-1].rule == "C6"
    assert result.record.row_count == 600


def test_ep_point_c5_failure_records_the_failed_rule(monkeypatch):
    """The real C5 re-check in ep_points rejects a tier summary whose loss falls; the record names it."""
    def aggregate_with_falling_loss(config, exposure):
        portfolio = aggregate(config, exposure)
        tiers = portfolio.tier_summary.copy()
        tiers.loc[(tiers["scenario_id"] == "high") & (tiers["tier"] == "common"), "portfolio_loss_kes"] = 1.0
        return dataclasses.replace(portfolio, tier_summary=tiers)
    monkeypatch.setattr(run_record_module, "aggregate", aggregate_with_falling_loss)
    result = run_model(SUPPLIED, CONFIG, MAPPING, now=T1)
    record = result.record
    assert result.outputs is None
    assert record.status == "failed" and record.failed_at == "ep_points"
    assert record.output_digests == {} and record.tier_summary == () and record.ep_points == ()
    entry = record.validation_results[-1]
    assert (entry.stage, entry.rule, entry.action, entry.status) == ("ep_points", "C5", "fail", "fail")
    assert entry.detail == record.failure
    assert entry.detail.startswith("C5: For each scenario, portfolio loss does not decrease from extreme to common.")
    assert "high: extreme = " in entry.detail and "common = 1.0" in entry.detail
    assert [v for v in record.validation_results if v.status == "fail"] == [entry]


def test_failed_run_is_written_without_csv_files(tmp_path):
    result = run_model(edited_copy(tmp_path, FAILURES["V3"]), CONFIG, MAPPING, now=T1)
    write_run(result, tmp_path / "outputs")
    files = sorted(p.name for p in (tmp_path / "outputs" / result.record.run_id).iterdir())
    assert files == ["run_record.json"]
    assert read_run_record(tmp_path / "outputs" / result.record.run_id / "run_record.json") == result.record


# --- Outputs -------------------------------------------------------------------------


def test_output_table_sizes(run):
    assert [len(getattr(run.outputs, name)) for name in OUTPUT_TABLES] == [12_000, 20, 80, 20]


def test_output_tables_are_the_unchanged_checkpoint_5_and_6_results(run, portfolio):
    expected = {
        "building_results": portfolio.building_results,
        "tier_summary": portfolio.tier_summary,
        "class_summary": portfolio.class_summary,
        "ep_points": ep_points(portfolio.tier_summary, MAPPING),
    }
    for name, table in expected.items():
        pd.testing.assert_frame_equal(getattr(run.outputs, name).drop(columns="run_id"), table)


def test_embedded_tier_summary_is_checkpoint_5_and_matches_specification_7_3(run, portfolio):
    rows = run.record.tier_summary
    assert len(rows) == 20 and "run_id" not in rows[0]
    assert [r["portfolio_loss_kes"] for r in rows] == portfolio.tier_summary["portfolio_loss_kes"].tolist()
    reference = [r["portfolio_loss_kes"] for r in rows if r["scenario_id"] == "reference"]
    assert reference == pytest.approx(
        [468_443_071.72, 826_175_003.83, 1_972_325_986.45, 3_279_344_346.85, 5_103_936_640.53], abs=1.0)


def test_embedded_ep_points(run):
    reference = [p for p in run.record.ep_points if p["scenario_id"] == "reference"]
    assert [p["return_period_years"] for p in reference] == [10, 25, 50, 100, 250]
    assert [p["annual_exceedance_probability"] for p in reference] == [0.1, 0.04, 0.02, 0.01, 0.004]
    assert len(run.record.ep_points) == 20


def test_output_digests_are_the_digests_of_the_checkpoint_tables(run, portfolio):
    assert run.record.output_digests["tier_summary"] == table_digest(portfolio.tier_summary)
    assert run.record.output_digests["class_summary"] == table_digest(portfolio.class_summary)
    assert run.record.output_digests["building_results"] == table_digest(portfolio.building_results)


# --- Digests -------------------------------------------------------------------------


def test_digest_is_the_sha256_of_the_documented_canonical_csv():
    table = pd.DataFrame({"a": [1, 2], "b": [0.1, float("nan")]})
    assert table_digest(table) == hashlib.sha256(b"a,b\n1,0.1\n2,\n").hexdigest()


@pytest.mark.parametrize("change", ["value", "column order", "row order", "dropped column", "renamed column"])
def test_digest_changes_with_the_table(portfolio, change):
    table = portfolio.tier_summary
    changed = {
        "value": lambda t: t.assign(portfolio_loss_kes=t["portfolio_loss_kes"] + ([0.01] + [0.0] * (len(t) - 1))),
        "column order": lambda t: t[list(t.columns[::-1])],
        "row order": lambda t: t.iloc[::-1],
        "dropped column": lambda t: t.drop(columns="tiv_kes"),
        "renamed column": lambda t: t.rename(columns={"tier": "hazard_tier"}),
    }[change](table)
    assert table_digest(changed) != table_digest(table)


def test_digest_does_not_modify_the_table(portfolio):
    before = portfolio.tier_summary.copy()
    table_digest(portfolio.tier_summary)
    pd.testing.assert_frame_equal(portfolio.tier_summary, before)


# --- Persistence -------------------------------------------------------------------------


@pytest.fixture
def written(run, tmp_path):
    record_sha256 = write_run(run, tmp_path)
    return tmp_path / RUN_1, record_sha256


def test_write_run_creates_the_record_and_one_csv_per_table(written):
    directory, _ = written
    assert sorted(p.name for p in directory.iterdir()) == [
        "building_results.csv", "class_summary.csv", "ep_points.csv", "run_record.json", "tier_summary.csv",
    ]


def test_record_sha256_is_the_hash_of_the_written_file_and_is_not_inside_it(written, run):
    directory, record_sha256 = written
    data = (directory / "run_record.json").read_bytes()
    assert record_sha256 == hashlib.sha256(data).hexdigest()
    assert "record_sha256" not in json.loads(data)
    assert record_sha256.encode() not in data
    assert "record_sha256" not in [f.name for f in dataclasses.fields(RunRecord)]


def test_write_read_round_trip(written, run):
    directory, _ = written
    back = read_run_record(directory / "run_record.json")
    assert back == run.record
    assert back.to_json() == run.record.to_json() == (directory / "run_record.json").read_text(encoding="utf-8")


def test_json_is_deterministic_and_sorted(run):
    text = run.record.to_json()
    assert text == run.record.to_json()
    keys = list(json.loads(text).keys())
    assert keys == sorted(keys)


def test_exact_total_tiv_survives_serialisation(written):
    directory, _ = written
    text = (directory / "run_record.json").read_text(encoding="utf-8")
    # Checkpoint 1 sums the supplied text values ("5170000.0", ...) exactly, so the total keeps its ".0".
    assert '"total_tiv_kes": "63635075000.0"' in text
    back = read_run_record(directory / "run_record.json").total_tiv_kes
    assert back == Decimal("63635075000") and str(back) == "63635075000.0"


def test_json_is_utf8_and_keeps_non_ascii_text(written):
    directory, _ = written
    text = (directory / "run_record.json").read_bytes().decode("utf-8")
    assert "H × hazard score" in text
    assert "×" in read_run_record(directory / "run_record.json").assumption_statements[0]


def test_csv_files_carry_the_run_id_and_match_the_digests(written, run):
    directory, _ = written
    for name in OUTPUT_TABLES:
        lines = (directory / f"{name}.csv").read_text(encoding="utf-8").splitlines(keepends=True)
        assert lines[0].startswith("run_id,")
        assert all(line.startswith(f"{RUN_1},") for line in lines[1:])
        content = "".join(line.split(",", 1)[1] for line in lines).encode("utf-8")
        assert hashlib.sha256(content).hexdigest() == run.record.output_digests[name]


def test_an_existing_run_directory_is_never_overwritten(written, run):
    directory, _ = written
    with pytest.raises(FileExistsError):
        write_run(run, directory.parent)


# --- Immutability -------------------------------------------------------------------------


def test_record_cannot_be_changed(run):
    record = run.record
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.run_id = "run-20000101T000000Z-00000000"
    with pytest.raises(TypeError):
        record.parameters["curve"] = {}
    with pytest.raises(TypeError):
        record.tier_summary[0]["portfolio_loss_kes"] = 0.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.validation_results[0].status = "fail"


def test_running_changes_no_input(tmp_path):
    path = tmp_path / SUPPLIED.name
    path.write_bytes(SUPPLIED.read_bytes())
    config, mapping = default_config(), default_return_periods()
    run_model(path, config, mapping, now=T1)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == SUPPLIED_SHA256
    assert config == CONFIG and config.parameter_set_id == CONFIG.parameter_set_id
    assert mapping == MAPPING and mapping.mapping_id == MAPPING.mapping_id


def test_editing_an_output_table_afterwards_does_not_change_the_record():
    result = run_model(SUPPLIED, CONFIG, MAPPING, now=T1)
    before = result.record.to_json()
    result.outputs.tier_summary.loc[0, "portfolio_loss_kes"] = 0.0
    assert result.record.to_json() == before
    assert result.record.tier_summary[0]["portfolio_loss_kes"] == pytest.approx(468_443_071.72, abs=1.0)


# --- Readiness for a later evidence layer -------------------------------------------------


def test_record_holds_portfolio_evidence_but_no_building_rows(written):
    directory, _ = written
    data = json.loads((directory / "run_record.json").read_text(encoding="utf-8"))
    for key in ["tier_summary", "ep_points", "validation_results", "synthetic_proxy_statement",
                "assumption_statements", "parameters", "return_period_mapping", "output_digests"]:
        assert data[key]
    assert "loc_id" not in json.dumps(data)


def test_every_recorded_number_is_reachable_by_a_json_path(run):
    data = json.loads(run.record.to_json())
    assert data["ep_points"][4]["portfolio_loss_kes"] == pytest.approx(5_103_936_640.53, abs=1.0)
    assert data["tier_summary"][0]["affected_buildings"] == 32
    assert data["parameters"]["scenarios"][0]["h"] == 4.0
