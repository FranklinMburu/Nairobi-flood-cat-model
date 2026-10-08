"""Building financial loss for the deterministic loss engine.

Implements step 7 of section 3.1 and the building-results table of section 4.1
of `docs/specifications/07 - Deterministic Loss Engine Specification (Revision 2).md`:

    damage ratio -> tiv_kes -> loss_kes

`tiv_kes` is used exactly as supplied (D-001). It is never recomputed from, or
checked against, floor area and cost per m². Damage ratios are taken from a
VulnerabilityResult and never recomputed here. This module contains no
aggregation: no tier, class or portfolio totals.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import ModelConfig
from .validation import LoadedExposure
from .vulnerability import VulnerabilityResult, assess

# Specification 4.1, in order. run_id is added by the run record, a later checkpoint.
BUILDING_RESULT_COLUMNS = (
    "scenario_id",
    "loc_id",
    "housing_class",
    "tiv_kes",
    "synthetic",
    "tier",
    "hazard_score",
    "affected",
    "curve_position",
    "damage_factor",
    "ceiling",
    "damage_ratio",
    "loss_kes",
)


def _read_only(values) -> np.ndarray:
    """A private float64 copy that cannot be changed afterwards."""
    array = np.array(values, dtype="float64", copy=True)
    array.setflags(write=False)
    return array


@dataclass(frozen=True, eq=False)
class BuildingLossResult:
    """Structural loss per building for one scenario and tier.

    `vulnerability` is the result the loss was computed from, kept whole, so
    each loss can be traced back through damage ratio, ceiling, damage factor
    and curve position to the hazard score. Every array is read-only.
    """

    vulnerability: VulnerabilityResult
    tiv_kes: np.ndarray
    loss_kes: np.ndarray


def building_loss(vulnerability: VulnerabilityResult, tiv_kes) -> BuildingLossResult:
    """Step 7 of specification 3.1: loss = tiv_kes × damage_ratio.

    `tiv_kes` is one value per entry of `vulnerability`, in the same order.
    It is refused, never repaired, if any value is not a finite number above 0.
    """
    tiv = np.asarray(tiv_kes, dtype="float64")
    if tiv.shape != vulnerability.damage_ratio.shape:
        raise ValueError(
            f"tiv_kes has shape {tiv.shape} but the vulnerability result has shape "
            f"{vulnerability.damage_ratio.shape}"
        )
    bad = ~(np.isfinite(tiv) & (tiv > 0))
    if bad.any():
        raise ValueError(
            "V5: tiv_kes is a finite number above 0. "
            f"{int(bad.sum())} value(s) are not, for example {tiv[bad][:5].tolist()}"
        )
    return BuildingLossResult(
        vulnerability=vulnerability,
        tiv_kes=_read_only(tiv),
        loss_kes=_read_only(tiv * vulnerability.damage_ratio),
    )


def building_results(config: ModelConfig, exposure: LoadedExposure) -> pd.DataFrame:
    """The building-results table of specification 4.1.

    One row per building, tier and scenario: 600 × 5 × 4 = 12,000 rows for the
    supplied file and the default scenarios. Rows are ordered by scenario as
    configured, then tier in the D-003 order, then building in file order.
    Input columns are carried through unchanged.
    """
    data = exposure.data
    blocks = []
    for scenario in config.scenarios:
        for tier in config.tiers:
            vulnerability = assess(config, scenario.scenario_id, data[f"hazard_score_{tier}"], data["housing_class"])
            loss = building_loss(vulnerability, data["tiv_kes"])
            blocks.append(pd.DataFrame({
                "scenario_id": scenario.scenario_id,
                "loc_id": data["loc_id"].array,
                "housing_class": data["housing_class"].array,
                "tiv_kes": loss.tiv_kes,
                "synthetic": data["synthetic"].array,
                "tier": tier,
                "hazard_score": vulnerability.hazard_score,
                "affected": vulnerability.affected,
                "curve_position": vulnerability.curve_position,
                "damage_factor": vulnerability.damage_factor,
                "ceiling": vulnerability.ceiling,
                "damage_ratio": vulnerability.damage_ratio,
                "loss_kes": loss.loss_kes,
            }))
    return pd.concat(blocks, ignore_index=True)[list(BUILDING_RESULT_COLUMNS)]
