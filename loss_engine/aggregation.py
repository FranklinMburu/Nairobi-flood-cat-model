"""Portfolio aggregation and output checks for the deterministic loss engine.

Implements section 3.2, the tier and class summaries of sections 4.2 and 4.3,
and output checks C1 to C8 of section 5.3 of
`docs/specifications/07 - Deterministic Loss Engine Specification (Revision 2).md`.

Consumes the building-results table of Checkpoint 4. Nothing here recomputes
vulnerability, loss or TIV: it only counts and sums the building rows.

- Ratios are stored as fractions, as in specification 3.2. The ×100 for a
  percentage belongs to display only.
- A field that divides by a proxy-flagged count or TIV is empty (NaN), never 0,
  when no building is flagged (specification 3.2 and 6).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .building_loss import building_results
from .config import ModelConfig
from .validation import HOUSING_CLASSES, LoadedExposure, RuleResult, ValidationReport

# Specification 4.2, with `buildings` and `tiv_kes` added after `tier`.
TIER_SUMMARY_COLUMNS = (
    "scenario_id",
    "tier",
    "buildings",
    "tiv_kes",
    "affected_buildings",
    "affected_tiv_kes",
    "portfolio_loss_kes",
    "loss_pct_portfolio",
    "loss_pct_affected",
    "avg_loss_per_affected_kes",
)

# Specification 4.3.
CLASS_SUMMARY_COLUMNS = (
    "scenario_id",
    "tier",
    "housing_class",
    "buildings",
    "tiv_kes",
    "affected_buildings",
    "affected_tiv_kes",
    "loss_kes",
    "loss_ratio",
    "share_of_tier_loss",
    "mean_damage_ratio_affected",
)

# Output checks (specification 5.3). Every one is a fail.
OUTPUT_RULES = {
    "C1": ("fail", "Every damage ratio is between 0 and the building's ceiling"),
    "C2": ("fail", "Every loss is between 0 and ceiling × tiv_kes"),
    "C3": ("fail", "Loss is 0 exactly where the score is 0"),
    "C4": ("fail", "For each building and scenario, loss does not decrease from extreme to common"),
    "C5": ("fail", "For each scenario, portfolio loss does not decrease from extreme to common"),
    "C6": ("fail", "Class losses sum to the tier's portfolio loss (tolerance KES 1)"),
    "C7": ("fail", "Building results have exactly buildings × 5 × scenarios rows"),
    "C8": ("fail", "For each tier, low ≤ reference ≤ high, and reference ≤ reference_rcc80"),
}
C6_TOLERANCE_KES = 1.0
C8_SCENARIOS = ("low", "reference", "high", "reference_rcc80")


# --- Summaries -----------------------------------------------------------------


def tier_summary(building_results: pd.DataFrame) -> pd.DataFrame:
    """Specification 4.2: one row per scenario and tier, in the order of the building results."""
    rows = []
    for (scenario_id, tier), block in building_results.groupby(["scenario_id", "tier"], sort=False):
        affected = block["affected"].to_numpy(dtype=bool)
        tiv = block["tiv_kes"].to_numpy(dtype="float64")
        loss = block["loss_kes"].to_numpy(dtype="float64")

        portfolio_tiv = tiv.sum()
        portfolio_loss = loss.sum()
        affected_buildings = int(affected.sum())
        affected_tiv = tiv[affected].sum()

        rows.append({
            "scenario_id": scenario_id,
            "tier": tier,
            "buildings": len(block),
            "tiv_kes": portfolio_tiv,
            "affected_buildings": affected_buildings,
            "affected_tiv_kes": affected_tiv,
            "portfolio_loss_kes": portfolio_loss,
            "loss_pct_portfolio": portfolio_loss / portfolio_tiv,
            "loss_pct_affected": portfolio_loss / affected_tiv if affected_buildings else np.nan,
            "avg_loss_per_affected_kes": portfolio_loss / affected_buildings if affected_buildings else np.nan,
        })
    return pd.DataFrame(rows, columns=list(TIER_SUMMARY_COLUMNS))


def class_summary(building_results: pd.DataFrame) -> pd.DataFrame:
    """Specification 4.3: one row per scenario, tier and housing class.

    Classes follow the configured class order. `mean_damage_ratio_affected`,
    laid out as class × tier, is the vulnerability matrix of the specification.
    """
    rows = []
    for (scenario_id, tier), tier_block in building_results.groupby(["scenario_id", "tier"], sort=False):
        tier_loss = tier_block["loss_kes"].to_numpy(dtype="float64").sum()
        present = set(tier_block["housing_class"])
        classes = [c for c in HOUSING_CLASSES if c in present] + sorted(present - set(HOUSING_CLASSES))

        for housing_class in classes:
            block = tier_block[tier_block["housing_class"] == housing_class]
            affected = block["affected"].to_numpy(dtype=bool)
            tiv = block["tiv_kes"].to_numpy(dtype="float64")
            loss = block["loss_kes"].to_numpy(dtype="float64")
            ratio = block["damage_ratio"].to_numpy(dtype="float64")

            class_tiv = tiv.sum()
            class_loss = loss.sum()
            affected_buildings = int(affected.sum())

            rows.append({
                "scenario_id": scenario_id,
                "tier": tier,
                "housing_class": housing_class,
                "buildings": len(block),
                "tiv_kes": class_tiv,
                "affected_buildings": affected_buildings,
                "affected_tiv_kes": tiv[affected].sum(),
                "loss_kes": class_loss,
                "loss_ratio": class_loss / class_tiv,
                "share_of_tier_loss": class_loss / tier_loss if tier_loss else np.nan,
                "mean_damage_ratio_affected": ratio[affected].mean() if affected_buildings else np.nan,
            })
    return pd.DataFrame(rows, columns=list(CLASS_SUMMARY_COLUMNS))


# --- Output checks -----------------------------------------------------------


class OutputCheckError(Exception):
    """Raised when at least one output check in C1 to C8 fails. The results must not be used."""

    def __init__(self, report: ValidationReport):
        self.report = report
        lines = [f"{r.rule}: {r.description}. {r.detail}" for r in report.failures]
        super().__init__(f"Output checks failed ({len(lines)} rule(s)):\n  " + "\n  ".join(lines))


def _rows(table: pd.DataFrame, bad: np.ndarray, value_columns: list[str], limit: int = 5) -> str:
    """Describe offending building rows by scenario, tier, loc_id and the observed values."""
    offending = table[bad]
    shown = [
        f"{r.scenario_id}/{r.tier}/{r.loc_id} (" + ", ".join(f"{c} = {getattr(r, c)}" for c in value_columns) + ")"
        for r in offending.head(limit).itertuples()
    ]
    more = f" and {len(offending) - limit} more" if len(offending) > limit else ""
    return f"{len(offending)} row(s): " + "; ".join(shown) + more


def check_outputs(
    config: ModelConfig,
    exposure: LoadedExposure,
    building_results: pd.DataFrame,
    tier_summary: pd.DataFrame,
    class_summary: pd.DataFrame,
) -> ValidationReport:
    """Apply C1 to C8 and return every rule's outcome, or raise OutputCheckError.

    Read-only: nothing is recomputed, adjusted or corrected to make a check
    pass. `exposure` supplies only the expected number of buildings and their ids.
    """
    report = ValidationReport()

    def record(rule: str, ok: bool, detail: str = "") -> bool:
        action, description = OUTPUT_RULES[rule]
        report.results.append(RuleResult(rule, description, action, "pass" if ok else action, detail))
        return ok

    def not_run(rule: str, reason: str) -> None:
        action, description = OUTPUT_RULES[rule]
        report.results.append(RuleResult(rule, description, action, "not_run", reason))

    b = building_results
    score = b["hazard_score"].to_numpy(dtype="float64")
    ratio = b["damage_ratio"].to_numpy(dtype="float64")
    ceiling = b["ceiling"].to_numpy(dtype="float64")
    tiv = b["tiv_kes"].to_numpy(dtype="float64")
    loss = b["loss_kes"].to_numpy(dtype="float64")

    # C1: 0 ≤ damage ratio ≤ ceiling.
    bad = ~((ratio >= 0) & (ratio <= ceiling))
    record("C1", not bad.any(), _rows(b, bad, ["damage_ratio", "ceiling"]) if bad.any() else "")

    # C2: 0 ≤ loss ≤ ceiling × tiv_kes.
    bad = ~((loss >= 0) & (loss <= ceiling * tiv))
    record("C2", not bad.any(), _rows(b, bad, ["loss_kes", "ceiling", "tiv_kes"]) if bad.any() else "")

    # C3: loss is 0 where the score is 0, and only there.
    bad = (score == 0) != (loss == 0)
    record("C3", not bad.any(), _rows(b, bad, ["hazard_score", "loss_kes"]) if bad.any() else "")

    # C7: every configured scenario and tier holds each building exactly once.
    expected_ids = list(exposure.data["loc_id"])
    expected_blocks = [(s.scenario_id, t) for s in config.scenarios for t in config.tiers]
    expected_rows = exposure.row_count * len(config.tiers) * len(config.scenarios)
    blocks = {key: block["loc_id"] for key, block in b.groupby(["scenario_id", "tier"], sort=False)}
    problems = []
    if len(b) != expected_rows:
        problems.append(
            f"{len(b)} rows, expected {expected_rows} ({exposure.row_count} buildings × "
            f"{len(config.tiers)} tiers × {len(config.scenarios)} scenarios)"
        )
    for key in expected_blocks:
        if key not in blocks:
            problems.append(f"{key[0]}/{key[1]}: no rows")
            continue
        ids = blocks[key]
        duplicated = sorted(set(ids[ids.duplicated()]))
        missing = sorted(set(expected_ids) - set(ids))
        unknown = sorted(set(ids) - set(expected_ids))
        for label, found in (("duplicated", duplicated), ("missing", missing), ("not in the exposure file", unknown)):
            if found:
                problems.append(f"{key[0]}/{key[1]}: {label} {found[:5]}")
    problems += [f"{k[0]}/{k[1]}: not a configured scenario and tier" for k in blocks if k not in expected_blocks]
    c7_ok = record("C7", not problems, "; ".join(problems))

    # C4: per building and scenario, loss in tier order (D-003) never decreases.
    if c7_ok:
        offending = []
        for s in config.scenarios:
            by_tier = (
                b[b["scenario_id"] == s.scenario_id]
                .pivot(index="loc_id", columns="tier", values="loss_kes")[list(config.tiers)]
            )
            decreasing = (np.diff(by_tier.to_numpy(), axis=1) < 0).any(axis=1)
            offending += [f"{s.scenario_id}/{loc}: {by_tier.loc[loc].tolist()}" for loc in by_tier.index[decreasing]]
        record("C4", not offending, f"{len(offending)} building(s), losses extreme to common: " + "; ".join(offending[:5]) if offending else "")
    else:
        not_run("C4", "C7 failed, so buildings cannot be matched across tiers")

    # Portfolio loss per scenario and tier, as reported in the tier summary.
    tier_loss = {(r.scenario_id, r.tier): r.portfolio_loss_kes for r in tier_summary.itertuples()}

    # C5: per scenario, portfolio loss in tier order (D-003) never decreases.
    offending = []
    for s in config.scenarios:
        losses = [tier_loss.get((s.scenario_id, t), np.nan) for t in config.tiers]
        if not all(a <= b_ for a, b_ in zip(losses, losses[1:])):
            offending.append(f"{s.scenario_id}: " + ", ".join(f"{t} = {v}" for t, v in zip(config.tiers, losses)))
    record("C5", not offending, "; ".join(offending))

    # C6: the class losses of each tier add up to its portfolio loss, within KES 1.
    class_totals = {}
    for r in class_summary.itertuples():
        class_totals[(r.scenario_id, r.tier)] = class_totals.get((r.scenario_id, r.tier), 0.0) + r.loss_kes
    offending = [
        f"{k[0]}/{k[1]}: classes sum to {class_totals.get(k, np.nan)}, tier loss {tier_loss.get(k, np.nan)}"
        for k in sorted(set(class_totals) | set(tier_loss))
        if not abs(class_totals.get(k, np.nan) - tier_loss.get(k, np.nan)) <= C6_TOLERANCE_KES
    ]
    record("C6", not offending, "; ".join(offending))

    # C8: scenario ordering per tier. Defined on the four default scenarios only.
    configured = {s.scenario_id for s in config.scenarios}
    if set(C8_SCENARIOS) <= configured:
        offending = []
        for t in config.tiers:
            low, ref, high, rcc80 = (tier_loss.get((sid, t), np.nan) for sid in C8_SCENARIOS)
            if not (low <= ref <= high and ref <= rcc80):
                offending.append(f"{t}: low = {low}, reference = {ref}, high = {high}, reference_rcc80 = {rcc80}")
        record("C8", not offending, "; ".join(offending))
    else:
        not_run("C8", "Needs the scenarios " + ", ".join(C8_SCENARIOS) + "; not all are configured")

    report.results.sort(key=lambda r: int(r.rule[1:]))
    if not report.passed:
        raise OutputCheckError(report)
    return report


# --- One call for the whole chain --------------------------------------------


@dataclass(frozen=True, eq=False)
class PortfolioResults:
    """Building results, both summaries, and the C1 to C8 report of one run."""

    building_results: pd.DataFrame
    tier_summary: pd.DataFrame
    class_summary: pd.DataFrame
    output_checks: ValidationReport


def aggregate(config: ModelConfig, exposure: LoadedExposure) -> PortfolioResults:
    """Building results (Checkpoint 4), the two summaries, then C1 to C8.

    Raises OutputCheckError if any check fails, so unchecked results are never returned.
    """
    results = building_results(config, exposure)
    tiers = tier_summary(results)
    classes = class_summary(results)
    report = check_outputs(config, exposure, results, tiers, classes)
    return PortfolioResults(results, tiers, classes, report)
