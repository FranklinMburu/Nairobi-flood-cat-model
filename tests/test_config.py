"""Tests for the parameter and scenario configuration (specification rev 2, sections 2.2, 2.3, rules P1 to P9)."""

import dataclasses
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loss_engine.config import (  # noqa: E402
    Ceiling,
    ConfigurationError,
    ModelConfig,
    default_config,
)
from loss_engine.validation import HOUSING_CLASSES, TIERS_WIDEST_FIRST  # noqa: E402

DEFAULT = default_config()
JRC_DEPTHS = (0, 0.5, 1, 1.5, 2, 3, 4, 5, 6)
JRC_FACTORS = (0, 0.220, 0.378, 0.531, 0.636, 0.817, 0.903, 0.957, 1.000)


def scenario(config, scenario_id):
    return next(s for s in config.scenarios if s.scenario_id == scenario_id)


def with_curve(**changes):
    return {"curve": dataclasses.replace(DEFAULT.curve, **changes)}


def with_scenario(target, /, **changes):
    return {
        "scenarios": tuple(
            dataclasses.replace(s, **changes) if s.scenario_id == target else s
            for s in DEFAULT.scenarios
        )
    }


def with_ceiling(scenario_id, housing_class, value, tag="[A]"):
    ceilings = {**scenario(DEFAULT, scenario_id).ceilings, housing_class: Ceiling(value, tag)}
    return with_scenario(scenario_id, ceilings=ceilings)


def failed_rules(**changes):
    """Build a variant of the default configuration and return the rules it fails."""
    with pytest.raises(ConfigurationError) as exc:
        dataclasses.replace(DEFAULT, **changes)
    return [r.rule for r in exc.value.report.failures]


# --- The default configuration (specification 2.2 and 2.3) -----------------


def test_default_passes_every_rule_with_no_warnings():
    assert [(r.rule, r.status) for r in DEFAULT.report.results] == [(f"P{i}", "pass") for i in range(1, 10)]


def test_tier_order_follows_d003():
    assert DEFAULT.tiers == ("extreme", "severe", "moderate", "occasional", "common")
    assert DEFAULT.tier_tag == "[O][D]"


def test_tier_order_is_the_validator_order_reversed():
    assert DEFAULT.tiers == tuple(reversed(TIERS_WIDEST_FIRST))


def test_curve_depth_points():
    assert DEFAULT.curve.depths == JRC_DEPTHS


def test_curve_damage_factors():
    assert DEFAULT.curve.damage_factors == JRC_FACTORS


def test_curve_name_and_tag():
    assert DEFAULT.curve.name == "JRC Africa residential (Huizinga et al. 2017)"
    assert DEFAULT.curve.tag == "[S]"


def test_every_scenario_has_a_ceiling_for_exactly_the_four_classes():
    assert HOUSING_CLASSES == ("informal_iron_sheet", "semi_permanent", "permanent_masonry", "concrete_rcc")
    for s in DEFAULT.scenarios:
        assert set(s.ceilings) == set(HOUSING_CLASSES)


def test_reference_ceilings():
    ceilings = scenario(DEFAULT, "reference").ceilings
    assert {cls: c.value for cls, c in ceilings.items()} == {
        "informal_iron_sheet": 0.95,
        "semi_permanent": 0.90,
        "permanent_masonry": 0.80,
        "concrete_rcc": 0.65,
    }
    assert {cls: c.tag for cls, c in ceilings.items()} == {
        "informal_iron_sheet": "[A]",
        "semi_permanent": "[A]",
        "permanent_masonry": "[A]",
        "concrete_rcc": "[A], informed by [S]",
    }


def test_rcc_sensitivity_ceiling_changes_only_the_rcc_ceiling():
    reference = scenario(DEFAULT, "reference").ceilings
    rcc80 = scenario(DEFAULT, "reference_rcc80").ceilings
    assert rcc80["concrete_rcc"] == Ceiling(0.80, "[A]")
    assert {c: rcc80[c] for c in HOUSING_CLASSES if c != "concrete_rcc"} == {
        c: reference[c] for c in HOUSING_CLASSES if c != "concrete_rcc"
    }


def test_four_default_scenarios():
    assert [(s.scenario_id, s.label, s.h, s.ceilings["concrete_rcc"].value) for s in DEFAULT.scenarios] == [
        ("reference", "Central/reference scenario", 4, 0.65),
        ("low", "Low sensitivity scenario", 2, 0.65),
        ("high", "High sensitivity scenario", 6, 0.65),
        ("reference_rcc80", "Reference scenario, RCC ceiling 0.80", 4, 0.80),
    ]
    assert all(s.h_tag == "[A]" for s in DEFAULT.scenarios)
    for scenario_id in ("low", "high"):
        assert scenario(DEFAULT, scenario_id).ceilings == scenario(DEFAULT, "reference").ceilings


# --- Fail rules (specification 5.2 and 7.7) ---------------------------------


@pytest.mark.parametrize("depths", [
    (0.5, 1, 1.5, 2, 3, 4, 5, 6, 7),                 # does not start at 0
    (0, 0.5, 1, 1, 2, 3, 4, 5, 6),                   # repeats a point
    (0, 0.5, 1, 1.5, 2, 3, 5, 4, 6),                 # decreases
    (0, 0.5, 1, 1.5, 2, 3, 4, 5, float("nan")),      # not a number
])
def test_p1_depth_points(depths):
    assert failed_rules(**with_curve(depths=depths)) == ["P1"]


def test_p1_failure_means_p5_is_not_run():
    with pytest.raises(ConfigurationError) as exc:
        dataclasses.replace(DEFAULT, **with_curve(depths=(0.5, 1, 1.5, 2, 3, 4, 5, 6, 7)))
    assert exc.value.report.status_of("P5") == "not_run"


def test_p2_curve_not_increasing_spec_case():
    """Specification 7.7: damage factors 0, 0.3, 0.2 fail P2."""
    assert failed_rules(**with_curve(depths=(0, 1, 2), damage_factors=(0, 0.3, 0.2))) == ["P2"]


@pytest.mark.parametrize("factors", [
    (0.1, 0.220, 0.378, 0.531, 0.636, 0.817, 0.903, 0.957, 1.000),           # does not start at 0
    (0, 0.220, 0.378, 0.531, 0.5, 0.817, 0.903, 0.957, 1.000),               # decreases
    (0, 0.220, 0.378, 0.531, 0.636, 0.817, 0.903, 0.957, 1.2),               # above 1
    (0, 0.220, 0.378, 0.531, float("nan"), 0.817, 0.903, 0.957, 1.000),      # not a number
])
def test_p2_damage_factors(factors):
    assert failed_rules(**with_curve(damage_factors=factors)) == ["P2"]


def test_p3_unequal_lengths():
    assert failed_rules(**with_curve(damage_factors=JRC_FACTORS[:-1])) == ["P3"]


@pytest.mark.parametrize("h", [0, -2, float("nan"), float("inf"), "4", None])
def test_p4_invalid_h(h):
    assert failed_rules(**with_scenario("low", h=h)) == ["P4"]


@pytest.mark.parametrize("h", [6.0001, 8])
def test_p5_h_above_6_warns_and_configuration_is_built(h):
    """Specification 7.7: H = 8 is a warning, not a failure."""
    config = dataclasses.replace(DEFAULT, **with_scenario("high", h=h))
    assert config.report.status_of("P5") == "warn"
    assert "high" in config.report.warnings[0].detail
    assert config.report.passed


def test_p5_h_of_exactly_6_passes():
    config = dataclasses.replace(DEFAULT, **with_scenario("high", h=6.0))
    assert config.report.status_of("P5") == "pass"


def test_p5_boundary_is_6_not_the_curve_last_point():
    """P5 is written against a fixed 6 (specification 5.2), so a longer curve does not move it."""
    curve = with_curve(depths=(0, 0.5, 1, 1.5, 2, 3, 4, 5, 7))
    config = dataclasses.replace(DEFAULT, **curve, **with_scenario("high", h=6.5))
    assert config.report.status_of("P5") == "warn"


@pytest.mark.parametrize("value", [1.2, 0, -0.1, float("nan")])
def test_p6_ceiling_out_of_range(value):
    assert failed_rules(**with_ceiling("reference_rcc80", "concrete_rcc", value)) == ["P6"]


def test_p6_ceiling_of_exactly_1_is_allowed():
    config = dataclasses.replace(DEFAULT, **with_ceiling("reference", "informal_iron_sheet", 1))
    assert config.report.status_of("P6") == "pass"


def test_p7_missing_ceiling():
    ceilings = {c: v for c, v in scenario(DEFAULT, "low").ceilings.items() if c != "permanent_masonry"}
    assert failed_rules(**with_scenario("low", ceilings=ceilings)) == ["P7"]


@pytest.mark.parametrize("tiers", [
    ("common", "occasional", "moderate", "severe", "extreme"),           # reversed
    ("extreme", "moderate", "severe", "occasional", "common"),           # two swapped
    ("extreme", "severe", "moderate", "occasional"),                     # one missing
    ("extreme", "severe", "moderate", "occasional", "common", "rare"),   # one extra
])
def test_p8_tier_list(tiers):
    assert failed_rules(tiers=tiers) == ["P8"]


def test_p9_duplicate_scenario_id():
    extra = dataclasses.replace(DEFAULT.scenarios[0], label="A second reference scenario")
    assert failed_rules(scenarios=DEFAULT.scenarios + (extra,)) == ["P9"]


# --- Behaviour ---------------------------------------------------------------


def test_all_failures_are_reported_together():
    changes = {**with_curve(damage_factors=JRC_FACTORS[:-1]), **with_scenario("low", h=0)}
    assert failed_rules(tiers=tuple(reversed(DEFAULT.tiers)), **changes) == ["P3", "P4", "P8"]


def test_invalid_configuration_cannot_be_built():
    with pytest.raises(ConfigurationError) as exc:
        ModelConfig(tiers=DEFAULT.tiers, tier_tag="[O][D]", curve=DEFAULT.curve,
                    scenarios=with_scenario("low", h=-1)["scenarios"])
    assert "P4" in str(exc.value) and "low" in str(exc.value)


def test_a_ceiling_must_carry_its_tag():
    with pytest.raises(TypeError):
        dataclasses.replace(DEFAULT.scenarios[0], ceilings={c: 0.9 for c in HOUSING_CLASSES})


# --- parameter_set_id --------------------------------------------------------


def test_parameter_set_id_is_the_sha256_of_the_canonical_json():
    expected = hashlib.sha256(DEFAULT.canonical_json().encode("utf-8")).hexdigest()
    assert DEFAULT.parameter_set_id == expected


def test_default_parameter_set_id_is_stable():
    """Pinned so that any change to the default parameters, or to how the id is made, is noticed."""
    assert DEFAULT.parameter_set_id == "3f78de52d9bcfcead9d0eb3ad257794818e7db50f74ac011d1eda7be3d6ab4bb"


def test_identical_configurations_give_identical_ids():
    rebuilt = ModelConfig(
        tiers=list(DEFAULT.tiers),
        tier_tag="[O][D]",
        curve=dataclasses.replace(DEFAULT.curve, depths=list(JRC_DEPTHS)),
        scenarios=[dataclasses.replace(s, ceilings=dict(s.ceilings)) for s in DEFAULT.scenarios],
    )
    assert default_config().parameter_set_id == DEFAULT.parameter_set_id
    assert rebuilt == DEFAULT
    assert rebuilt.parameter_set_id == DEFAULT.parameter_set_id


def test_integer_and_float_h_give_the_same_id():
    assert dataclasses.replace(DEFAULT, **with_scenario("reference", h=4)).parameter_set_id == DEFAULT.parameter_set_id


CHANGES = {
    "H": with_scenario("low", h=2.5),
    "ceiling value": with_ceiling("reference", "semi_permanent", 0.85),
    "ceiling tag": with_ceiling("reference", "semi_permanent", 0.90, tag="[S]"),
    "damage factor": with_curve(damage_factors=JRC_FACTORS[:2] + (0.38,) + JRC_FACTORS[3:]),
    "depth point": with_curve(depths=JRC_DEPTHS[:-1] + (6.5,)),
    "curve name": with_curve(name="Another curve"),
    "scenario label": with_scenario("high", label="High"),
    "scenario id": with_scenario("high", scenario_id="high_h6"),
    "tier tag": {"tier_tag": "[O]"},
}


@pytest.mark.parametrize("name", CHANGES)
def test_changing_a_parameter_changes_the_id(name):
    assert dataclasses.replace(DEFAULT, **CHANGES[name]).parameter_set_id != DEFAULT.parameter_set_id


def test_every_change_gives_a_different_id():
    ids = {dataclasses.replace(DEFAULT, **c).parameter_set_id for c in CHANGES.values()}
    assert len(ids) == len(CHANGES)


# --- Immutability ------------------------------------------------------------


def test_configuration_cannot_be_changed_after_it_is_built():
    reference = scenario(DEFAULT, "reference")
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT.tiers = ("extreme",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT.curve.depths = (0, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        reference.h = 8
    with pytest.raises(dataclasses.FrozenInstanceError):
        reference.ceilings["concrete_rcc"].value = 1.0
    with pytest.raises(TypeError):
        reference.ceilings["concrete_rcc"] = Ceiling(1.0, "[A]")
    with pytest.raises(AttributeError):
        DEFAULT.curve.depths.append(7)


def test_changing_the_input_lists_afterwards_does_not_change_the_configuration():
    depths = list(JRC_DEPTHS)
    ceilings = dict(scenario(DEFAULT, "low").ceilings)
    config = dataclasses.replace(
        DEFAULT, **with_curve(depths=depths), **with_scenario("low", ceilings=ceilings)
    )
    before = config.parameter_set_id
    depths[1] = 0.25
    ceilings["concrete_rcc"] = Ceiling(1.0, "[A]")
    assert config.curve.depths == JRC_DEPTHS
    assert scenario(config, "low").ceilings["concrete_rcc"].value == 0.65
    assert config.parameter_set_id == before
