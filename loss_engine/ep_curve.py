"""Return-period mapping and scenario-based EP / loss points.

Applies decision D-004 to the tier summary of Checkpoint 5. Specification 07
leaves return periods and the EP curve to this later step (section 1).

What the output is: a scenario-based EP / loss representation constructed from
five deterministic hazard tiers assigned provisional return periods. It is not
a calibrated, stochastic or Monte Carlo EP curve, and not a flood-frequency
model. The return periods are PROVISIONAL assumptions taken from the
organizers' reference dashboard (D-004); the hazard files carry no frequency
information.

The five modelled points are the whole output. Nothing is interpolated,
extrapolated, smoothed or integrated. EAL, PML and TVaR are deferred: five
points do not constrain them without assumptions the data cannot support.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

from .config import TIERS
from .validation import RuleResult, ValidationReport

# Return-period mapping rules. Every one is a fail.
RP_RULES = {
    "RP1": ("fail", "The tier list is exactly the five tiers in the D-003 order"),
    "RP2": ("fail", "Every tier has one return period, a finite number of at least 1 year"),
    "RP3": ("fail", "Return periods strictly increase in the D-003 order"),
}

EP_POINT_COLUMNS = (
    "scenario_id",
    "tier",
    "return_period_years",
    "annual_exceedance_probability",
    "portfolio_loss_kes",
    "loss_pct_portfolio",
    "affected_buildings",
    "affected_tiv_kes",
)

# Columns of the Checkpoint 5 tier summary that the EP points are read from.
_INPUT_COLUMNS = ("scenario_id", "tier", "portfolio_loss_kes", "loss_pct_portfolio", "affected_buildings", "affected_tiv_kes")


class ReturnPeriodError(Exception):
    """Raised when a return-period mapping fails RP1 to RP3."""

    def __init__(self, report: ValidationReport):
        self.report = report
        lines = [f"{r.rule}: {r.description}. {r.detail}" for r in report.failures]
        super().__init__(f"Return-period mapping is invalid ({len(lines)} rule(s)):\n  " + "\n  ".join(lines))


class EPCurveError(Exception):
    """Raised when a tier summary cannot be turned into EP points. Nothing is reordered or repaired."""


def _finite(x) -> bool:
    """A real number that is not NaN or infinite. True and False are not numbers here."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# --- The mapping -------------------------------------------------------------


@dataclass(frozen=True)
class ReturnPeriodMapping:
    """One configurable tier-to-years table (D-004), checked against RP1 to RP3 when built.

    `annual_exceedance_probabilities` uses the convention AEP = 1 / T.
    """

    tiers: tuple[str, ...]
    return_periods_years: tuple[float, ...]
    status: str
    source: str
    tag: str
    report: ValidationReport = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "tiers", tuple(self.tiers))
        object.__setattr__(self, "return_periods_years", tuple(self.return_periods_years))
        report = _validate_mapping(self)
        if not report.passed:
            raise ReturnPeriodError(report)
        object.__setattr__(self, "report", report)

    @property
    def annual_exceedance_probabilities(self) -> tuple[float, ...]:
        """AEP = 1 / T for each tier, as a fraction (0.1, not 10%)."""
        return tuple(1 / t for t in self.return_periods_years)

    def canonical_json(self) -> str:
        """Every value and label of the mapping in one fixed text form. mapping_id is its SHA-256."""
        content = {
            "tiers": list(self.tiers),
            "return_periods_years": [float(t) for t in self.return_periods_years],
            "status": self.status,
            "source": self.source,
            "tag": self.tag,
        }
        return json.dumps(content, sort_keys=True, separators=(",", ":"))

    @property
    def mapping_id(self) -> str:
        """SHA-256 of canonical_json(). The same mapping always gives the same id; any change, a new one."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _validate_mapping(mapping: ReturnPeriodMapping) -> ValidationReport:
    """Apply RP1 to RP3 and return every rule's outcome. Nothing is sorted or corrected."""
    report = ValidationReport()

    def record(rule: str, ok: bool, detail: str = "") -> bool:
        action, description = RP_RULES[rule]
        report.results.append(RuleResult(rule, description, action, "pass" if ok else action, detail))
        return ok

    def not_run(rule: str, reason: str) -> None:
        action, description = RP_RULES[rule]
        report.results.append(RuleResult(rule, description, action, "not_run", reason))

    tiers, years = mapping.tiers, mapping.return_periods_years

    # RP1: exactly the D-003 tiers, in order. No tier missing, extra, repeated or moved.
    rp1_ok = tiers == TIERS
    record("RP1", rp1_ok, "" if rp1_ok else f"Got {list(tiers)}, expected {list(TIERS)}")

    # RP2: one finite return period of at least 1 year per tier, so that 0 < AEP ≤ 1.
    problems = []
    if len(years) != len(tiers):
        problems.append(f"{len(tiers)} tiers but {len(years)} return periods")
    problems += [f"{t} = {y}" for t, y in zip(tiers, years) if not (_finite(y) and y >= 1)]
    rp2_ok = record("RP2", not problems, "; ".join(problems))

    # RP3: strictly increasing in the D-003 order. A repeated or decreasing value fails.
    if rp2_ok:
        broken = [f"{a} = {x} then {b} = {y}" for (a, x), (b, y) in zip(zip(tiers, years), zip(tiers[1:], years[1:])) if not x < y]
        record("RP3", not broken, "; ".join(broken))
    else:
        not_run("RP3", "RP2 failed, so the return periods cannot be compared")

    return report


def default_return_periods() -> ReturnPeriodMapping:
    """The D-004 mapping: 10, 25, 50, 100 and 250 years. PROVISIONAL; an assumption, not an observation."""
    return ReturnPeriodMapping(
        tiers=TIERS,
        return_periods_years=(10.0, 25.0, 50.0, 100.0, 250.0),
        status="PROVISIONAL",
        source="D-004: the organizers' reference dashboard; not confirmed by the organizers",
        tag="[O, as a stated assumption of the reference dashboard]",
    )


# --- EP points ---------------------------------------------------------------


def ep_points(tier_summary: pd.DataFrame, mapping: ReturnPeriodMapping) -> pd.DataFrame:
    """Five EP points per scenario, read from the Checkpoint 5 tier summary.

    Each point is a modelled portfolio loss with the return period and AEP the
    mapping assigns to its tier. Rows are ordered by scenario as in the
    summary, then by tier in the D-003 order: AEP descending, return period
    ascending. Loss, loss share and affected values are carried over unchanged.

    Raises EPCurveError, before producing anything, if a scenario does not
    have each mapped tier exactly once, or if its portfolio loss decreases from
    extreme to common (C5).
    """
    missing = [c for c in _INPUT_COLUMNS if c not in tier_summary.columns]
    if missing:
        raise EPCurveError(f"The tier summary lacks the column(s): {', '.join(missing)}")
    if tier_summary.empty:
        raise EPCurveError("The tier summary has no rows")

    return_period = dict(zip(mapping.tiers, mapping.return_periods_years))
    aep = dict(zip(mapping.tiers, mapping.annual_exceedance_probabilities))
    expected = Counter(mapping.tiers)

    rows = []
    for scenario_id, block in tier_summary.groupby("scenario_id", sort=False):
        found = Counter(block["tier"])
        if found != expected:
            raise EPCurveError(
                f"{scenario_id}: needs each of {list(mapping.tiers)} exactly once; "
                f"got {dict(found)}"
            )
        by_tier = block.set_index("tier")

        # C5, checked again on the input received: loss must not fall as the tier becomes rarer.
        losses = [by_tier.at[t, "portfolio_loss_kes"] for t in mapping.tiers]
        if not all(a <= b for a, b in zip(losses, losses[1:])):
            raise EPCurveError(
                f"C5: For each scenario, portfolio loss does not decrease from extreme to common. "
                f"{scenario_id}: " + ", ".join(f"{t} = {v}" for t, v in zip(mapping.tiers, losses))
            )

        for tier in mapping.tiers:
            rows.append({
                "scenario_id": scenario_id,
                "tier": tier,
                "return_period_years": return_period[tier],
                "annual_exceedance_probability": aep[tier],
                "portfolio_loss_kes": by_tier.at[tier, "portfolio_loss_kes"],
                "loss_pct_portfolio": by_tier.at[tier, "loss_pct_portfolio"],
                "affected_buildings": by_tier.at[tier, "affected_buildings"],
                "affected_tiv_kes": by_tier.at[tier, "affected_tiv_kes"],
            })
    return pd.DataFrame(rows, columns=list(EP_POINT_COLUMNS))
