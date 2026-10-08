"""Parameter and scenario configuration for the deterministic loss engine.

Implements sections 2.2 and 2.3 and rules P1 to P9 of section 5.2 of
`docs/specifications/07 - Deterministic Loss Engine Specification (Revision 2).md`.

A ModelConfig holds every parameter the loss calculation will use, each with
its source tag. It is checked against P1 to P9 when it is built and cannot be
changed afterwards, so an invalid configuration cannot reach a loss
calculation. It contains no loss calculation.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .validation import HOUSING_CLASSES, RuleResult, ValidationReport

# D-003: most frequent to rarest. P8 requires exactly this list.
TIERS = ("extreme", "severe", "moderate", "occasional", "common")

# The action each rule takes when it is violated (specification 5.2).
RULES = {
    "P1": ("fail", "Curve depth points start at 0 and strictly increase"),
    "P2": ("fail", "Curve damage factors start at 0, never decrease, and stay within 0 to 1"),
    "P3": ("fail", "Depth and damage-factor lists have equal length"),
    "P4": ("fail", "H is a finite number above 0"),
    "P5": ("warn", "H is at most the last depth point (6)"),
    "P6": ("fail", "Every ceiling is above 0 and at most 1"),
    "P7": ("fail", "A ceiling exists for every class"),
    "P8": ("fail", "The tier list has exactly the five tiers in the D-003 order"),
    "P9": ("fail", "Scenario ids are unique"),
}


# --- Configuration objects ---------------------------------------------------


@dataclass(frozen=True)
class Ceiling:
    """A structure-only damage ceiling for one housing class, with its source tag."""

    value: float
    tag: str


@dataclass(frozen=True)
class VulnerabilityCurve:
    """The published depth-damage curve.

    `depths` is the curve's own axis. It is used only to interpolate at a
    curve position, never as a building's flood depth (specification 3.3).
    """

    name: str
    depths: tuple[float, ...]
    damage_factors: tuple[float, ...]
    tag: str

    def __post_init__(self):
        object.__setattr__(self, "depths", tuple(self.depths))
        object.__setattr__(self, "damage_factors", tuple(self.damage_factors))


@dataclass(frozen=True)
class Scenario:
    """A named pair of H and a ceiling set (specification 2.3)."""

    scenario_id: str
    label: str
    h: float
    h_tag: str
    ceilings: Mapping[str, Ceiling]

    def __post_init__(self):
        ceilings = dict(self.ceilings)
        if not all(isinstance(c, Ceiling) for c in ceilings.values()):
            raise TypeError("Each ceiling must be a Ceiling(value, tag)")
        object.__setattr__(self, "ceilings", MappingProxyType(ceilings))


@dataclass(frozen=True)
class ModelConfig:
    """Every parameter of one run. Built only if P1 to P9 raise no failure.

    `report` holds every rule's outcome, including any P5 warning, for the run
    record. It is not part of the parameter set.
    """

    tiers: tuple[str, ...]
    tier_tag: str
    curve: VulnerabilityCurve
    scenarios: tuple[Scenario, ...]
    report: ValidationReport = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "tiers", tuple(self.tiers))
        object.__setattr__(self, "scenarios", tuple(self.scenarios))
        report = validate_parameters(self)
        if not report.passed:
            raise ConfigurationError(report)
        object.__setattr__(self, "report", report)

    def canonical_json(self) -> str:
        """Every parameter value and tag, written in one fixed text form.

        Numbers are written as floats, so H = 4 and H = 4.0 are the same
        parameter. Mapping keys are sorted. List order (tiers, curve points,
        scenarios) is kept, because it is part of the parameter set.
        """
        content = {
            "tiers": {"values": list(self.tiers), "tag": self.tier_tag},
            "curve": {
                "name": self.curve.name,
                "depths": [float(d) for d in self.curve.depths],
                "damage_factors": [float(f) for f in self.curve.damage_factors],
                "tag": self.curve.tag,
            },
            "scenarios": [
                {
                    "scenario_id": s.scenario_id,
                    "label": s.label,
                    "h": float(s.h),
                    "h_tag": s.h_tag,
                    "ceilings": {
                        cls: {"value": float(c.value), "tag": c.tag} for cls, c in s.ceilings.items()
                    },
                }
                for s in self.scenarios
            ],
        }
        return json.dumps(content, sort_keys=True, separators=(",", ":"))

    @property
    def parameter_set_id(self) -> str:
        """SHA-256 of canonical_json(). The same parameters always give the same
        id; any change to any value or tag gives a different one. No time,
        randomness or machine detail goes into it."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class ConfigurationError(Exception):
    """Raised when at least one fail rule in P1 to P9 is violated. No loss may be computed."""

    def __init__(self, report: ValidationReport):
        self.report = report
        lines = [f"{r.rule}: {r.description}. {r.detail}" for r in report.failures]
        super().__init__(
            f"Parameter validation failed ({len(lines)} rule(s)):\n  " + "\n  ".join(lines)
        )


# --- The validator -----------------------------------------------------------


def _finite(x) -> bool:
    """A real number that is not NaN or infinite. True and False are not numbers here."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_parameters(config: ModelConfig) -> ValidationReport:
    """Apply P1 to P9 and return every rule's outcome. Nothing is corrected."""
    report = ValidationReport()

    def record(rule: str, ok: bool, detail: str = "") -> bool:
        action, description = RULES[rule]
        status = "pass" if ok else action
        report.results.append(RuleResult(rule, description, action, status, detail))
        return ok

    def not_run(rule: str, reason: str) -> None:
        action, description = RULES[rule]
        report.results.append(RuleResult(rule, description, action, "not_run", reason))

    depths, factors = config.curve.depths, config.curve.damage_factors

    # P1: depth points start at 0 and strictly increase.
    p1_ok = (
        len(depths) > 0
        and all(_finite(d) for d in depths)
        and depths[0] == 0
        and all(a < b for a, b in zip(depths, depths[1:]))
    )
    record("P1", p1_ok, "" if p1_ok else f"Got {list(depths)}")

    # P2: damage factors start at 0, never decrease, and stay within 0 to 1.
    p2_ok = (
        len(factors) > 0
        and all(_finite(f) for f in factors)
        and factors[0] == 0
        and all(a <= b for a, b in zip(factors, factors[1:]))
        and all(0 <= f <= 1 for f in factors)
    )
    record("P2", p2_ok, "" if p2_ok else f"Got {list(factors)}")

    # P3: one damage factor per depth point.
    p3_ok = len(depths) == len(factors)
    record("P3", p3_ok, "" if p3_ok else f"{len(depths)} depth points, {len(factors)} damage factors")

    # P4: every scenario's H is a finite number above 0.
    bad_h = [f"{s.scenario_id} (H = {s.h!r})" for s in config.scenarios if not (_finite(s.h) and s.h > 0)]
    record("P4", not bad_h, "Scenarios: " + ", ".join(bad_h) if bad_h else "")

    # P5 (warn): H at most 6, the fixed boundary stated in specification 5.2.
    # Higher positions take the last damage factor.
    if p1_ok:
        high = [f"{s.scenario_id} (H = {s.h})" for s in config.scenarios if _finite(s.h) and s.h > 6.0]
        record("P5", not high, "Above 6: " + ", ".join(high) if high else "")
    else:
        not_run("P5", "P1 failed, so the last depth point is not defined")

    # P6: every ceiling is above 0 and at most 1.
    bad_c = [
        f"{s.scenario_id}: {cls} = {c.value!r}"
        for s in config.scenarios
        for cls, c in s.ceilings.items()
        if not (_finite(c.value) and 0 < c.value <= 1)
    ]
    record("P6", not bad_c, "; ".join(bad_c))

    # P7: a ceiling for every housing class, in every scenario. No default is ever applied.
    missing = [f"{s.scenario_id}: {cls}" for s in config.scenarios for cls in HOUSING_CLASSES if cls not in s.ceilings]
    record("P7", not missing, "Missing " + "; ".join(missing) if missing else "")

    # P8: exactly the five tiers, in the D-003 order.
    p8_ok = config.tiers == TIERS
    record("P8", p8_ok, "" if p8_ok else f"Got {list(config.tiers)}")

    # P9: scenario ids are unique.
    ids = [s.scenario_id for s in config.scenarios]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    record("P9", not dup, "Duplicated: " + ", ".join(dup) if dup else "")

    return report


# --- The parameter set of the specification ----------------------------------

# Specification 2.2. Database precision values (R-003), not the rounded report table.
JRC_AFRICA_RESIDENTIAL = VulnerabilityCurve(
    name="JRC Africa residential (Huizinga et al. 2017)",
    depths=(0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0),
    damage_factors=(0.0, 0.220, 0.378, 0.531, 0.636, 0.817, 0.903, 0.957, 1.000),
    tag="[S]",
)

# Specification 2.2 and D-005. Structure-only team assumptions; none is calibrated to Kenyan loss data.
REFERENCE_CEILINGS = MappingProxyType({
    "informal_iron_sheet": Ceiling(0.95, "[A]"),
    "semi_permanent": Ceiling(0.90, "[A]"),
    "permanent_masonry": Ceiling(0.80, "[A]"),
    "concrete_rcc": Ceiling(0.65, "[A], informed by [S]"),
})
RCC_SENSITIVITY_CEILING = Ceiling(0.80, "[A]")


def default_config() -> ModelConfig:
    """The parameters of specification 2.2 and the four default scenarios of 2.3."""
    rcc80 = {**REFERENCE_CEILINGS, "concrete_rcc": RCC_SENSITIVITY_CEILING}
    return ModelConfig(
        tiers=TIERS,
        tier_tag="[O][D]",
        curve=JRC_AFRICA_RESIDENTIAL,
        scenarios=(
            Scenario("reference", "Central/reference scenario", 4.0, "[A]", REFERENCE_CEILINGS),
            Scenario("low", "Low sensitivity scenario", 2.0, "[A]", REFERENCE_CEILINGS),
            Scenario("high", "High sensitivity scenario", 6.0, "[A]", REFERENCE_CEILINGS),
            Scenario("reference_rcc80", "Reference scenario, RCC ceiling 0.80", 4.0, "[A]", rcc80),
        ),
    )
