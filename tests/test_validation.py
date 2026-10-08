"""Tests for the input validation layer (specification rev 2, rules V1 to V11)."""

import hashlib
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loss_engine.validation import (  # noqa: E402
    EXPECTED_SHA256,
    REQUIRED_COLUMNS,
    ValidationError,
    load_and_validate_exposure,
)

SUPPLIED = ROOT / "data" / "exposure_nairobi_with_hazard.csv"


def good_rows():
    """Three hand-made rows that satisfy every fail rule."""
    base = {
        "lat": "-1.30", "lon": "36.80", "floor_area_m2": "40", "cost_per_m2_kes": "10000",
        "synthetic": "True", "source": "hand-made test row",
    }
    rows = [
        {"loc_id": "T-1", "housing_class": "concrete_rcc", "tiv_kes": "1000000.0",
         "hazard_score_common": "0.6", "hazard_score_occasional": "0.5",
         "hazard_score_moderate": "0.4", "hazard_score_severe": "0.2", "hazard_score_extreme": "0.1"},
        {"loc_id": "T-2", "housing_class": "semi_permanent", "tiv_kes": "250000.0",
         "hazard_score_common": "0.1", "hazard_score_occasional": "0.0",
         "hazard_score_moderate": "0.0", "hazard_score_severe": "0.0", "hazard_score_extreme": "0.0"},
        {"loc_id": "T-3", "housing_class": "informal_iron_sheet", "tiv_kes": "50000.0",
         "hazard_score_common": "0.0", "hazard_score_occasional": "0.0",
         "hazard_score_moderate": "0.0", "hazard_score_severe": "0.0", "hazard_score_extreme": "0.0"},
    ]
    return [{**base, **r} for r in rows]


def write(tmp_path, rows, drop=None):
    df = pd.DataFrame(rows, columns=list(REQUIRED_COLUMNS))
    if drop:
        df = df.drop(columns=drop)
    path = tmp_path / "exposure.csv"
    df.to_csv(path, index=False)
    return path


def failed_rules(tmp_path, rows, drop=None):
    with pytest.raises(ValidationError) as exc:
        load_and_validate_exposure(write(tmp_path, rows, drop))
    return [r.rule for r in exc.value.report.failures]


def change(field, value, row=0):
    rows = good_rows()
    rows[row][field] = value
    return rows


# --- The supplied file -------------------------------------------------------


def test_supplied_file_passes_every_rule_with_no_warnings():
    result = load_and_validate_exposure(SUPPLIED)
    assert [r.status for r in result.report.results] == ["pass"] * 11
    assert result.sha256 == EXPECTED_SHA256
    assert result.row_count == 600
    assert result.total_tiv == Decimal("63635075000")


def test_input_untouched():
    """tiv_kes in the output equals the input on every row; nothing is corrected."""
    result = load_and_validate_exposure(SUPPLIED)
    original = pd.read_csv(SUPPLIED)
    assert (result.data["tiv_kes"].to_numpy() == original["tiv_kes"].to_numpy()).all()
    assert list(result.data["loc_id"]) == list(original["loc_id"])
    assert float(result.data["tiv_kes"].sum()) == 63635075000.0


def test_supplied_file_is_not_modified_on_disk():
    before = hashlib.sha256(SUPPLIED.read_bytes()).hexdigest()
    load_and_validate_exposure(SUPPLIED)
    assert hashlib.sha256(SUPPLIED.read_bytes()).hexdigest() == before


# --- Fail rules (specification 7.7) -----------------------------------------


def test_v1_missing_column(tmp_path):
    with pytest.raises(ValidationError) as exc:
        load_and_validate_exposure(write(tmp_path, good_rows(), drop=["tiv_kes"]))
    report = exc.value.report
    assert [r.rule for r in report.failures] == ["V1"]
    assert "tiv_kes" in report.failures[0].detail
    assert all(r.status == "not_run" for r in report.results[1:])


def test_v2_missing_hazard_score(tmp_path):
    assert "V2" in failed_rules(tmp_path, change("hazard_score_moderate", ""))


def test_v2_missing_loc_id(tmp_path):
    assert "V2" in failed_rules(tmp_path, change("loc_id", ""))


def test_v3_duplicate_loc_id(tmp_path):
    assert failed_rules(tmp_path, change("loc_id", "T-1", row=1)) == ["V3"]


def test_v4_unknown_class(tmp_path):
    assert failed_rules(tmp_path, change("housing_class", "timber")) == ["V4"]


@pytest.mark.parametrize("value", ["0", "-5000", "abc", "inf", "nan"])
def test_v5_invalid_tiv(tmp_path, value):
    assert "V5" in failed_rules(tmp_path, change("tiv_kes", value))


@pytest.mark.parametrize("value", ["1.2", "-0.1", "high", "nan"])
def test_v6_score_out_of_range(tmp_path, value):
    assert "V6" in failed_rules(tmp_path, change("hazard_score_common", value))


def test_v7_extreme_above_common(tmp_path):
    assert failed_rules(tmp_path, change("hazard_score_extreme", "0.9")) == ["V7"]


def test_v7_adjacent_pair_severe_above_moderate(tmp_path):
    assert failed_rules(tmp_path, change("hazard_score_severe", "0.45")) == ["V7"]


def test_v7_within_tolerance_passes(tmp_path):
    rows = change("hazard_score_occasional", "0.6000000005")  # above common by 5e-10
    result = load_and_validate_exposure(write(tmp_path, rows))
    assert result.report.status_of("V7") == "pass"


def test_v7_just_outside_tolerance_fails(tmp_path):
    assert failed_rules(tmp_path, change("hazard_score_occasional", "0.60000001")) == ["V7"]


# --- Warn rules --------------------------------------------------------------


def test_v8_not_synthetic_warns_and_run_completes(tmp_path):
    result = load_and_validate_exposure(write(tmp_path, change("synthetic", "False")))
    assert result.report.status_of("V8") == "warn"


def test_v9_different_file_warns_and_v10_is_skipped(tmp_path):
    result = load_and_validate_exposure(write(tmp_path, good_rows()))
    assert result.report.status_of("V9") == "warn"
    assert result.report.status_of("V10") == "not_run"
    assert result.row_count == 3 and result.total_tiv == Decimal("1300000.0")


def test_v9_modified_copy_of_supplied_file_warns(tmp_path):
    copy = tmp_path / SUPPLIED.name
    copy.write_bytes(SUPPLIED.read_bytes() + b"\n")
    result = load_and_validate_exposure(copy)
    assert result.report.status_of("V9") == "warn"
    assert result.report.status_of("V10") == "not_run"


def test_v10_fails_when_hash_matches_but_content_does_not(tmp_path):
    """V10 is tied to the hash: pretend the small file is the locked one."""
    path = write(tmp_path, good_rows())
    own_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValidationError) as exc:
        load_and_validate_exposure(path, expected_sha256=own_hash)
    assert [r.rule for r in exc.value.report.failures] == ["V10"]


def test_v11_coordinates_outside_extent_warn(tmp_path):
    result = load_and_validate_exposure(write(tmp_path, change("lat", "-4.05")))
    assert result.report.status_of("V11") == "warn"


# --- Behaviour ---------------------------------------------------------------


def test_all_failures_are_reported_together(tmp_path):
    rows = good_rows()
    rows[0]["housing_class"] = "timber"
    rows[1]["tiv_kes"] = "0"
    rows[2]["hazard_score_common"] = "1.2"
    assert failed_rules(tmp_path, rows) == ["V4", "V5", "V6"]


def test_error_message_names_rule_and_row(tmp_path):
    with pytest.raises(ValidationError) as exc:
        load_and_validate_exposure(write(tmp_path, change("housing_class", "timber")))
    assert "V4" in str(exc.value) and "line 2 (T-1)" in str(exc.value)


def test_extra_columns_are_carried_through(tmp_path):
    path = write(tmp_path, good_rows())
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df["broker"] = "X"
    df.to_csv(path, index=False)
    result = load_and_validate_exposure(path)
    assert list(result.data["broker"]) == ["X", "X", "X"]


def test_all_scores_zero_is_valid(tmp_path):
    rows = good_rows()
    for row in rows:
        for col in [c for c in row if c.startswith("hazard_score_")]:
            row[col] = "0.0"
    assert load_and_validate_exposure(write(tmp_path, rows)).report.passed
