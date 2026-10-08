"""Tests for the return-period mapping and EP / loss points (D-004; Checkpoint 6).

Expected values are the frozen D-004 mapping, AEP = 1 / T written out by hand,
and the frozen portfolio losses of specification 7.3. None is produced by the
code under test.
"""

import dataclasses
import inspect
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loss_engine.aggregation import aggregate  # noqa: E402
from loss_engine.config import default_config  # noqa: E402
from loss_engine.ep_curve import (  # noqa: E402
    EP_POINT_COLUMNS,
    EPCurveError,
    ReturnPeriodError,
    ReturnPeriodMapping,
    default_return_periods,
    ep_points,
)
from loss_engine.validation import load_and_validate_exposure  # noqa: E402

SUPPLIED = ROOT / "data" / "exposure_nairobi_with_hazard.csv"
MAPPING = default_return_periods()
SCENARIOS = ["reference", "low", "high", "reference_rcc80"]  # specification 2.3, in order
TIERS = ["extreme", "severe", "moderate", "occasional", "common"]  # D-003
D004_YEARS = [10, 25, 50, 100, 250]
D004_AEP = [0.100, 0.040, 0.020, 0.010, 0.004]
TOTAL_TOL = 1.0  # specification 7: KES 1 on totals

SPEC_7_3 = {
    "low": [266_230_031.74, 461_802_507.68, 1_086_205_322.49, 1_832_007_234.02, 2_848_531_482.88],
    "reference": [468_443_071.72, 826_175_003.83, 1_972_325_986.45, 3_279_344_346.85, 5_103_936_640.53],
    "high": [609_294_901.20, 1_103_866_847.82, 2_688_400_578.14, 4_491_451_558.67, 6_958_250_832.32],
    "reference_rcc80": [544_354_996.24, 964_742_195.00, 2_333_078_198.37, 3_887_799_934.29, 6_070_980_379.83],
}


@pytest.fixture(scope="module")
def tier_summary():
    return aggregate(default_config(), load_and_validate_exposure(SUPPLIED)).tier_summary


@pytest.fixture(scope="module")
def points(tier_summary):
    return ep_points(tier_summary, MAPPING)


def with_years(years):
    return dataclasses.replace(MAPPING, return_periods_years=years)


def with_tiers(tiers):
    return dataclasses.replace(MAPPING, tiers=tiers)


def failed_rules(**changes):
    with pytest.raises(ReturnPeriodError) as exc:
        dataclasses.replace(MAPPING, **changes)
    return [r.rule for r in exc.value.report.failures]


def hand_made_summary(losses_by_scenario):
    """A tier summary built by hand, with values chosen to be recognisable, not realistic."""
    rows = []
    for scenario_id, losses in losses_by_scenario.items():
        for i, (tier, loss) in enumerate(zip(TIERS, losses)):
            rows.append({
                "scenario_id": scenario_id, "tier": tier, "buildings": 3, "tiv_kes": 1_000.0,
                "affected_buildings": i + 1, "affected_tiv_kes": 100.0 * (i + 1),
                "portfolio_loss_kes": loss, "loss_pct_portfolio": 0.5 + i,  # deliberately not loss ÷ TIV
                "loss_pct_affected": np.nan, "avg_loss_per_affected_kes": np.nan,
            })
    return pd.DataFrame(rows)


# --- The D-004 mapping -------------------------------------------------------


def test_default_mapping_is_d004():
    assert MAPPING.tiers == tuple(TIERS)
    assert list(MAPPING.return_periods_years) == D004_YEARS


def test_default_mapping_is_labelled_provisional_and_an_assumption():
    assert MAPPING.status == "PROVISIONAL"
    assert MAPPING.tag == "[O, as a stated assumption of the reference dashboard]"
    assert "D-004" in MAPPING.source and "reference dashboard" in MAPPING.source
    assert "not confirmed" in MAPPING.source


def test_default_mapping_passes_every_rule():
    assert [(r.rule, r.status) for r in MAPPING.report.results] == [("RP1", "pass"), ("RP2", "pass"), ("RP3", "pass")]


def test_aep_is_one_over_the_return_period():
    assert MAPPING.annual_exceedance_probabilities == (1 / 10, 1 / 25, 1 / 50, 1 / 100, 1 / 250)
    assert list(MAPPING.annual_exceedance_probabilities) == D004_AEP


def test_mapping_id_is_stable_and_sensitive():
    assert default_return_periods().mapping_id == MAPPING.mapping_id
    assert MAPPING.mapping_id == "5756dc7ba9c1eee5e8036f3ebc93f32734d0ae288ca0d1a648b3dea7f26162b6"
    assert with_years((10, 25, 50, 100, 500)).mapping_id != MAPPING.mapping_id
    assert dataclasses.replace(MAPPING, status="CONFIRMED").mapping_id != MAPPING.mapping_id


def test_integer_and_float_years_give_the_same_mapping_id():
    assert with_years((10, 25, 50, 100, 250)).mapping_id == MAPPING.mapping_id


def test_a_replacement_mapping_needs_no_engine_change():
    """D-004: the mapping is configurable. A valid alternative is accepted and used as given."""
    alternative = with_years((5, 20, 50, 200, 1000))
    assert alternative.annual_exceedance_probabilities == (1 / 5, 1 / 20, 1 / 50, 1 / 200, 1 / 1000)


def test_mapping_cannot_be_changed_after_it_is_built():
    with pytest.raises(dataclasses.FrozenInstanceError):
        MAPPING.return_periods_years = (1, 2, 3, 4, 5)
    with pytest.raises(AttributeError):
        MAPPING.return_periods_years.append(500)


# --- Invalid mappings (RP1 to RP3) --------------------------------------------


@pytest.mark.parametrize("tiers", [
    ("extreme", "severe", "moderate", "occasional"),                    # missing
    ("extreme", "severe", "moderate", "occasional", "common", "rare"),  # extra
    ("extreme", "severe", "severe", "occasional", "common"),            # duplicated
    ("severe", "extreme", "moderate", "occasional", "common"),          # reordered
    ("common", "occasional", "moderate", "severe", "extreme"),          # reversed
])
def test_rp1_tier_list(tiers):
    assert "RP1" in failed_rules(tiers=tiers)


@pytest.mark.parametrize("years", [
    (0, 25, 50, 100, 250),
    (-10, 25, 50, 100, 250),
    (0.5, 25, 50, 100, 250),             # AEP would exceed 1
    (10, 25, float("nan"), 100, 250),
    (10, 25, 50, 100, float("inf")),
    (10, 25, 50, 100, "250"),
    (10, 25, 50, 100),                   # one tier without a return period
])
def test_rp2_invalid_return_periods(years):
    assert failed_rules(return_periods_years=years) == ["RP2"]


def test_rp2_failure_means_rp3_is_not_run():
    with pytest.raises(ReturnPeriodError) as exc:
        with_years((10, 25, float("nan"), 100, 250))
    assert exc.value.report.status_of("RP3") == "not_run"


@pytest.mark.parametrize("years", [
    (10, 10, 50, 100, 250),     # repeated
    (10, 50, 25, 100, 250),     # decreasing
    (250, 100, 50, 25, 10),     # reversed
])
def test_rp3_return_periods_must_strictly_increase(years):
    assert failed_rules(return_periods_years=years) == ["RP3"]


def test_invalid_mapping_is_not_sorted_into_shape():
    with pytest.raises(ReturnPeriodError, match="RP3.*moderate = 25"):
        with_years((10, 50, 25, 100, 250))


def test_a_return_period_of_exactly_1_year_is_allowed():
    assert with_years((1, 25, 50, 100, 250)).annual_exceedance_probabilities[0] == 1.0


# --- EP points on the supplied file ------------------------------------------


@pytest.mark.parametrize("tier_index", range(5))
def test_reference_ep_points(points, tier_index):
    """H = 4, RCC ceiling 0.65."""
    row = points[(points["scenario_id"] == "reference") & (points["tier"] == TIERS[tier_index])].iloc[0]
    assert row["return_period_years"] == D004_YEARS[tier_index]
    assert row["annual_exceedance_probability"] == D004_AEP[tier_index]
    assert row["portfolio_loss_kes"] == pytest.approx(SPEC_7_3["reference"][tier_index], abs=TOTAL_TOL)


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_every_scenario_has_its_five_frozen_points(points, scenario_id):
    block = points[points["scenario_id"] == scenario_id]
    assert block["tier"].tolist() == TIERS
    assert block["return_period_years"].tolist() == D004_YEARS
    assert block["annual_exceedance_probability"].tolist() == D004_AEP
    assert block["portfolio_loss_kes"].to_numpy() == pytest.approx(SPEC_7_3[scenario_id], abs=TOTAL_TOL)


def test_points_table_shape_columns_and_order(points):
    assert list(points.columns) == [
        "scenario_id", "tier", "return_period_years", "annual_exceedance_probability",
        "portfolio_loss_kes", "loss_pct_portfolio", "affected_buildings", "affected_tiv_kes",
    ]
    assert list(EP_POINT_COLUMNS) == list(points.columns)
    assert len(points) == 20
    assert list(zip(points["scenario_id"], points["tier"])) == [(s, t) for s in SCENARIOS for t in TIERS]


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_aep_falls_and_loss_rises_from_extreme_to_common(points, scenario_id):
    block = points[points["scenario_id"] == scenario_id]
    assert (np.diff(block["annual_exceedance_probability"]) < 0).all()
    assert (np.diff(block["return_period_years"]) > 0).all()
    assert (np.diff(block["portfolio_loss_kes"]) >= 0).all()


def test_points_carry_the_tier_summary_values_unchanged(points, tier_summary):
    for column in ["portfolio_loss_kes", "loss_pct_portfolio", "affected_buildings", "affected_tiv_kes"]:
        assert points[column].tolist() == tier_summary[column].tolist()


def test_affected_values_match_specification_7_4(points):
    block = points[points["scenario_id"] == "reference"]
    assert block["affected_buildings"].tolist() == [32, 51, 110, 174, 259]
    assert block["affected_tiv_kes"].tolist() == [1_659_425_000, 5_254_505_000, 11_978_060_000, 19_754_335_000, 31_394_010_000]


def test_there_are_exactly_five_points_per_scenario_and_nothing_in_between(points):
    assert points.groupby("scenario_id").size().tolist() == [5, 5, 5, 5]
    assert sorted(set(points["return_period_years"])) == D004_YEARS


# --- Consumes the tier summary only -------------------------------------------


def test_ep_points_needs_only_a_tier_summary_and_a_mapping():
    assert list(inspect.signature(ep_points).parameters) == ["tier_summary", "mapping"]


def test_values_are_read_not_recomputed():
    """loss_pct_portfolio is deliberately inconsistent with loss ÷ TIV here; it must pass through as given."""
    summary = hand_made_summary({"s": [1.0, 2.0, 3.0, 4.0, 5.0]})
    result = ep_points(summary, MAPPING)
    assert result["portfolio_loss_kes"].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert result["loss_pct_portfolio"].tolist() == [0.5, 1.5, 2.5, 3.5, 4.5]
    assert result["affected_buildings"].tolist() == [1, 2, 3, 4, 5]
    assert result["affected_tiv_kes"].tolist() == [100.0, 200.0, 300.0, 400.0, 500.0]


def test_rows_are_placed_by_tier_name_not_by_input_order():
    summary = hand_made_summary({"s": [1.0, 2.0, 3.0, 4.0, 5.0]}).iloc[::-1]
    result = ep_points(summary, MAPPING)
    assert result["tier"].tolist() == TIERS
    assert result["portfolio_loss_kes"].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_a_replacement_mapping_changes_only_the_probability_axis():
    summary = hand_made_summary({"s": [1.0, 2.0, 3.0, 4.0, 5.0]})
    result = ep_points(summary, with_years((5, 20, 50, 200, 1000)))
    assert result["return_period_years"].tolist() == [5, 20, 50, 200, 1000]
    assert result["portfolio_loss_kes"].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]


# --- Invalid tier summaries ----------------------------------------------------


def test_decreasing_loss_is_refused_citing_c5():
    summary = hand_made_summary({"s": [1.0, 3.0, 2.0, 4.0, 5.0]})
    with pytest.raises(EPCurveError, match="C5.*s: extreme = 1.0, severe = 3.0, moderate = 2.0"):
        ep_points(summary, MAPPING)


def test_decreasing_loss_in_the_real_summary_is_refused(tier_summary):
    broken = tier_summary.copy()
    broken.loc[(broken["scenario_id"] == "high") & (broken["tier"] == "common"), "portfolio_loss_kes"] = 1.0
    with pytest.raises(EPCurveError, match="C5.*high"):
        ep_points(broken, MAPPING)


def test_non_finite_loss_is_refused():
    with pytest.raises(EPCurveError, match="C5"):
        ep_points(hand_made_summary({"s": [1.0, 2.0, float("nan"), 4.0, 5.0]}), MAPPING)


def test_missing_tier_is_refused():
    summary = hand_made_summary({"s": [1.0, 2.0, 3.0, 4.0, 5.0]})
    with pytest.raises(EPCurveError, match="exactly once"):
        ep_points(summary[summary["tier"] != "moderate"], MAPPING)


def test_duplicated_tier_is_refused():
    summary = hand_made_summary({"s": [1.0, 2.0, 3.0, 4.0, 5.0]})
    with pytest.raises(EPCurveError, match="exactly once"):
        ep_points(pd.concat([summary, summary.iloc[[2]]], ignore_index=True), MAPPING)


def test_unknown_tier_is_refused():
    summary = hand_made_summary({"s": [1.0, 2.0, 3.0, 4.0, 5.0]})
    summary.loc[0, "tier"] = "rare"
    with pytest.raises(EPCurveError, match="exactly once"):
        ep_points(summary, MAPPING)


def test_missing_column_is_refused():
    summary = hand_made_summary({"s": [1.0, 2.0, 3.0, 4.0, 5.0]}).drop(columns="portfolio_loss_kes")
    with pytest.raises(EPCurveError, match="portfolio_loss_kes"):
        ep_points(summary, MAPPING)


def test_empty_summary_is_refused():
    with pytest.raises(EPCurveError, match="no rows"):
        ep_points(hand_made_summary({"s": [1.0] * 5}).iloc[0:0], MAPPING)


# --- Determinism and immutability --------------------------------------------


def test_ep_points_are_deterministic(points, tier_summary):
    pd.testing.assert_frame_equal(ep_points(tier_summary, default_return_periods()), points)


def test_input_summary_is_not_modified(tier_summary):
    before = tier_summary.copy()
    ep_points(tier_summary, MAPPING)
    pd.testing.assert_frame_equal(tier_summary, before)


def test_no_eal_pml_or_tvar_is_produced(points):
    """Deferred by design: five points do not constrain them."""
    import loss_engine.ep_curve as module
    names = " ".join(list(points.columns) + dir(module)).lower()
    assert not any(word in names for word in ["eal", "pml", "tvar", "interpol", "extrapol"])
