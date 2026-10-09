"""Approved extraction -> engine-ready exposure rows (Checkpoint 8, Mode B).

Converts the exact approved candidate into rows of the Checkpoint 1 exposure
schema (validation.REQUIRED_COLUMNS), then fills the five hazard scores from
the supplied rasters (hazard_lookup). Nothing reaches this step unless
approval.check_approval accepts the approval for this exact candidate.

Rules, all deterministic:
- location: a reviewer correction, else coordinates stated in the source,
  else an exact name match to one of the organizers' 24 geocoded hotspots
  (a neighbourhood point, approximate [A]); otherwise the item cannot proceed;
- housing class, building count and per-building value: a reviewer correction,
  else the verified candidate; a total for several buildings is never split;
- the value must be in KES; nothing is converted;
- `tiv_kes` is used exactly as stated or entered; floor area and cost per m²
  are carried through only and never used for TIV (D-001);
- one row per building: loc_id = SUB-<first 8 hex of document sha256>-<item number>-<building number>;
- synthetic = True; source names the submission document.
Every value's origin (ai, human, gazetteer, raster) is recorded per row.
Policy terms (deductibles, limits, business interruption) are listed, also for
excluded items, and never applied: the engine computes structural loss only.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .ai_records import AIExtractionRecord, SourceDocument
from .approval import ApprovalRecord, check_approval
from .exposure_extraction import VerificationReport
from .hazard_lookup import HazardLookupError, HazardRasters
from .validation import REQUIRED_COLUMNS, SCORE_COLUMNS

GAZETTEER_FILE = "nairobi_hotspots_geocoded.csv"


class AssemblyError(ValueError):
    """An approved item that still cannot become an engine row. Nothing is guessed to work around it."""


def load_gazetteer(data_dir: str | Path) -> dict:
    """The organizers' 24 geocoded hotspot names (lower-cased) -> (name, lat, lon). Approximate centroids."""
    with open(Path(data_dir) / GAZETTEER_FILE, encoding="utf-8", newline="") as handle:
        return {row["name"].strip().lower(): (row["name"], float(row["lat"]), float(row["lon"]))
                for row in csv.DictReader(handle)}


@dataclass(frozen=True)
class AssembledExposure:
    """Engine rows built from one approval, with each value's origin and the terms not modelled."""

    rows: tuple
    origins: tuple
    unmodelled_terms: tuple
    excluded_items: tuple


def _items(record: AIExtractionRecord) -> list[dict]:
    import json
    return json.loads(record.response_text)["items"]


def assemble(document: SourceDocument, record: AIExtractionRecord, report: VerificationReport,
             approval: ApprovalRecord, gazetteer: Mapping) -> AssembledExposure:
    """Rows for every building of every included item, without hazard scores yet."""
    check_approval(approval, document, record, report)
    rows, origins, terms = [], [], []
    prefix = f"SUB-{document.sha256[:8]}"
    for number, item in enumerate(_items(record), start=1):
        item_id = item["item_id"]
        if item_id in approval.excluded_items:  # its policy terms stay visible in the audit trail
            terms += [{"item_id": item_id, "item_status": "excluded", **t} for t in item["unmodelled_terms"]]
            continue
        fixed = {c.field: c for c in approval.corrections if c.item_id == item_id}
        origin = {}
        loc, cls, count, value = item["location"], item["housing_class"], item["building_count"], item["insured_value"]

        if "lat" in fixed:
            lat, lon = fixed["lat"].value, fixed["lon"].value
            origin["lat_lon"] = f"human: {fixed['lat'].reason}"
        elif loc["status"] == "stated" and loc["lat"] is not None:
            lat, lon = loc["lat"], loc["lon"]
            origin["lat_lon"] = f"ai, verified quote: {loc['quote']!r}"
        elif loc["status"] == "stated" and isinstance(loc["text"], str) and loc["text"].strip().lower() in gazetteer:
            name, lat, lon = gazetteer[loc["text"].strip().lower()]
            origin["lat_lon"] = f"gazetteer: organizer hotspot point for {name!r} [A] (neighbourhood_point, approximate)"
        else:
            raise AssemblyError(f"{item_id}: no usable location")

        if "housing_class" in fixed:
            housing_class, origin["housing_class"] = fixed["housing_class"].value, f"human: {fixed['housing_class'].reason}"
        else:
            housing_class = cls["value"]
            origin["housing_class"] = (f"ai, stated: {cls['quote']!r}" if cls["status"] == "stated"
                                       else f"ai, mapped by rule {cls['mapping_rule']} [A] from {cls['quote']!r}, "
                                            f"confirmed by {approval.approver}")

        if "building_count" in fixed:
            buildings, origin["building_count"] = fixed["building_count"].value, f"human: {fixed['building_count'].reason}"
        else:
            buildings, origin["building_count"] = count["value"], f"ai, verified quote: {count['quote']!r}"

        if "tiv_kes_per_building" in fixed:
            tiv, origin["tiv_kes"] = fixed["tiv_kes_per_building"].value, f"human: {fixed['tiv_kes_per_building'].reason}"
        elif value["currency"] == "KES" and (value["basis"] == "per_building" or (value["basis"] == "total" and buildings == 1)):
            tiv, origin["tiv_kes"] = value["amount"], f"ai, verified quote: {value['quote']!r} ({value['basis']})"
        else:
            raise AssemblyError(f"{item_id}: no KES value per building; a total is never split")

        area = item["floor_area_m2"]
        cost = item["cost_per_m2"]
        for building in range(1, buildings + 1):
            rows.append({
                "loc_id": f"{prefix}-{number:02d}-{building:03d}",
                "lat": lat, "lon": lon, "housing_class": housing_class,
                "floor_area_m2": area["value"] if area["status"] == "stated" else "",
                "cost_per_m2_kes": cost["amount"] if cost["status"] == "stated" and cost["currency"] == "KES" else "",
                "tiv_kes": tiv, "synthetic": "True",
                "source": f"free-text submission {document.document_id}",
            })
            origins.append({"loc_id": rows[-1]["loc_id"], "item_id": item_id, **origin})
        terms += [{"item_id": item_id, "item_status": "included", **t} for t in item["unmodelled_terms"]]
    return AssembledExposure(tuple(rows), tuple(origins), tuple(terms), tuple(approval.excluded_items))


def enrich(rows, rasters: HazardRasters) -> tuple:
    """The rows with the five hazard scores sampled from the rasters. A failure names the building."""
    enriched = []
    tier_of = {f"hazard_score_{t}": t for t in ("extreme", "severe", "moderate", "occasional", "common")}
    for row in rows:
        try:
            sample = rasters.sample(row["lat"], row["lon"])
        except HazardLookupError as error:
            raise AssemblyError(f"{row['loc_id']}: hazard lookup failed ({error})") from error
        enriched.append({**row, **{column: sample.scores[tier_of[column]] for column in SCORE_COLUMNS}})
    return tuple(enriched)


def _text(value) -> str:
    if isinstance(value, bool) or value is None:
        return "" if value is None else str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value)) if abs(value) < 2**53 else repr(value)
    return repr(value) if isinstance(value, float) else str(value)


def write_exposure(rows, path: str | Path) -> Path:
    """Write rows as a Checkpoint 1 exposure CSV (all required columns, in the engine's order)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(REQUIRED_COLUMNS)
        for row in rows:
            writer.writerow([_text(row[c]) for c in REQUIRED_COLUMNS])
    return path


def write_combined(base_csv: str | Path, rows, path: str | Path) -> Path:
    """The base portfolio exactly as supplied (read as text), followed by the new rows."""
    base = pd.read_csv(base_csv, dtype=str, keep_default_na=False)
    added = pd.DataFrame([{c: _text(row[c]) for c in REQUIRED_COLUMNS} for row in rows])
    overlap = set(base["loc_id"]) & set(added["loc_id"])
    if overlap:
        raise AssemblyError(f"loc_id already in the base portfolio: {sorted(overlap)[:5]}")
    combined = pd.concat([base, added.reindex(columns=base.columns, fill_value="")], ignore_index=True)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="") as handle:
        combined.to_csv(handle, index=False, lineterminator="\n")
    return path
