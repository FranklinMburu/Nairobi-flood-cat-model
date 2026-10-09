"""Tests for human approval (Checkpoint 8, gate 1), on the bundled example submission.

The example has three items: four reinforced concrete houses in Kayole (KES
per house), one masonry building with stated coordinates, and twelve mabati
structures with only a total value, which can never be split automatically.
"""

import dataclasses
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loss_engine.ai_providers import ReplayProvider, build_exposure_request, run_extraction  # noqa: E402
from loss_engine.ai_records import AIProviderResult, SourceDocument, build_extraction_record  # noqa: E402
from loss_engine.approval import (  # noqa: E402
    ApprovalError,
    ApprovalRecord,
    Correction,
    approval_file_sha256,
    approval_requirements,
    candidate_sha256,
    check_approval,
    decide,
)
from loss_engine.exposure_assembly import load_gazetteer  # noqa: E402
from loss_engine.exposure_extraction import verify_extraction  # noqa: E402

EXAMPLES = ROOT / "examples"
ITEM2_FLAGS = ("item-2|housing_class|mapping_needs_confirmation", "item-2|insured_value|basis_unsupported")
ITEM3_FLAGS = ("item-3|housing_class|mapping_needs_confirmation", "item-3|insured_value|total_needs_allocation")


@pytest.fixture(scope="module")
def gazetteer():
    return load_gazetteer(ROOT / "data")


@pytest.fixture(scope="module")
def case():
    document = SourceDocument.from_text((EXAMPLES / "demo_submission.txt").read_text(encoding="utf-8"),
                                        "exposure_text", "test")
    record = run_extraction(ReplayProvider.from_file(EXAMPLES / "demo_replay_record.json"),
                            build_exposure_request(document))
    return document, record, verify_extraction(record, document)


def approve(case, gazetteer, **kwargs):
    document, record, report = case
    options = dict(approver="Reviewer", acknowledged=ITEM2_FLAGS, excluded_items=("item-3",))
    options.update(kwargs)
    return decide("approve", document=document, record=record, report=report, gazetteer=gazetteer, **options)


def with_response(case, text):
    """The same document with a different AI response, verified afresh."""
    document, record, _ = case
    result = AIProviderResult("fixture", "edited", "live", "ok", text)
    other = build_extraction_record(build_exposure_request(document), result)
    return document, other, verify_extraction(other, document)


# --- Requirements -----------------------------------------------------------------------


def test_requirements_list_every_review_flag_and_the_unsplittable_total(case, gazetteer):
    _, record, report = case
    assert report.outcome == "needs_review"
    required = approval_requirements(record, report, gazetteer)
    assert required.approvable
    assert required.acknowledge == ITEM2_FLAGS + ITEM3_FLAGS
    assert dict(required.unresolved) == {"item-3": ("tiv_kes_per_building",)}


def test_excluding_an_item_drops_its_flags(case, gazetteer):
    _, record, report = case
    required = approval_requirements(record, report, gazetteer, excluded_items=("item-3",))
    assert required.acknowledge == ITEM2_FLAGS and not required.unresolved


def test_a_rejected_candidate_cannot_be_approved(case, gazetteer):
    bad = json.loads(case[1].response_text)
    bad["items"][0]["insured_value"]["amount"] = 7_000_000  # not in the source
    rejected = with_response(case, json.dumps(bad))
    assert rejected[2].outcome == "rejected"
    assert not approval_requirements(rejected[1], rejected[2], gazetteer).approvable
    with pytest.raises(ApprovalError, match="rejected"):
        approve(rejected, gazetteer)


# --- Approval rules ---------------------------------------------------------------------


def test_a_valid_approval_records_the_exact_candidate(case, gazetteer):
    document, record, report = case
    approval = approve(case, gazetteer)
    assert approval.decision == "approve" and approval.approver_verified is False
    assert approval.document_sha256 == document.sha256
    assert approval.response_sha256 == record.response_sha256
    assert approval.candidate_sha256 == candidate_sha256(record)
    assert approval.verification_outcome == "needs_review"
    check_approval(approval, document, record, report)


def test_every_flag_must_be_acknowledged(case, gazetteer):
    with pytest.raises(ApprovalError, match="acknowledged.*basis_unsupported"):
        approve(case, gazetteer, acknowledged=ITEM2_FLAGS[:1])


def test_an_unsplittable_total_blocks_approval_until_resolved(case, gazetteer):
    with pytest.raises(ApprovalError, match="unresolved.*item-3"):
        approve(case, gazetteer, acknowledged=ITEM2_FLAGS + ITEM3_FLAGS, excluded_items=())


def test_a_reviewer_entered_value_per_building_resolves_the_total(case, gazetteer):
    entered = Correction("item-3", "tiv_kes_per_building", 800_000, "per-building schedule supplied by the broker")
    approval = approve(case, gazetteer, acknowledged=ITEM2_FLAGS + ITEM3_FLAGS, excluded_items=(), corrections=[entered])
    assert approval.to_dict()["corrections"] == [{"item_id": "item-3", "field": "tiv_kes_per_building",
                                                   "value": 800_000, "reason": entered.reason, "source": "human"}]


@pytest.mark.parametrize("excluded", [("item-1", "item-2", "item-3"), ("item-9",)])
def test_exclusions_must_be_real_items_and_leave_one(case, gazetteer, excluded):
    with pytest.raises(ApprovalError):
        approve(case, gazetteer, acknowledged=ITEM2_FLAGS + ITEM3_FLAGS, excluded_items=excluded)


def test_corrections_to_excluded_or_unknown_items_are_refused(case, gazetteer):
    with pytest.raises(ApprovalError, match="not included"):
        approve(case, gazetteer, corrections=[Correction("item-3", "building_count", 2, "r")])


def test_latitude_and_longitude_are_corrected_together(case, gazetteer):
    with pytest.raises(ApprovalError, match="together"):
        approve(case, gazetteer, corrections=[Correction("item-1", "lat", -1.27, "surveyed")])
    approve(case, gazetteer, corrections=[Correction("item-1", "lat", -1.27, "surveyed"),
                                          Correction("item-1", "lon", 36.91, "surveyed")])


@pytest.mark.parametrize("field, value", [
    ("tiv_kes", 1), ("lat", 91), ("lon", float("nan")), ("housing_class", "bungalow"), ("building_count", 0),
    ("building_count", 2.0), ("building_count", True), ("tiv_kes_per_building", 0), ("tiv_kes_per_building", "5m"),
])
def test_invalid_corrections_are_refused(field, value):
    with pytest.raises(ApprovalError):
        Correction("item-1", field, value, "reason")


def test_a_correction_needs_a_reason():
    with pytest.raises(ApprovalError, match="reason"):
        Correction("item-1", "building_count", 3, " ")


def test_zero_is_not_accepted_as_a_value_but_is_not_treated_as_missing():
    with pytest.raises(ApprovalError, match="invalid value"):
        Correction("item-1", "tiv_kes_per_building", 0, "r")


@pytest.mark.parametrize("approver", ["", "  ", None])
def test_the_approver_must_be_named(case, gazetteer, approver):
    with pytest.raises(ApprovalError):
        approve(case, gazetteer, approver=approver)


def test_approver_verified_can_never_be_true(case, gazetteer):
    approval = approve(case, gazetteer)
    with pytest.raises(ApprovalError, match="authentication"):
        dataclasses.replace(approval, approver_verified=True)


@pytest.mark.parametrize("decision", ["reject", "request_review"])
def test_reject_and_review_need_a_reason_and_never_pass(case, gazetteer, decision):
    document, record, report = case
    with pytest.raises(ApprovalError, match="reason"):
        decide(decision, document=document, record=record, report=report, approver="R", gazetteer=gazetteer)
    no = decide(decision, document=document, record=record, report=report, approver="R", gazetteer=gazetteer,
                reason="values not confirmed")
    with pytest.raises(ApprovalError, match="nothing may proceed"):
        check_approval(no, document, record, report)


def test_unknown_decision(case, gazetteer):
    document, record, report = case
    with pytest.raises(ApprovalError):
        decide("maybe", document=document, record=record, report=report, approver="R", gazetteer=gazetteer, reason="x")


def test_mismatched_inputs_are_refused(case, gazetteer):
    document, record, report = case
    other = SourceDocument.from_text("Another text.", "exposure_text", "test")
    with pytest.raises(ApprovalError, match="belong together"):
        decide("approve", document=other, record=record, report=report, approver="R", gazetteer=gazetteer)


# --- Staleness --------------------------------------------------------------------------


def test_approval_is_stale_for_a_different_response(case, gazetteer):
    approval = approve(case, gazetteer)
    changed = json.loads(case[1].response_text)
    changed["items"][1]["unmodelled_terms"] = []
    document, other, other_report = with_response(case, json.dumps(changed))
    with pytest.raises(ApprovalError, match="stale"):
        check_approval(approval, document, other, other_report)


def test_approval_is_stale_for_a_different_report(case, gazetteer):
    document, record, report = case
    approval = approve(case, gazetteer)
    first = dataclasses.replace(report.checks[0], detail=report.checks[0].detail + " (edited)")
    edited = dataclasses.replace(report, checks=(first, *report.checks[1:]))
    with pytest.raises(ApprovalError, match="stale: verification"):
        check_approval(approval, document, record, edited)


def test_approval_is_stale_for_a_different_document(case, gazetteer):
    _, record, report = case
    approval = approve(case, gazetteer)
    other = SourceDocument.from_text("Another text.", "exposure_text", "test")
    with pytest.raises(ApprovalError, match="stale: document"):
        check_approval(approval, other, record, report)


def test_reformatted_json_with_the_same_content_is_the_same_candidate(case):
    _, record, _ = case
    document, compact, _ = with_response(case, json.dumps(json.loads(record.response_text), separators=(",", ":")))
    assert candidate_sha256(compact) == candidate_sha256(record)
    assert compact.response_sha256 != record.response_sha256  # the exact response still differs


# --- Persistence ------------------------------------------------------------------------


def test_round_trip_and_file_hash(case, gazetteer):
    approval = approve(case, gazetteer, corrections=[Correction("item-1", "building_count", 4, "confirmed")])
    again = ApprovalRecord.from_dict(json.loads(approval.to_json()))
    assert again == approval
    assert approval_file_sha256(again) == approval_file_sha256(approval)


def test_malformed_approval_id_is_refused(case, gazetteer):
    with pytest.raises(ApprovalError, match="malformed"):
        dataclasses.replace(approve(case, gazetteer), approval_id="apr-1")
