"""Tests for portfolio aggregation and output checks (specification rev 2, sections 3.2, 4.2, 4.3 and 5.3).

Expected values are the frozen figures of specification 7.3 to 7.6, or are
worked by hand on small hand-made tables. None is produced by the code under test.
"""

import dataclasses
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loss_engine.aggregation import (  # noqa: E402
    CLASS_SUMMARY_COLUMNS,
    TIER_SUMMARY_COLUMNS,
    OutputCheckError,
    PortfolioResults,
    aggregate,
    check_outputs,
    class_summary,
    tier_summary,
)
from loss_engine.building_loss import building_results  # noqa: E402
from loss_engine.config import Ceiling, Scenario, default_config  # noqa: E402
from loss_engine.validation import load_and_validate_exposure  # noqa: E402

SUPPLIED = ROOT / "data" / "exposure_nairobi_with_hazard.csv"
CONFIG = default_config()
SCENARIOS = ["reference", "low", "high", "reference_rcc80"]  # specification 2.3, in order
TIERS = ["extreme", "severe", "moderate", "occasional", "common"]  # D-003
CLASSES = ["informal_iron_sheet", "semi_permanent", "permanent_masonry", "concrete_rcc"]
PORTFOLIO_TIV = 63_635_075_000  # D-001
TOTAL_TOL = 1.0  # specification 7: KES 1 on totals
PCT_4DP = 0.00005 / 100  # a percentage printed to 4 decimals, as a fraction
PCT_3DP = 0.0005 / 100  # a percentage printed to 3 decimals, as a fraction


@pytest.fixture(scope="module")
def exposure():
    return load_and_validate_exposure(SUPPLIED)


@pytest.fixture(scope="module")
def portfolio(exposure):
    return aggregate(CONFIG, exposure)


@pytest.fixture(scope="module")
def tiers(portfolio):
    return portfolio.tier_summary


@pytest.fixture(scope="module")
def classes(portfolio):
    return portfolio.class_summary


def tier_row(tiers, scenario_id, tier):
    match = tiers[(tiers["scenario_id"] == scenario_id) & (tiers["tier"] == tier)]
    assert len(match) == 1
    return match.iloc[0]


def class_row(classes, scenario_id, tier, housing_class):
    match = classes[(classes["scenario_id"] == scenario_id) & (classes["tier"] == tier)
                    & (classes["housing_class"] == housing_class)]
    assert len(match) == 1
    return match.iloc[0]


# --- Tier summary (specification 7.3 and 7.4) --------------------------------

SPEC_7_3 = {
    "low": [266_230_031.74, 461_802_507.68, 1_086_205_322.49, 1_832_007_234.02, 2_848_531_482.88],
    "reference": [468_443_071.72, 826_175_003.83, 1_972_325_986.45, 3_279_344_346.85, 5_103_936_640.53],
    "high": [609_294_901.20, 1_103_866_847.82, 2_688_400_578.14, 4_491_451_558.67, 6_958_250_832.32],
    "reference_rcc80": [544_354_996.24, 964_742_195.00, 2_333_078_198.37, 3_887_799_934.29, 6_070_980_379.83],
}


@pytest.mark.parametrize("scenario_id", SCENARIOS)
@pytest.mark.parametrize("tier_index", range(5))
def test_spec_7_3_portfolio_loss(tiers, scenario_id, tier_index):
    row = tier_row(tiers, scenario_id, TIERS[tier_index])
    assert row["portfolio_loss_kes"] == pytest.approx(SPEC_7_3[scenario_id][tier_index], abs=TOTAL_TOL)


SPEC_7_4 = [
    # tier, affected buildings, affected TIV, loss ÷ portfolio TIV %, loss ÷ affected TIV %, average per affected
    ("extreme", 32, 1_659_425_000, 0.7361, 28.2292, 14_638_845.99),
    ("severe", 51, 5_254_505_000, 1.2983, 15.7232, 16_199_509.88),
    ("moderate", 110, 11_978_060_000, 3.0994, 16.4662, 17_930_236.24),
    ("occasional", 174, 19_754_335_000, 5.1534, 16.6006, 18_846_806.59),
    ("common", 259, 31_394_010_000, 8.0206, 16.2577, 19_706_319.08),
]


@pytest.mark.parametrize("tier, flagged, flagged_tiv, pct_portfolio, pct_affected, average", SPEC_7_4)
def test_spec_7_4_reference_tier_summary(tiers, tier, flagged, flagged_tiv, pct_portfolio, pct_affected, average):
    row = tier_row(tiers, "reference", tier)
    assert row["affected_buildings"] == flagged
    assert row["affected_tiv_kes"] == flagged_tiv
    assert row["loss_pct_portfolio"] == pytest.approx(pct_portfolio / 100, abs=PCT_4DP)  # stored as a fraction
    assert row["loss_pct_affected"] == pytest.approx(pct_affected / 100, abs=PCT_4DP)
    assert row["avg_loss_per_affected_kes"] == pytest.approx(average, abs=0.01)


@pytest.mark.parametrize("scenario_id", SCENARIOS)
@pytest.mark.parametrize("tier, flagged, flagged_tiv", [r[:3] for r in SPEC_7_4])
def test_affected_counts_and_tiv_are_the_same_in_every_scenario(tiers, scenario_id, tier, flagged, flagged_tiv):
    """Specification 7.4: affected counts and affected TIV do not depend on the scenario."""
    row = tier_row(tiers, scenario_id, tier)
    assert row["affected_buildings"] == flagged and row["affected_tiv_kes"] == flagged_tiv


def test_every_tier_row_covers_the_whole_portfolio(tiers):
    """D-001: 600 buildings and KES 63,635,075,000, derived from the building rows."""
    assert (tiers["buildings"] == 600).all()
    assert (tiers["tiv_kes"] == PORTFOLIO_TIV).all()


def test_tier_summary_columns_rows_and_order(tiers):
    assert list(tiers.columns) == [
        "scenario_id", "tier", "buildings", "tiv_kes", "affected_buildings", "affected_tiv_kes",
        "portfolio_loss_kes", "loss_pct_portfolio", "loss_pct_affected", "avg_loss_per_affected_kes",
    ]
    assert list(TIER_SUMMARY_COLUMNS) == list(tiers.columns)
    assert list(zip(tiers["scenario_id"], tiers["tier"])) == [(s, t) for s in SCENARIOS for t in TIERS]


# --- Class summary (specification 7.5 and 7.6) -------------------------------

SPEC_7_5 = [
    # tier, class, buildings, class TIV, affected, affected TIV, loss, loss ÷ class TIV %, share of tier loss %
    ("common", "informal_iron_sheet", 179, 198_110_000, 74, 81_800_000, 20_602_488.80, 10.3995, 0.404),
    ("common", "semi_permanent", 181, 850_710_000, 74, 362_000_000, 81_540_194.91, 9.5850, 1.598),
    ("common", "permanent_masonry", 156, 8_451_170_000, 66, 3_525_445_000, 811_271_086.49, 9.5995, 15.895),
    ("common", "concrete_rcc", 84, 54_135_085_000, 45, 27_424_765_000, 4_190_522_870.32, 7.7409, 82.104),
    ("extreme", "informal_iron_sheet", 179, 198_110_000, 10, 10_235_000, 3_257_421.29, 1.6442, 0.695),
    ("extreme", "semi_permanent", 181, 850_710_000, 8, 32_095_000, 7_146_264.84, 0.8400, 1.526),
    ("extreme", "permanent_masonry", 156, 8_451_170_000, 11, 552_725_000, 129_087_712.68, 1.5275, 27.557),
    ("extreme", "concrete_rcc", 84, 54_135_085_000, 3, 1_064_370_000, 328_951_672.92, 0.6076, 70.222),
]


@pytest.mark.parametrize("tier, housing_class, buildings, class_tiv, flagged, flagged_tiv, loss, loss_ratio, share", SPEC_7_5)
def test_spec_7_5_reference_class_summary(classes, tier, housing_class, buildings, class_tiv, flagged, flagged_tiv,
                                          loss, loss_ratio, share):
    row = class_row(classes, "reference", tier, housing_class)
    assert row["buildings"] == buildings
    assert row["tiv_kes"] == class_tiv
    assert row["affected_buildings"] == flagged
    assert row["affected_tiv_kes"] == flagged_tiv
    assert row["loss_kes"] == pytest.approx(loss, abs=TOTAL_TOL)
    assert row["loss_ratio"] == pytest.approx(loss_ratio / 100, abs=PCT_4DP)
    assert row["share_of_tier_loss"] == pytest.approx(share / 100, abs=PCT_3DP)


SPEC_7_6 = {
    # mean damage ratio of affected buildings, reference scenario, extreme to common
    "informal_iron_sheet": [0.2963, 0.3420, 0.2375, 0.2372, 0.2519],
    "semi_permanent": [0.2405, 0.2242, 0.2256, 0.2329, 0.2320],
    "permanent_masonry": [0.2696, 0.3184, 0.2259, 0.2434, 0.2345],
    "concrete_rcc": [0.2532, 0.1621, 0.1860, 0.1620, 0.1636],
}


@pytest.mark.parametrize("housing_class", CLASSES)
@pytest.mark.parametrize("tier_index", range(5))
def test_spec_7_6_vulnerability_matrix(classes, housing_class, tier_index):
    row = class_row(classes, "reference", TIERS[tier_index], housing_class)
    assert row["mean_damage_ratio_affected"] == pytest.approx(SPEC_7_6[housing_class][tier_index], abs=1e-4)


def test_class_summary_columns_rows_and_order(classes):
    assert list(classes.columns) == [
        "scenario_id", "tier", "housing_class", "buildings", "tiv_kes", "affected_buildings", "affected_tiv_kes",
        "loss_kes", "loss_ratio", "share_of_tier_loss", "mean_damage_ratio_affected",
    ]
    assert list(CLASS_SUMMARY_COLUMNS) == list(classes.columns)
    assert list(zip(classes["scenario_id"], classes["tier"], classes["housing_class"])) == [
        (s, t, c) for s in SCENARIOS for t in TIERS for c in CLASSES
    ]


@pytest.mark.parametrize("scenario_id", SCENARIOS)
@pytest.mark.parametrize("tier_index", range(5))
def test_classes_reconcile_to_the_frozen_tier_total(classes, scenario_id, tier_index):
    block = classes[(classes["scenario_id"] == scenario_id) & (classes["tier"] == TIERS[tier_index])]
    assert block["buildings"].sum() == 600
    assert block["tiv_kes"].sum() == PORTFOLIO_TIV
    assert block["loss_kes"].sum() == pytest.approx(SPEC_7_3[scenario_id][tier_index], abs=TOTAL_TOL)
    assert block["share_of_tier_loss"].sum() == pytest.approx(1.0, abs=1e-12)


# --- Hand-made tables: worked arithmetic and empty fields --------------------


def hand_made_results():
    """One scenario, two tiers, three buildings. Tier 'dry' has no flagged building."""
    rows = []
    for tier, flags, ratios in [("wet", [True, False, True], [0.1, 0.0, 0.2]), ("dry", [False] * 3, [0.0] * 3)]:
        for loc_id, housing_class, tiv, flag, ratio in zip(
            ["B-1", "B-2", "B-3"], ["concrete_rcc", "concrete_rcc", "semi_permanent"], [1_000.0, 2_000.0, 3_000.0],
            flags, ratios,
        ):
            rows.append({
                "scenario_id": "s", "loc_id": loc_id, "housing_class": housing_class, "tiv_kes": tiv,
                "synthetic": True, "tier": tier, "hazard_score": 0.5 if flag else 0.0, "affected": flag,
                "curve_position": 2.0 if flag else 0.0, "damage_factor": ratio / 0.9, "ceiling": 0.9,
                "damage_ratio": ratio, "loss_kes": tiv * ratio,
            })
    return pd.DataFrame(rows)


def test_hand_worked_tier_summary():
    wet, dry = tier_summary(hand_made_results()).itertuples()
    # wet: losses 100, 0, 600; flagged B-1 and B-3, TIV 1,000 + 3,000.
    assert (wet.buildings, wet.tiv_kes, wet.affected_buildings, wet.affected_tiv_kes) == (3, 6_000.0, 2, 4_000.0)
    assert wet.portfolio_loss_kes == pytest.approx(700.0)
    assert wet.loss_pct_portfolio == pytest.approx(700 / 6_000)
    assert wet.loss_pct_affected == pytest.approx(700 / 4_000)
    assert wet.avg_loss_per_affected_kes == pytest.approx(350.0)


def test_tier_with_no_flagged_building_has_empty_affected_ratios_not_zero():
    """Specification 3.2 and 6: loss 0; the two ratios that divide by affected values are empty."""
    dry = tier_summary(hand_made_results()).iloc[1]
    assert dry["affected_buildings"] == 0 and dry["affected_tiv_kes"] == 0
    assert dry["portfolio_loss_kes"] == 0 and dry["loss_pct_portfolio"] == 0
    assert np.isnan(dry["loss_pct_affected"]) and np.isnan(dry["avg_loss_per_affected_kes"])


def test_hand_worked_class_summary():
    table = class_summary(hand_made_results()).query("tier == 'wet'").set_index("housing_class")
    assert list(table.index) == ["semi_permanent", "concrete_rcc"]  # configured class order
    rcc, semi = table.loc["concrete_rcc"], table.loc["semi_permanent"]
    assert (rcc["buildings"], rcc["tiv_kes"], rcc["affected_buildings"], rcc["affected_tiv_kes"]) == (2, 3_000.0, 1, 1_000.0)
    assert rcc["loss_kes"] == pytest.approx(100.0)
    assert rcc["loss_ratio"] == pytest.approx(100 / 3_000)
    assert rcc["share_of_tier_loss"] == pytest.approx(100 / 700)
    assert rcc["mean_damage_ratio_affected"] == pytest.approx(0.1)  # only B-1 is flagged
    assert semi["share_of_tier_loss"] == pytest.approx(600 / 700)
    assert semi["mean_damage_ratio_affected"] == pytest.approx(0.2)


def test_class_with_no_flagged_building_has_empty_mean_damage_ratio_not_zero():
    dry = class_summary(hand_made_results()).query("tier == 'dry'")
    assert (dry["loss_kes"] == 0).all() and (dry["loss_ratio"] == 0).all()
    assert dry["mean_damage_ratio_affected"].isna().all()
    assert dry["share_of_tier_loss"].isna().all()  # the tier loss is 0, so no share exists


# --- Output checks: the real run passes ----------------------------------------


def test_every_output_check_passes_on_the_supplied_file(portfolio):
    assert [(r.rule, r.status) for r in portfolio.output_checks.results] == [(f"C{i}", "pass") for i in range(1, 9)]


def test_aggregate_returns_the_checkpoint_4_table_unchanged(portfolio, exposure):
    pd.testing.assert_frame_equal(portfolio.building_results, building_results(CONFIG, exposure))
    assert isinstance(portfolio, PortfolioResults)


# --- Output checks: each rule fails when its condition is broken -------------


def failed_rules(exposure, results, tiers, classes, config=CONFIG):
    with pytest.raises(OutputCheckError) as exc:
        check_outputs(config, exposure, results, tiers, classes)
    return [r.rule for r in exc.value.report.failures], exc.value


def locate(results, scenario_id, tier, loc_id):
    return results.index[(results["scenario_id"] == scenario_id) & (results["tier"] == tier)
                         & (results["loc_id"] == loc_id)][0]


def shift_tier_loss(tiers, classes, scenario_id, tier, delta):
    """Move a tier's loss by delta in both summaries, so C6 still reconciles."""
    tiers, classes = tiers.copy(), classes.copy()
    tiers.loc[(tiers["scenario_id"] == scenario_id) & (tiers["tier"] == tier), "portfolio_loss_kes"] += delta
    first = classes.index[(classes["scenario_id"] == scenario_id) & (classes["tier"] == tier)][0]
    classes.loc[first, "loss_kes"] += delta
    return tiers, classes


def test_c1_damage_ratio_above_ceiling(exposure, portfolio):
    results = portfolio.building_results.copy()
    results.loc[locate(results, "reference", "common", "NBO-0002"), "damage_ratio"] = 0.95  # ceiling 0.90
    failed, error = failed_rules(exposure, results, portfolio.tier_summary, portfolio.class_summary)
    assert failed == ["C1"]
    assert "reference/common/NBO-0002" in str(error)


def test_c2_loss_above_ceiling_times_tiv(exposure, portfolio):
    results = portfolio.building_results.copy()
    results.loc[locate(results, "reference", "common", "NBO-0002"), "loss_kes"] = 0.91 * 3_300_000
    failed, _ = failed_rules(exposure, results, portfolio.tier_summary, portfolio.class_summary)
    assert failed == ["C2"]


def test_c3_loss_where_the_score_is_zero(exposure, portfolio):
    results = portfolio.building_results.copy()
    results.loc[locate(results, "reference", "common", "NBO-0001"), "loss_kes"] = 1.0  # dry in every tier
    failed, _ = failed_rules(exposure, results, portfolio.tier_summary, portfolio.class_summary)
    assert failed == ["C3"]


def test_c3_no_loss_where_the_score_is_positive(exposure, portfolio):
    results = portfolio.building_results.copy()
    results.loc[locate(results, "reference", "extreme", "NBO-0002"), "loss_kes"] = 0.0  # score 0.1346
    failed, error = failed_rules(exposure, results, portfolio.tier_summary, portfolio.class_summary)
    assert failed == ["C3"]
    assert "reference/extreme/NBO-0002" in str(error)


def test_c4_building_loss_falls_from_extreme_to_severe(exposure, portfolio):
    results = portfolio.building_results.copy()
    results.loc[locate(results, "reference", "extreme", "NBO-0002"), "loss_kes"] = 1_785_150.55  # its common loss
    failed, error = failed_rules(exposure, results, portfolio.tier_summary, portfolio.class_summary)
    assert failed == ["C4"]
    assert "reference/NBO-0002" in str(error)


def test_c5_portfolio_loss_falls_between_tiers(exposure, portfolio):
    # high: extreme 609.3m becomes 1,109.3m, above severe 1,103.9m; still above reference, so C8 holds.
    tiers, classes = shift_tier_loss(portfolio.tier_summary, portfolio.class_summary, "high", "extreme", 500_000_000)
    failed, error = failed_rules(exposure, portfolio.building_results, tiers, classes)
    assert failed == ["C5"]
    assert "high: extreme = " in str(error)


def test_c6_class_losses_do_not_reconcile(exposure, portfolio):
    classes = portfolio.class_summary.copy()
    classes.loc[0, "loss_kes"] += 2.0
    failed, error = failed_rules(exposure, portfolio.building_results, portfolio.tier_summary, classes)
    assert failed == ["C6"]
    assert "reference/extreme" in str(error)


def test_c6_tolerance_is_kes_1(exposure, portfolio):
    classes = portfolio.class_summary.copy()
    classes.loc[0, "loss_kes"] += 0.5
    report = check_outputs(CONFIG, exposure, portfolio.building_results, portfolio.tier_summary, classes)
    assert report.status_of("C6") == "pass"


def test_c7_missing_building_row(exposure, portfolio):
    results = portfolio.building_results.drop(index=locate(portfolio.building_results, "high", "moderate", "NBO-0005"))
    with pytest.raises(OutputCheckError) as exc:
        check_outputs(CONFIG, exposure, results, portfolio.tier_summary, portfolio.class_summary)
    report = exc.value.report
    assert [r.rule for r in report.failures] == ["C7"]
    assert "high/moderate: missing ['NBO-0005']" in str(exc.value)
    assert report.status_of("C4") == "not_run"


def test_c7_duplicated_building_row(exposure, portfolio):
    results = portfolio.building_results
    duplicate = results.loc[[locate(results, "low", "severe", "NBO-0000")]]
    failed, error = failed_rules(exposure, pd.concat([results, duplicate], ignore_index=True),
                                 portfolio.tier_summary, portfolio.class_summary)
    assert failed == ["C7"]
    assert "low/severe: duplicated ['NBO-0000']" in str(error)


def test_c7_duplicate_and_missing_rows_that_keep_the_total_are_still_caught(exposure, portfolio):
    results = portfolio.building_results.copy()
    results.loc[locate(results, "low", "severe", "NBO-0001"), "loc_id"] = "NBO-0000"
    failed, error = failed_rules(exposure, results, portfolio.tier_summary, portfolio.class_summary)
    assert len(results) == 12_000
    assert failed == ["C7"] and "missing ['NBO-0001']" in str(error)


def test_c8_scenario_ordering(exposure, portfolio):
    tiers, classes = shift_tier_loss(portfolio.tier_summary, portfolio.class_summary, "high", "extreme", -200_000_000)
    failed, error = failed_rules(exposure, portfolio.building_results, tiers, classes)
    assert failed == ["C8"]
    assert "extreme" in str(error)


def test_c8_rcc80_below_reference(exposure, portfolio):
    # reference_rcc80 extreme 544.4m becomes 444.4m, below reference 468.4m; still below its own severe tier.
    tiers, classes = shift_tier_loss(portfolio.tier_summary, portfolio.class_summary, "reference_rcc80", "extreme", -100_000_000)
    failed, error = failed_rules(exposure, portfolio.building_results, tiers, classes)
    assert failed == ["C8"]
    assert "extreme: low = " in str(error)


def test_c8_is_not_run_without_the_four_default_scenarios(exposure):
    ceilings = {**CONFIG.scenarios[0].ceilings, "concrete_rcc": Ceiling(0.80, "[A]")}
    config = dataclasses.replace(CONFIG, scenarios=(Scenario("combination", "Test combination", 2.0, "[A]", ceilings),))
    portfolio = aggregate(config, exposure)
    assert portfolio.output_checks.status_of("C8") == "not_run"
    assert portfolio.output_checks.passed


def test_error_message_names_the_rule_and_the_observed_values(exposure, portfolio):
    results = portfolio.building_results.copy()
    results.loc[locate(results, "reference", "common", "NBO-0002"), "damage_ratio"] = 0.95
    _, error = failed_rules(exposure, results, portfolio.tier_summary, portfolio.class_summary)
    assert "C1: Every damage ratio is between 0 and the building's ceiling" in str(error)
    assert "damage_ratio = 0.95" in str(error) and "ceiling = 0.9" in str(error)


# --- Determinism and immutability --------------------------------------------


def test_aggregation_is_deterministic(exposure, portfolio):
    again = aggregate(CONFIG, exposure)
    pd.testing.assert_frame_equal(again.tier_summary, portfolio.tier_summary)
    pd.testing.assert_frame_equal(again.class_summary, portfolio.class_summary)


def test_summaries_and_checks_leave_the_building_results_unchanged(exposure, portfolio):
    results = portfolio.building_results
    before = results.copy()
    tiers, classes = tier_summary(results), class_summary(results)
    check_outputs(CONFIG, exposure, results, tiers, classes)
    pd.testing.assert_frame_equal(results, before)


def test_no_rounding_before_or_after_aggregation(tiers):
    assert (tiers["portfolio_loss_kes"] != tiers["portfolio_loss_kes"].round(2)).any()
    assert tiers["loss_pct_portfolio"].dtype == np.float64
