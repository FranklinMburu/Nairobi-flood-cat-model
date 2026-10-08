"""Tests for building financial loss (specification rev 2, section 3.1 step 7 and section 4.1).

Benchmarks are the frozen values of specification 7.2 to 7.5 and 7.7. Totals
are summed inside the tests only, to compare building losses with the frozen
figures; the package itself performs no aggregation.
"""

import dataclasses
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loss_engine.building_loss import (  # noqa: E402
    BUILDING_RESULT_COLUMNS,
    BuildingLossResult,
    building_loss,
    building_results,
)
from loss_engine.config import Ceiling, Scenario, default_config  # noqa: E402
from loss_engine.validation import load_and_validate_exposure  # noqa: E402
from loss_engine.vulnerability import VulnerabilityResult, assess  # noqa: E402

SUPPLIED = ROOT / "data" / "exposure_nairobi_with_hazard.csv"
CONFIG = default_config()
TIERS = ["extreme", "severe", "moderate", "occasional", "common"]  # D-003
BUILDING_TOL = 0.01  # specification 7: KES 0.01 on building losses
TOTAL_TOL = 1.0      # specification 7: KES 1 on totals
PORTFOLIO_TIV = 63_635_075_000  # D-001


def hand_made_vulnerability(damage_ratio, ceiling=0.95):
    """A VulnerabilityResult with a chosen damage ratio, independent of the curve."""
    ratio = np.asarray(damage_ratio, dtype="float64")
    return VulnerabilityResult(
        scenario_id="hand-made",
        hazard_score=np.where(ratio > 0, 0.5, 0.0),
        affected=ratio > 0,
        curve_position=np.where(ratio > 0, 2.0, 0.0),
        damage_factor=ratio / ceiling,
        ceiling=np.full(ratio.shape, ceiling),
        damage_ratio=ratio,
    )


def rows(table, scenario_id, tier):
    return table[(table["scenario_id"] == scenario_id) & (table["tier"] == tier)]


@pytest.fixture(scope="module")
def exposure():
    return load_and_validate_exposure(SUPPLIED)


@pytest.fixture(scope="module")
def table(exposure):
    return building_results(CONFIG, exposure)


# --- The primitive, with hand-worked values ----------------------------------


def test_hand_worked_losses():
    result = building_loss(hand_made_vulnerability([0.0, 0.5, 0.95]), [1_000, 1_000, 1_000])
    assert result.loss_kes.tolist() == [0.0, 500.0, 950.0]


def test_zero_damage_ratio_gives_exactly_zero_loss():
    result = building_loss(hand_made_vulnerability([0.0, 0.0]), [410_000, 1_592_010_000])
    assert result.loss_kes.tolist() == [0.0, 0.0]


def test_damage_ratio_equal_to_the_ceiling_gives_ceiling_times_tiv():
    result = building_loss(hand_made_vulnerability([0.65], ceiling=0.65), [1_592_010_000])
    assert float(result.loss_kes[0]) == pytest.approx(1_034_806_500.00, abs=BUILDING_TOL)


def test_smallest_and_largest_supplied_tiv(exposure):
    """D-001: the file values are minimum 410,000 and maximum 1,592,010,000."""
    tiv = exposure.data["tiv_kes"]
    assert tiv.min() == 410_000 and tiv.max() == 1_592_010_000
    result = building_loss(hand_made_vulnerability([0.5, 0.5]), [410_000, 1_592_010_000])
    assert result.loss_kes.tolist() == [205_000.0, 796_005_000.0]


def test_spec_7_7_score_1_h4_rcc_tiv_1m():
    result = building_loss(assess(CONFIG, "reference", 1.0, "concrete_rcc"), 1_000_000)
    assert float(result.loss_kes) == pytest.approx(586_950.00, abs=BUILDING_TOL)


def test_result_keeps_the_vulnerability_result_it_was_computed_from():
    vulnerability = hand_made_vulnerability([0.2])
    assert building_loss(vulnerability, [1_000]).vulnerability is vulnerability


# --- TIV rules (V5, D-001) ---------------------------------------------------


@pytest.mark.parametrize("tiv", [0, -1_000, float("nan"), float("inf"), float("-inf")])
def test_invalid_tiv_is_refused_citing_v5(tiv):
    with pytest.raises(ValueError, match="V5"):
        building_loss(hand_made_vulnerability([0.1, 0.2]), [1_000, tiv])


def test_tiv_and_vulnerability_must_have_the_same_length():
    with pytest.raises(ValueError, match="shape"):
        building_loss(hand_made_vulnerability([0.1, 0.2]), [1_000, 2_000, 3_000])


def test_supplied_tiv_is_preserved_exactly(exposure, table):
    """Specification 7.7, input untouched: compared with the raw CSV, not with the loader."""
    raw = pd.read_csv(SUPPLIED)
    for (scenario_id, tier), block in table.groupby(["scenario_id", "tier"], sort=False):
        assert block["tiv_kes"].to_numpy().tolist() == raw["tiv_kes"].to_numpy().tolist()
        assert block["tiv_kes"].sum() == PORTFOLIO_TIV


def test_tiv_is_never_recomputed_from_floor_area_and_cost(table):
    """D-001: the two disagree in the file, and the supplied tiv_kes is the one used."""
    raw = pd.read_csv(SUPPLIED)
    assert not np.allclose(raw["tiv_kes"], raw["floor_area_m2"] * raw["cost_per_m2_kes"])
    assert rows(table, "reference", "common")["tiv_kes"].tolist() == raw["tiv_kes"].tolist()


# --- Single buildings, reference scenario (specification 7.2) ---------------

SPEC_7_2 = [
    # loc_id, class, TIV, tier, score, curve position, damage factor, ceiling, damage ratio, loss
    ("NBO-0001", "informal_iron_sheet", 625_000, "common", 0, 0, 0, 0.95, 0, 0.00),
    ("NBO-0000", "semi_permanent", 5_170_000, "common", 0.0232857969, 0.093143, 0.040983, 0.90, 0.036885, 190_693.91),
    ("NBO-0000", "semi_permanent", 5_170_000, "moderate", 0, 0, 0, 0.90, 0, 0.00),
    ("NBO-0002", "semi_permanent", 3_300_000, "common", 0.4584057033, 1.833623, 0.601061, 0.90, 0.540955, 1_785_150.55),
    ("NBO-0002", "semi_permanent", 3_300_000, "moderate", 0.3452048898, 1.380820, 0.494531, 0.90, 0.445078, 1_468_756.43),
    ("NBO-0002", "semi_permanent", 3_300_000, "extreme", 0.1346025914, 0.538410, 0.232138, 0.90, 0.208924, 689_448.90),
    ("NBO-0005", "informal_iron_sheet", 1_570_000, "common", 0.6697713137, 2.679085, 0.758914, 0.95, 0.720969, 1_131_920.87),
    ("NBO-0005", "informal_iron_sheet", 1_570_000, "extreme", 0.4723374248, 1.889350, 0.612763, 0.95, 0.582125, 913_936.67),
    ("NBO-0316", "concrete_rcc", 522_650_000, "common", 0.6274335384, 2.509734, 0.728262, 0.65, 0.473370, 247_406_947.15),
    ("NBO-0316", "concrete_rcc", 522_650_000, "extreme", 0.4046871066, 1.618748, 0.555937, 0.65, 0.361359, 188_864_365.08),
    ("NBO-0130", "concrete_rcc", 958_965_000, "common", 0.2919898331, 1.167959, 0.429396, 0.65, 0.279107, 267_653_950.92),
    ("NBO-0130", "concrete_rcc", 958_965_000, "extreme", 0, 0, 0, 0.65, 0, 0.00),
]


@pytest.mark.parametrize(
    "loc_id, housing_class, tiv, tier, score, position, factor, ceiling, ratio, loss", SPEC_7_2
)
def test_spec_7_2_building_row_from_score_to_loss(table, loc_id, housing_class, tiv, tier, score, position,
                                                  factor, ceiling, ratio, loss):
    row = rows(table, "reference", tier).set_index("loc_id").loc[loc_id]
    assert row["housing_class"] == housing_class
    assert row["tiv_kes"] == tiv
    assert row["hazard_score"] == pytest.approx(score, abs=1e-10)  # printed to 10 decimals
    assert row["curve_position"] == pytest.approx(position, abs=1e-6)
    assert row["damage_factor"] == pytest.approx(factor, abs=1e-6)
    assert row["ceiling"] == ceiling
    assert row["damage_ratio"] == pytest.approx(ratio, abs=1e-6)
    assert row["loss_kes"] == pytest.approx(loss, abs=BUILDING_TOL)
    assert bool(row["affected"]) == (score > 0)


# --- Portfolio totals (specification 7.3), summed in the test ---------------

SPEC_7_3 = {
    "low": [266_230_031.74, 461_802_507.68, 1_086_205_322.49, 1_832_007_234.02, 2_848_531_482.88],
    "reference": [468_443_071.72, 826_175_003.83, 1_972_325_986.45, 3_279_344_346.85, 5_103_936_640.53],
    "high": [609_294_901.20, 1_103_866_847.82, 2_688_400_578.14, 4_491_451_558.67, 6_958_250_832.32],
    "reference_rcc80": [544_354_996.24, 964_742_195.00, 2_333_078_198.37, 3_887_799_934.29, 6_070_980_379.83],
}


@pytest.mark.parametrize("scenario_id", SPEC_7_3)
@pytest.mark.parametrize("tier_index", range(5))
def test_spec_7_3_tier_totals(table, scenario_id, tier_index):
    tier = TIERS[tier_index]
    total = rows(table, scenario_id, tier)["loss_kes"].sum()
    assert total == pytest.approx(SPEC_7_3[scenario_id][tier_index], abs=TOTAL_TOL)


SPEC_7_3_FURTHER = {
    2: [309_239_672.62, 538_678_237.49, 1_283_493_518.05, 2_171_392_798.76, 3_386_495_169.47],
    6: [707_127_001.41, 1_289_768_761.10, 3_181_688_855.74, 5_328_379_145.57, 8_280_759_673.94],
}


@pytest.mark.parametrize("h", SPEC_7_3_FURTHER)
@pytest.mark.parametrize("tier_index", range(5))
def test_spec_7_3_further_combinations_with_rcc_080(exposure, h, tier_index):
    """Specification 7.3: H = 2 and H = 6 with RCC 0.80, to show scenarios are freely configurable."""
    ceilings = {**CONFIG.scenarios[0].ceilings, "concrete_rcc": Ceiling(0.80, "[A]")}
    config = dataclasses.replace(CONFIG, scenarios=(Scenario("combination", "Test combination", h, "[A]", ceilings),))
    tier = TIERS[tier_index]
    total = rows(building_results(config, exposure), "combination", tier)["loss_kes"].sum()
    assert total == pytest.approx(SPEC_7_3_FURTHER[h][tier_index], abs=TOTAL_TOL)


# --- Tier summary, reference scenario (specification 7.4), computed in the test

SPEC_7_4 = [
    # tier, affected buildings, affected TIV, loss ÷ portfolio TIV %, loss ÷ affected TIV %, average loss per affected
    ("extreme", 32, 1_659_425_000, 0.7361, 28.2292, 14_638_845.99),
    ("severe", 51, 5_254_505_000, 1.2983, 15.7232, 16_199_509.88),
    ("moderate", 110, 11_978_060_000, 3.0994, 16.4662, 17_930_236.24),
    ("occasional", 174, 19_754_335_000, 5.1534, 16.6006, 18_846_806.59),
    ("common", 259, 31_394_010_000, 8.0206, 16.2577, 19_706_319.08),
]


@pytest.mark.parametrize("tier, flagged, flagged_tiv, pct_portfolio, pct_affected, average", SPEC_7_4)
def test_spec_7_4_tier_summary(table, tier, flagged, flagged_tiv, pct_portfolio, pct_affected, average):
    block = rows(table, "reference", tier)
    affected = block[block["affected"]]
    loss = block["loss_kes"].sum()
    assert len(affected) == flagged
    assert affected["tiv_kes"].sum() == flagged_tiv
    assert 100 * loss / PORTFOLIO_TIV == pytest.approx(pct_portfolio, abs=0.00005)  # printed to 4 decimals
    assert 100 * loss / flagged_tiv == pytest.approx(pct_affected, abs=0.00005)
    assert loss / flagged == pytest.approx(average, abs=BUILDING_TOL)


@pytest.mark.parametrize("tier, flagged, flagged_tiv", [r[:3] for r in SPEC_7_4])
def test_spec_7_4_affected_counts_and_tiv_are_the_same_in_every_scenario(table, tier, flagged, flagged_tiv):
    for scenario_id in SPEC_7_3:
        affected = rows(table, scenario_id, tier).query("affected")
        assert len(affected) == flagged and affected["tiv_kes"].sum() == flagged_tiv


# --- Class summary, reference scenario (specification 7.5), computed in the test

SPEC_7_5 = [
    # tier, class, buildings, class TIV, affected, affected TIV, loss
    ("common", "informal_iron_sheet", 179, 198_110_000, 74, 81_800_000, 20_602_488.80),
    ("common", "semi_permanent", 181, 850_710_000, 74, 362_000_000, 81_540_194.91),
    ("common", "permanent_masonry", 156, 8_451_170_000, 66, 3_525_445_000, 811_271_086.49),
    ("common", "concrete_rcc", 84, 54_135_085_000, 45, 27_424_765_000, 4_190_522_870.32),
    ("extreme", "informal_iron_sheet", 179, 198_110_000, 10, 10_235_000, 3_257_421.29),
    ("extreme", "semi_permanent", 181, 850_710_000, 8, 32_095_000, 7_146_264.84),
    ("extreme", "permanent_masonry", 156, 8_451_170_000, 11, 552_725_000, 129_087_712.68),
    ("extreme", "concrete_rcc", 84, 54_135_085_000, 3, 1_064_370_000, 328_951_672.92),
]


@pytest.mark.parametrize("tier, housing_class, buildings, class_tiv, flagged, flagged_tiv, loss", SPEC_7_5)
def test_spec_7_5_class_summary(table, tier, housing_class, buildings, class_tiv, flagged, flagged_tiv, loss):
    block = rows(table, "reference", tier)
    block = block[block["housing_class"] == housing_class]
    assert len(block) == buildings
    assert block["tiv_kes"].sum() == class_tiv
    assert int(block["affected"].sum()) == flagged
    assert block.loc[block["affected"], "tiv_kes"].sum() == flagged_tiv
    assert block["loss_kes"].sum() == pytest.approx(loss, abs=TOTAL_TOL)


# --- Building-results table (specification 4.1) -----------------------------


def test_table_has_the_specification_columns_in_order(table):
    assert list(table.columns) == [
        "scenario_id", "loc_id", "housing_class", "tiv_kes", "synthetic", "tier", "hazard_score",
        "affected", "curve_position", "damage_factor", "ceiling", "damage_ratio", "loss_kes",
    ]
    assert list(BUILDING_RESULT_COLUMNS) == list(table.columns)


def test_table_has_600_x_5_x_4_rows(table):
    """Specification 4.1 and C7."""
    assert len(table) == 12_000 == 600 * 5 * 4


def test_every_building_appears_once_per_scenario_and_tier(table, exposure):
    assert not table.duplicated(["scenario_id", "tier", "loc_id"]).any()
    file_order = exposure.data["loc_id"].tolist()
    for _, block in table.groupby(["scenario_id", "tier"], sort=False):
        assert block["loc_id"].tolist() == file_order


def test_rows_are_ordered_by_scenario_then_tier(table):
    order = table[["scenario_id", "tier"]].drop_duplicates().apply(tuple, axis=1).tolist()
    assert order == [(s, t) for s in ["reference", "low", "high", "reference_rcc80"] for t in TIERS]


def test_input_columns_are_carried_through(table, exposure):
    data = exposure.data
    for _, block in table.groupby(["scenario_id", "tier"], sort=False):
        assert block["housing_class"].tolist() == data["housing_class"].tolist()
        assert block["synthetic"].tolist() == data["synthetic"].tolist()
    assert table["synthetic"].all()


def test_hazard_score_comes_from_the_matching_tier_column(table, exposure):
    for tier in TIERS:
        expected = exposure.data[f"hazard_score_{tier}"].tolist()
        assert rows(table, "low", tier)["hazard_score"].tolist() == expected


# --- Output invariants (specification 3.1 and 5.3) ---------------------------


def test_loss_is_tiv_times_damage_ratio_on_every_row(table):
    assert (table["loss_kes"] == table["tiv_kes"] * table["damage_ratio"]).all()


def test_c3_loss_is_zero_exactly_where_the_score_is_zero(table):
    zero = table["hazard_score"] == 0
    assert (table.loc[zero, "loss_kes"] == 0.0).all()
    assert (table.loc[~zero, "loss_kes"] > 0).all()
    assert (table["affected"] == ~zero).all()


def test_c1_c2_ratio_and_loss_are_bounded_by_the_ceiling(table):
    assert ((table["damage_ratio"] >= 0) & (table["damage_ratio"] <= table["ceiling"])).all()
    assert ((table["loss_kes"] >= 0) & (table["loss_kes"] <= table["ceiling"] * table["tiv_kes"])).all()


def test_c4_loss_does_not_decrease_from_extreme_to_common(table):
    for scenario_id in SPEC_7_3:
        by_tier = rows(table, scenario_id, TIERS[0])[["loc_id"]].copy()
        for tier in TIERS:
            by_tier[tier] = rows(table, scenario_id, tier)["loss_kes"].to_numpy()
        assert (np.diff(by_tier[TIERS].to_numpy(), axis=1) >= 0).all()


def test_values_are_float64_and_not_rounded(table):
    for column in ["tiv_kes", "hazard_score", "curve_position", "damage_factor", "ceiling", "damage_ratio", "loss_kes"]:
        assert table[column].dtype == np.float64
    assert (table["loss_kes"] != table["loss_kes"].round(2)).any()


# --- Determinism and immutability --------------------------------------------


def test_building_results_are_deterministic(table, exposure):
    pd.testing.assert_frame_equal(building_results(CONFIG, exposure), table)


def test_building_results_leave_the_exposure_unchanged(exposure):
    before = exposure.data.copy()
    building_results(CONFIG, exposure)
    pd.testing.assert_frame_equal(exposure.data, before)


def test_building_loss_is_deterministic_and_leaves_inputs_unchanged():
    tiv = np.array([1_000.0, 2_000.0])
    vulnerability = hand_made_vulnerability([0.1, 0.3])
    first, second = building_loss(vulnerability, tiv), building_loss(vulnerability, tiv)
    assert np.array_equal(first.loss_kes, second.loss_kes)
    assert tiv.tolist() == [1_000.0, 2_000.0]
    assert vulnerability.damage_ratio.tolist() == [0.1, 0.3]


def test_result_cannot_be_changed():
    result = building_loss(hand_made_vulnerability([0.1, 0.3]), [1_000, 2_000])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.loss_kes = np.zeros(2)
    for name in ["tiv_kes", "loss_kes"]:
        with pytest.raises(ValueError):
            getattr(result, name)[0] = 0


def test_changing_the_tiv_input_afterwards_does_not_change_the_result():
    tiv = np.array([1_000.0, 2_000.0])
    result = building_loss(hand_made_vulnerability([0.1, 0.3]), tiv)
    tiv[0] = 9_999.0
    assert result.tiv_kes.tolist() == [1_000.0, 2_000.0]
    assert result.loss_kes.tolist() == pytest.approx([100.0, 600.0])
    assert isinstance(result, BuildingLossResult)
