"""Human review and approval of a verified extraction (Checkpoint 8, gate 1).

An approval applies to one exact candidate: it records the hashes of the
source document, the AI response, the parsed candidate and the verification
report. Any later change to any of them makes the approval stale, and
`check_approval` refuses it.

Rules:
- a rejected verification cannot be approved;
- every check the verifier sent to review must be acknowledged by name;
- every included item needs a location, a housing class, a building count and
  a KES value per building, either from the verified candidate or entered by
  the reviewer as a correction (recorded as human-entered, with a reason);
- a total value for several buildings is never split: the reviewer must enter
  a per-building value or exclude the item;
- policy terms are kept and reported as not modelled.

There is no authentication in this prototype: `approver` is whatever name the
caller supplies, and `approver_verified` is always False.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime

from .ai_records import AIExtractionRecord, SourceDocument, _utc, canonical_json, sha256_text, utc_timestamp
from .exposure_extraction import VerificationReport
from .validation import HOUSING_CLASSES

RECORD_FORMAT = "approval-1"
DECISIONS = ("approve", "reject", "request_review")
CORRECTABLE_FIELDS = ("lat", "lon", "housing_class", "building_count", "tiv_kes_per_building")
APPROVAL_ID_PATTERN = re.compile(r"^apr-\d{8}T\d{6}Z-[0-9a-f]{8}$")


class ApprovalError(ValueError):
    """An approval that the rules do not allow, or one that no longer matches its candidate."""


def candidate_sha256(record: AIExtractionRecord) -> str:
    """SHA-256 of the parsed candidate in a fixed JSON form: any change to its content changes it."""
    return sha256_text(canonical_json(json.loads(record.response_text)))


def report_sha256(report: VerificationReport) -> str:
    return sha256_text(report.to_json())


def review_key(check) -> str:
    return f"{check.item_id or '*'}|{check.field}|{check.check}"


@dataclass(frozen=True)
class Correction:
    """A value entered by the human reviewer. It is never presented as extracted by the AI."""

    item_id: str
    field: str
    value: object
    reason: str

    def __post_init__(self):
        if self.field not in CORRECTABLE_FIELDS:
            raise ApprovalError(f"{self.field!r} cannot be corrected; allowed: {list(CORRECTABLE_FIELDS)}")
        if not (isinstance(self.reason, str) and self.reason.strip()):
            raise ApprovalError(f"a correction to {self.item_id}.{self.field} needs a reason")
        v = self.value
        number = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        valid = {
            "lat": number and -90 <= v <= 90,
            "lon": number and -180 <= v <= 180,
            "housing_class": v in HOUSING_CLASSES,
            "building_count": isinstance(v, int) and not isinstance(v, bool) and v > 0,
            "tiv_kes_per_building": number and v > 0,
        }[self.field]
        if not valid:
            raise ApprovalError(f"invalid value for {self.item_id}.{self.field}: {v!r}")


@dataclass(frozen=True)
class Requirements:
    """What a reviewer must do before an approval is possible."""

    approvable: bool
    acknowledge: tuple[str, ...]
    unresolved: Mapping
    reason: str = ""


def _items(record: AIExtractionRecord) -> list[dict]:
    return json.loads(record.response_text)["items"]


def _resolved(item: dict, gazetteer: Mapping, corrected: set[str]) -> list[str]:
    """The fields an item still lacks for the engine, given the candidate and the reviewer's corrections."""
    missing = []
    loc, cls, count, value = item["location"], item["housing_class"], item["building_count"], item["insured_value"]
    has_coords = loc["status"] == "stated" and loc["lat"] is not None and loc["lon"] is not None
    in_gazetteer = loc["status"] == "stated" and isinstance(loc["text"], str) and loc["text"].strip().lower() in gazetteer
    if not (has_coords or in_gazetteer or {"lat", "lon"} <= corrected):
        missing.append("location")
    if not (cls["status"] in ("stated", "mapped") and cls["value"] in HOUSING_CLASSES) and "housing_class" not in corrected:
        missing.append("housing_class")
    if not (count["status"] == "stated" and isinstance(count["value"], int)) and "building_count" not in corrected:
        missing.append("building_count")
    count_value = count["value"] if count["status"] == "stated" else None
    per_building_kes = (value["currency"] == "KES" and value["amount"] is not None
                        and (value["basis"] == "per_building" or (value["basis"] == "total" and count_value == 1)))
    if not per_building_kes and "tiv_kes_per_building" not in corrected:
        missing.append("tiv_kes_per_building")
    return missing


def approval_requirements(record: AIExtractionRecord, report: VerificationReport, gazetteer: Mapping,
                          *, corrections=(), excluded_items=()) -> Requirements:
    """The review flags to acknowledge and the fields still unresolved, per item."""
    if report.outcome == "rejected":
        return Requirements(False, (), {}, "the verifier rejected this candidate; it cannot be approved")
    if record.response_text is None:
        return Requirements(False, (), {}, "the extraction has no response")
    excluded = set(excluded_items)
    acknowledge = tuple(sorted({review_key(c) for c in report.checks
                                if c.result == "review" and c.item_id not in excluded}))
    unresolved = {}
    for item in _items(record):
        if item["item_id"] in excluded:
            continue
        corrected = {c.field for c in corrections if c.item_id == item["item_id"]}
        missing = _resolved(item, gazetteer, corrected)
        if missing:
            unresolved[item["item_id"]] = tuple(missing)
    return Requirements(True, acknowledge, unresolved)


@dataclass(frozen=True)
class ApprovalRecord:
    """One human decision on one exact verified candidate."""

    record_format: str
    approval_id: str
    decision: str
    document_id: str
    document_sha256: str
    extraction_id: str
    response_sha256: str
    candidate_sha256: str
    verification_sha256: str
    verification_outcome: str
    approver: str
    approver_verified: bool
    decided_at: str
    reason: str | None
    acknowledged: tuple[str, ...]
    corrections: tuple[Correction, ...]
    excluded_items: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "acknowledged", tuple(self.acknowledged))
        object.__setattr__(self, "corrections", tuple(self.corrections))
        object.__setattr__(self, "excluded_items", tuple(self.excluded_items))
        if self.decision not in DECISIONS:
            raise ApprovalError(f"decision must be one of {list(DECISIONS)}")
        if not (isinstance(self.approver, str) and self.approver.strip()):
            raise ApprovalError("the approver must be named")
        if self.approver_verified is not False:
            raise ApprovalError("there is no authentication in this prototype; approver_verified must be False")
        if self.decision != "approve" and not (isinstance(self.reason, str) and self.reason.strip()):
            raise ApprovalError(f"a {self.decision} decision needs a reason")
        if not APPROVAL_ID_PATTERN.match(self.approval_id):
            raise ApprovalError(f"approval_id {self.approval_id!r} is malformed")

    def to_dict(self) -> dict:
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data["acknowledged"] = list(self.acknowledged)
        data["excluded_items"] = list(self.excluded_items)
        data["corrections"] = [{"item_id": c.item_id, "field": c.field, "value": c.value, "reason": c.reason,
                                "source": "human"} for c in self.corrections]
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"

    @classmethod
    def from_dict(cls, data: dict) -> "ApprovalRecord":
        values = dict(data)
        values["corrections"] = tuple(Correction(c["item_id"], c["field"], c["value"], c["reason"])
                                      for c in data["corrections"])
        return cls(**values)


def decide(decision: str, *, document: SourceDocument, record: AIExtractionRecord, report: VerificationReport,
           approver: str, gazetteer: Mapping, reason: str | None = None, acknowledged=(), corrections=(),
           excluded_items=(), now: datetime | None = None) -> ApprovalRecord:
    """Record a human decision. An approval is refused unless every rule above is met."""
    if record.document_hashes != (document.sha256,) or report.document_sha256 != document.sha256:
        raise ApprovalError("the extraction, report and document do not belong together")
    if report.response_sha256 != record.response_sha256 or report.extraction_id != record.extraction_id:
        raise ApprovalError("the verification report is not of this extraction")
    corrections, excluded = tuple(corrections), tuple(excluded_items)
    if decision == "approve":
        required = approval_requirements(record, report, gazetteer, corrections=corrections, excluded_items=excluded)
        if not required.approvable:
            raise ApprovalError(required.reason)
        item_ids = [item["item_id"] for item in _items(record)]
        if not set(excluded) <= set(item_ids) or not set(item_ids) - set(excluded):
            raise ApprovalError("excluded items must be items of the candidate, and at least one item must remain")
        unknown = [c.item_id for c in corrections if c.item_id not in item_ids or c.item_id in excluded]
        if unknown:
            raise ApprovalError(f"corrections refer to items that are not included: {sorted(set(unknown))}")
        missing_ack = sorted(set(required.acknowledge) - set(acknowledged))
        if missing_ack:
            raise ApprovalError(f"these review flags must be acknowledged before approval: {missing_ack}")
        if required.unresolved:
            raise ApprovalError(f"these fields are unresolved and must be corrected or the item excluded: "
                                f"{dict(required.unresolved)}")
        for item_id in item_ids:
            lat_lon = {c.field for c in corrections if c.item_id == item_id} & {"lat", "lon"}
            if len(lat_lon) == 1:
                raise ApprovalError(f"{item_id}: latitude and longitude must be corrected together")
    started = _utc(now)
    return ApprovalRecord(
        record_format=RECORD_FORMAT,
        approval_id=f"apr-{started:%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}",
        decision=decision,
        document_id=document.document_id,
        document_sha256=document.sha256,
        extraction_id=record.extraction_id,
        response_sha256=record.response_sha256,
        candidate_sha256=candidate_sha256(record) if record.response_text is not None else "",
        verification_sha256=report_sha256(report),
        verification_outcome=report.outcome,
        approver=approver,
        approver_verified=False,
        decided_at=utc_timestamp(started),
        reason=reason,
        acknowledged=tuple(sorted(set(acknowledged))),
        corrections=corrections,
        excluded_items=excluded,
    )


def check_approval(approval: ApprovalRecord, document: SourceDocument, record: AIExtractionRecord,
                   report: VerificationReport) -> None:
    """Refuse an approval that is not an approval, or that no longer matches its exact candidate."""
    if approval.decision != "approve":
        raise ApprovalError(f"the decision was {approval.decision!r}: nothing may proceed")
    current = {
        "document": document.sha256, "response": record.response_sha256,
        "candidate": candidate_sha256(record), "verification": report_sha256(report),
        "extraction": record.extraction_id,
    }
    recorded = {
        "document": approval.document_sha256, "response": approval.response_sha256,
        "candidate": approval.candidate_sha256, "verification": approval.verification_sha256,
        "extraction": approval.extraction_id,
    }
    stale = [k for k in current if current[k] != recorded[k]]
    if stale:
        raise ApprovalError(f"the approval is stale: {', '.join(stale)} changed after it was given")


def approval_file_sha256(approval: ApprovalRecord) -> str:
    return hashlib.sha256(approval.to_json().encode("utf-8")).hexdigest()
