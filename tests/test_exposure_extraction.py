"""Tests for the exposure extraction schema and deterministic verifier (Checkpoint 8.2).

Candidates are hand-written JSON, standing in for an AI response. No provider,
SDK or network is used, and nothing is approved: verification only reports.
"""

import copy
import dataclasses
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loss_engine.ai_records import AIProviderResult, AIRequest, SourceDocument, build_extraction_record  # noqa: E402
from loss_engine.exposure_extraction import (  # noqa: E402
    CLASS_MAPPING_RULES,
    EXPOSURE_SCHEMA,
    ITEM_KEYS,
    SCHEMA_VERSION,
    VerificationReport,
    parse_amounts,
    parse_coordinates,
    parse_count_words,
    parse_currencies,
    verify_candidate,
    verify_extraction,
)
from loss_engine.validation import HOUSING_CLASSES  # noqa: E402

T1 = datetime(2026, 10, 8, 12, 15, 30, 123456, tzinfo=timezone.utc)
SUBMISSION = (
    "Broker submission, account Mwangi Holdings.\n"
    "Building A: one reinforced concrete office block in Westlands at -1.2676, 36.8108, insured for KES 450m.\n"
    "Building B: 40 masonry houses in Kayole, KSh 3.2M each, about 120 m2 each, rebuild cost KES 26,000 per m2.\n"
    "Building C: 12 mabati structures in Kibera insured for KSh 6m in total.\n"
    "Building D: a warehouse near the river, insured for 80m.\n"
    "Deductible KES 100,000 per claim. Business interruption cover limited to KES 5m.\n"
)
DOC = SourceDocument.from_text(SUBMISSION, "exposure_text", "test broker email", now=T1)


def missing(kind="value"):
    if kind == "location":
        return {"text": None, "lat": None, "lon": None, "status": "missing", "quote": None}
    if kind == "class":
        return {"value": None, "status": "missing", "quote": None, "mapping_rule": None}
    if kind == "insured":
        return {"amount": None, "currency": None, "basis": "missing", "quote": None}
    if kind == "cost":
        return {"amount": None, "currency": None, "status": "missing", "quote": None}
    return {"value": None, "status": "missing", "quote": None}


def item_a():
    """One RCC building, stated class, stated coordinates, KES value."""
    return {
        "item_id": "A",
        "location": {"text": "Westlands", "lat": -1.2676, "lon": 36.8108, "status": "stated",
                     "quote": "in Westlands at -1.2676, 36.8108"},
        "housing_class": {"value": "concrete_rcc", "status": "stated", "quote": "reinforced concrete office block",
                          "mapping_rule": None},
        "building_count": {"value": 1, "status": "stated", "quote": "one reinforced concrete office block"},
        "insured_value": {"amount": 450000000, "currency": "KES", "basis": "per_building", "quote": "insured for KES 450m"},
        "floor_area_m2": missing(),
        "cost_per_m2": missing("cost"),
        "unmodelled_terms": [],
    }


def item_b():
    """Forty houses, class mapped from "masonry", per-building value, area and cost stated."""
    return {
        "item_id": "B",
        "location": {"text": "Kayole", "lat": None, "lon": None, "status": "stated", "quote": "in Kayole"},
        "housing_class": {"value": "permanent_masonry", "status": "mapped", "quote": "40 masonry houses",
                          "mapping_rule": "map-masonry"},
        "building_count": {"value": 40, "status": "stated", "quote": "40 masonry houses"},
        "insured_value": {"amount": 3200000, "currency": "KES", "basis": "per_building", "quote": "KSh 3.2M each"},
        "floor_area_m2": {"value": 120, "status": "stated", "quote": "about 120 m2 each"},
        "cost_per_m2": {"amount": 26000, "currency": "KES", "status": "stated", "quote": "rebuild cost KES 26,000 per m2"},
        "unmodelled_terms": [],
    }


def item_c():
    """Twelve structures with a total value."""
    return {
        "item_id": "C",
        "location": {"text": "Kibera", "lat": None, "lon": None, "status": "stated", "quote": "in Kibera"},
        "housing_class": {"value": "informal_iron_sheet", "status": "mapped", "quote": "12 mabati structures",
                          "mapping_rule": "map-iron-sheet"},
        "building_count": {"value": 12, "status": "stated", "quote": "12 mabati structures"},
        "insured_value": {"amount": 6000000, "currency": "KES", "basis": "total", "quote": "KSh 6m in total"},
        "floor_area_m2": missing(),
        "cost_per_m2": missing("cost"),
        "unmodelled_terms": [
            {"kind": "deductible", "text": "KES 100,000 per claim", "quote": "Deductible KES 100,000 per claim"},
            {"kind": "business_interruption", "text": "limited to KES 5m",
             "quote": "Business interruption cover limited to KES 5m"},
        ],
    }


def run(*items, document=DOC):
    return verify_candidate(document, json.dumps({"items": list(items)}))


def changed(item, field, **changes):
    new = copy.deepcopy(item)
    new[field].update(changes)
    return new


def results(report, field=None, check=None):
    return [c.result for c in report.checks if (field is None or c.field == field) and (check is None or c.check == check)]


def assert_flag(report, field, check, result):
    assert result in results(report, field, check), [(c.field, c.check, c.result, c.detail) for c in report.checks]


# --- Valid candidates -----------------------------------------------------------------


def test_single_building_with_kes_value_is_a_valid_candidate():
    report = run(item_a())
    assert report.outcome == "valid_candidate"
    assert all(c.result == "pass" for c in report.checks)
    assert_flag(report, "insured_value", "currency_kes", "pass")
    assert_flag(report, "location", "coordinates_in_quote", "pass")


def test_valid_candidate_still_requires_human_approval():
    report = run(item_a())
    assert report.requires_human_approval is True
    assert not hasattr(report, "approved") and "approved" not in report.to_dict()
    with pytest.raises(ValueError, match="human approval"):
        dataclasses.replace(report, requires_human_approval=False)


def test_total_value_for_several_buildings_is_kept_and_flagged_not_split():
    report = run(item_c())
    assert report.outcome == "needs_review"
    assert not report.failures("reject")
    assert_flag(report, "insured_value", "total_needs_allocation", "review")
    assert_flag(report, "insured_value", "amount_in_quote", "pass")


def test_missing_coordinates_are_not_a_rejection():
    report = run(changed(item_a(), "location", lat=None, lon=None, quote="in Westlands"))
    assert report.outcome == "valid_candidate"


def test_missing_location_needs_review():
    report = run(changed(item_a(), "location", **missing("location")))
    assert report.outcome == "needs_review"
    assert_flag(report, "location", "location_missing", "review")


def test_mapped_class_with_optional_area_and_cost():
    report = run(item_b())
    assert report.outcome == "needs_review" and not report.failures("reject")
    assert_flag(report, "housing_class", "mapping_needs_confirmation", "review")
    assert_flag(report, "floor_area_m2", "value_in_quote", "pass")
    assert_flag(report, "cost_per_m2", "amount_in_quote", "pass")


@pytest.mark.parametrize("status, check", [("missing", "class_missing"), ("ambiguous", "class_ambiguous")])
def test_missing_or_ambiguous_class_needs_review(status, check):
    cls = missing("class") if status == "missing" else {"value": None, "status": "ambiguous",
                                                         "quote": "a warehouse near the river", "mapping_rule": None}
    report = run(changed(item_a(), "housing_class", **cls))
    assert report.outcome == "needs_review"
    assert_flag(report, "housing_class", check, "review")


def test_unmodelled_terms_are_recorded_and_not_applied():
    report = run(item_c())
    assert results(report, "unmodelled_terms[0]", "not_modelled") == ["pass"]
    assert results(report, "unmodelled_terms[1]", "not_modelled") == ["pass"]


def test_whole_submission():
    report = run(item_a(), item_b(), item_c())
    assert report.outcome == "needs_review" and not report.failures("reject")
    assert {c.item_id for c in report.checks if c.item_id} == {"A", "B", "C"}


def test_empty_extraction_needs_review():
    report = verify_candidate(DOC, '{"items": []}')
    assert report.outcome == "needs_review"


# --- Malformed candidates ---------------------------------------------------------------


@pytest.mark.parametrize("text", ["not json", '{"items": [', "", '["A"]', '{"items": {}}', '{"rows": []}',
                                  '{"items": [], "extra": 1}'])
def test_malformed_responses_are_rejected(text):
    assert verify_candidate(DOC, text).outcome == "rejected"


def test_missing_or_extra_keys_are_rejected():
    no_field = item_a()
    del no_field["building_count"]
    extra_field = changed(item_a(), "insured_value", note="added")
    for item in (no_field, extra_field):
        assert run(item).outcome == "rejected"


@pytest.mark.parametrize("field, changes, check", [
    ("building_count", {"value": "1"}, "count_type"),
    ("building_count", {"value": True}, "count_type"),
    ("insured_value", {"amount": "450m"}, "amount_type"),
    ("location", {"lat": "-1.2676"}, "coordinate_type"),
    ("housing_class", {"value": "timber_frame"}, "class_value"),
    ("housing_class", {"status": "probable"}, "status"),
    ("insured_value", {"basis": "estimated"}, "basis"),
    ("insured_value", {"currency": "Kenyan"}, "currency_value"),
])
def test_wrong_types_and_values_are_rejected(field, changes, check):
    report = run(changed(item_a(), field, **changes))
    assert report.outcome == "rejected"
    assert_flag(report, field, check, "reject")


def test_duplicate_item_ids_are_rejected():
    assert run(item_a(), item_a()).outcome == "rejected"


def test_non_exposure_document_is_rejected():
    hazard_doc = SourceDocument.from_text(SUBMISSION, "hazard_source", "test", now=T1)
    assert run(item_a(), document=hazard_doc).outcome == "rejected"


# --- Quotes and evidence -------------------------------------------------------------------


@pytest.mark.parametrize("quote", [
    "insured for KES 450 million",      # fabricated wording
    "insured for  KES 450m",            # extra space
    "insured for KES 450m!",            # altered punctuation
    "Insured for KES 450m",             # changed case
])
def test_quote_must_be_an_exact_substring(quote):
    report = run(changed(item_a(), "insured_value", quote=quote))
    assert report.outcome == "rejected"
    assert_flag(report, "insured_value", "quote_in_source", "reject")


def test_stated_value_without_quote_is_rejected():
    report = run(changed(item_a(), "insured_value", quote=None))
    assert_flag(report, "insured_value", "quote_present", "reject")


def test_missing_status_with_a_value_is_rejected():
    report = run(changed(item_a(), "building_count", status="missing"))
    assert_flag(report, "building_count", "missing_is_empty", "reject")


def test_ambiguous_status_with_a_value_is_rejected():
    report = run(changed(item_a(), "building_count", status="ambiguous"))
    assert_flag(report, "building_count", "ambiguous_has_no_value", "reject")


# --- Numbers --------------------------------------------------------------------------------


def test_amount_not_written_in_the_quote_is_rejected():
    report = run(changed(item_b(), "insured_value", amount=3300000))
    assert_flag(report, "insured_value", "amount_in_quote", "reject")


@pytest.mark.parametrize("value, check", [
    (0, "count_positive"), (-40, "count_positive"), (40.0, "count_type"), (4.5, "count_type"), (41, "count_in_quote"),
])
def test_unsupported_building_counts_are_rejected(value, check):
    report = run(changed(item_b(), "building_count", value=value))
    assert report.outcome == "rejected"
    assert_flag(report, "building_count", check, "reject")


@pytest.mark.parametrize("text", ['"Infinity"', '"NaN"'])
def test_non_finite_counts_and_amounts_are_rejected(text):
    for field, key in (("building_count", "value"), ("insured_value", "amount")):
        raw = json.dumps({"items": [item_a()]}).replace(
            f'"{key}": {1 if key == "value" else 450000000}', f'"{key}": {text.strip(chr(34))}', 1)
        assert verify_candidate(DOC, raw).outcome == "rejected", (field, raw[:80])


@pytest.mark.parametrize("changes, check", [
    ({"lat": 95.0}, "coordinate_range"),
    ({"lon": 200.0}, "coordinate_range"),
    ({"lon": None}, "coordinates_pair"),
    ({"lat": -1.3, "lon": 36.8}, "coordinates_in_quote"),     # coordinates not in the quote
])
def test_invalid_or_unsupported_coordinates_are_rejected(changes, check):
    report = run(changed(item_a(), "location", **changes))
    assert report.outcome == "rejected"
    assert_flag(report, "location", check, "reject")


@pytest.mark.parametrize("amount", [-450000000, 0])
def test_negative_or_zero_value_is_rejected(amount):
    report = run(changed(item_a(), "insured_value", amount=amount))
    assert_flag(report, "insured_value", "amount_positive", "reject")


# --- Currency ------------------------------------------------------------------------------


def item_d(currency=None):
    return {**item_a(), "item_id": "D",
            "location": {"text": "near the river", "lat": None, "lon": None, "status": "ambiguous",
                         "quote": "a warehouse near the river"},
            "housing_class": missing("class"),
            "building_count": {"value": 1, "status": "stated", "quote": "a warehouse"},
            "insured_value": {"amount": 80000000, "currency": currency, "basis": "per_building", "quote": "insured for 80m"}}


def test_amount_without_a_currency_marker_needs_review():
    report = run(item_d())
    assert report.outcome == "needs_review"
    assert_flag(report, "insured_value", "currency_missing", "review")


def test_currency_claimed_without_a_marker_is_rejected():
    report = run(item_d(currency="KES"))
    assert_flag(report, "insured_value", "currency_in_quote", "reject")


def test_non_kes_and_conflicting_currencies_need_review():
    usd_doc = SourceDocument.from_text("One office block insured for USD 2m, or KES 260m.", "exposure_text", "t", now=T1)
    usd = {**item_a(), "location": missing("location"),
           "building_count": {"value": 1, "status": "stated", "quote": "One office block"},
           "housing_class": missing("class"),
           "insured_value": {"amount": 2000000, "currency": "USD", "basis": "per_building", "quote": "insured for USD 2m"}}
    assert_flag(run(usd, document=usd_doc), "insured_value", "currency_not_kes", "review")
    both = changed(usd, "insured_value", quote="insured for USD 2m, or KES 260m")
    assert_flag(run(both, document=usd_doc), "insured_value", "currency_conflict", "review")


# --- Total versus per-building value -----------------------------------------------------------


def test_total_mislabelled_as_per_building_is_rejected():
    report = run(changed(item_c(), "insured_value", basis="per_building"))
    assert_flag(report, "insured_value", "basis_matches_source", "reject")


def test_per_building_mislabelled_as_total_is_rejected():
    report = run(changed(item_b(), "insured_value", basis="total"))
    assert_flag(report, "insured_value", "basis_matches_source", "reject")


def test_silently_split_total_is_rejected():
    report = run(changed(item_c(), "insured_value", amount=500000, basis="per_building"))
    assert report.outcome == "rejected"
    assert_flag(report, "insured_value", "amount_in_quote", "reject")


def test_basis_wording_left_out_of_the_quote_needs_review():
    """The source says "KSh 3.2M each"; quoting only "KSh 3.2M" leaves the basis outside the evidence."""
    report = run(changed(item_b(), "insured_value", quote="KSh 3.2M"))
    assert_flag(report, "insured_value", "basis_cue_outside_quote", "review")


# --- Inferred location and class -----------------------------------------------------------------


def test_location_not_in_its_quote_is_rejected():
    report = run(changed(item_b(), "location", text="Nairobi East"))
    assert_flag(report, "location", "text_in_quote", "reject")


def test_class_read_from_a_description_cannot_be_called_stated():
    report = run(changed(item_a(), "housing_class", quote="office block in Westlands"))
    assert_flag(report, "housing_class", "class_named_in_quote", "reject")


def test_mapped_class_needs_its_rule_phrase_in_the_quote():
    report = run(changed(item_b(), "housing_class", quote="houses in Kayole"))
    assert_flag(report, "housing_class", "rule_phrase_in_quote", "reject")


def test_mapped_class_needs_a_matching_documented_rule():
    for rule in (None, "map-guess"):
        assert_flag(run(changed(item_b(), "housing_class", mapping_rule=rule)), "housing_class", "mapping_rule", "reject")
    assert_flag(run(changed(item_b(), "housing_class", mapping_rule="map-concrete")), "housing_class", "rule_target", "reject")


def test_conflicting_class_wording_needs_review():
    doc = SourceDocument.from_text("Ten masonry houses with mabati roofs, KES 2m each.", "exposure_text", "t", now=T1)
    item = {**item_b(), "location": missing("location"), "floor_area_m2": missing(), "cost_per_m2": missing("cost"),
            "housing_class": {"value": "permanent_masonry", "status": "mapped",
                              "quote": "Ten masonry houses with mabati roofs", "mapping_rule": "map-masonry"},
            "building_count": {"value": 10, "status": "stated", "quote": "Ten masonry houses"},
            "insured_value": {"amount": 2000000, "currency": "KES", "basis": "per_building", "quote": "KES 2m each"}}
    report = run(item, document=doc)
    assert_flag(report, "housing_class", "class_conflict", "review")


# --- Recorded extractions --------------------------------------------------------------------------


def recorded(response_text, document=DOC, **result_changes):
    request = AIRequest("exposure", "exposure-prompt-1", "Extract stated facts only.", SCHEMA_VERSION,
                        EXPOSURE_SCHEMA, (document,))
    values = dict(provider="replay", model="m", execution_mode="replay", status="ok", response_text=response_text,
                  replay_of="ext-20261008T000000Z-00000000")
    values.update(result_changes)
    return build_extraction_record(request, AIProviderResult(**values), now=T1)


def test_recorded_extraction_is_verified_with_its_provenance():
    record = recorded(json.dumps({"items": [item_a()]}))
    report = verify_extraction(record, DOC)
    assert report.outcome == "valid_candidate"
    assert report.extraction_id == record.extraction_id and report.response_sha256 == record.response_sha256


def test_source_changed_after_extraction_is_rejected():
    record = recorded(json.dumps({"items": [item_a()]}))
    edited = SourceDocument.from_text(SUBMISSION.replace("450m", "650m"), "exposure_text", "test", now=T1)
    report = verify_extraction(record, edited)
    assert report.outcome == "rejected"
    assert_flag(report, "document", "document_hash", "reject")


def test_tampered_source_text_cannot_be_constructed():
    with pytest.raises(ValueError, match="sha256"):
        dataclasses.replace(DOC, text=SUBMISSION.replace("450m", "650m"))


def test_failed_or_mismatched_extractions_are_rejected():
    failed = recorded(None, status="error", error="provider unavailable", execution_mode="live", replay_of=None,
                      provider="gemini")
    assert verify_extraction(failed, DOC).outcome == "rejected"
    wrong_schema = dataclasses.replace(recorded('{"items": []}'), schema_version="exposure-extraction-0")
    assert_flag(verify_extraction(wrong_schema, DOC), "extraction", "schema_version", "reject")


# --- Safety properties -----------------------------------------------------------------------------


def test_verification_changes_neither_source_nor_candidate():
    before_doc = DOC.to_json()
    candidate = {"items": [item_a(), item_c()]}
    text = json.dumps(candidate)
    snapshot = copy.deepcopy(candidate)
    verify_candidate(DOC, text)
    assert DOC.to_json() == before_doc and candidate == snapshot and text == json.dumps(snapshot)


def test_report_is_deterministic_immutable_and_round_trips():
    first, second = run(item_a(), item_b()), run(item_a(), item_b())
    assert first.to_json() == second.to_json()
    assert VerificationReport.from_dict(json.loads(first.to_json())) == first
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.outcome = "valid_candidate"
    with pytest.raises(ValueError, match="does not follow"):
        dataclasses.replace(first, outcome="valid_candidate")


def test_every_failed_check_names_its_field_and_reason():
    report = run(changed(item_c(), "insured_value", amount=500000, basis="per_building"))
    for check in report.failures("reject") + report.failures("review"):
        assert check.field and check.check and check.detail


def test_schema_matches_the_engine_contract():
    items = EXPOSURE_SCHEMA["properties"]["items"]["items"]
    assert items["required"] == list(ITEM_KEYS)
    assert items["properties"]["housing_class"]["properties"]["value"]["anyOf"][0]["enum"] == list(HOUSING_CLASSES)
    assert all(target in HOUSING_CLASSES for target, _ in CLASS_MAPPING_RULES.values())
    json.dumps(EXPOSURE_SCHEMA)


# --- Deterministic parsers ----------------------------------------------------------------------


@pytest.mark.parametrize("quote, amount", [
    ("insured for 500,000", Decimal("500000")),
    ("500k", Decimal("500000")),
    ("KES 0.5M", Decimal("500000")),
    ("KSh 3.2M each", Decimal("3200000")),
    ("Sh 1.5 billion", Decimal("1500000000")),
    ("KES 26,000 per m2", Decimal("26000")),
])
def test_amount_parsing(quote, amount):
    assert amount in parse_amounts(quote)


def test_area_units_are_not_read_as_millions():
    assert Decimal("120") in parse_amounts("about 120 m2 each")
    assert Decimal("120000000") not in parse_amounts("about 120 m2 each")


@pytest.mark.parametrize("quote, currencies", [
    ("KSh 3.2M", {"KES"}), ("Sh 2m", {"KES"}), ("KES 5m", {"KES"}), ("USD 2m", {"USD"}), ("US$ 2m", {"USD"}),
    ("it makes 5m", set()), ("insured for 80m", set()), ("USD 2m or KES 260m", {"USD", "KES"}),
])
def test_currency_parsing(quote, currencies):
    assert parse_currencies(quote) == currencies


def test_coordinate_parsing():
    assert set(parse_coordinates("at -1.2676, 36.8108")) == {Decimal("-1.2676"), Decimal("36.8108")}
    assert set(parse_coordinates("1.2676° S, 36.8108° E")) == {Decimal("-1.2676"), Decimal("36.8108")}


def test_count_parsing():
    assert {40, 1}.issubset(parse_count_words("40 masonry houses and one office"))
    assert 12 in parse_count_words("twelve houses") or 12 in parse_count_words("12 houses")


# =====================================================================================
# Adversarial review: does each value mean what the candidate says it means?
# =====================================================================================

NO_LOCATION = {"text": None, "lat": None, "lon": None, "status": "missing", "quote": None}
NO_CLASS = {"value": None, "status": "missing", "quote": None, "mapping_rule": None}


def one(text, *, count_quote=None, count=1, value_quote=None, amount=None, currency="KES", basis="per_building",
        area=None, item_id="X"):
    """One item from a one-line source. Location and class are left missing (always flagged for review)."""
    doc = SourceDocument.from_text(text, "exposure_text", "adversarial test", now=T1)
    item = {"item_id": item_id, "location": NO_LOCATION, "housing_class": NO_CLASS,
            "building_count": ({"value": count, "status": "stated", "quote": count_quote} if count_quote
                               else {"value": None, "status": "missing", "quote": None}),
            "insured_value": ({"amount": amount, "currency": currency, "basis": basis, "quote": value_quote}
                              if value_quote else {"amount": None, "currency": None, "basis": "missing", "quote": None}),
            "floor_area_m2": area or missing(), "cost_per_m2": missing("cost"), "unmodelled_terms": []}
    return doc, item


def flags(report):
    """The non-pass checks, apart from the missing-field flags an adversarial item carries by design."""
    ignored = ("location_missing", "class_missing", "count_missing", "value_missing")
    return {(c.check, c.result) for c in report.checks if c.result != "pass" and c.check not in ignored}


def check(text, **kwargs):
    doc, item = one(text, **kwargs)
    return verify_candidate(doc, json.dumps({"items": [item]}))


# --- Scale words: thousand, million, billion ------------------------------------------------


@pytest.mark.parametrize("text, quote, amount", [
    ("One shop, KES 3.2M.", "KES 3.2M", 3_200_000),
    ("One shop, KES 450k.", "KES 450k", 450_000),
    ("One shop, KES 450K.", "KES 450K", 450_000),
    ("One shop, KES 1.2bn.", "KES 1.2bn", 1_200_000_000),
    ("One shop, KES 2 billion.", "KES 2 billion", 2_000_000_000),
    ("One shop, KES 3 thousand.", "KES 3 thousand", 3_000),
    ("One shop, KES 7 mn.", "KES 7 mn", 7_000_000),
    ("One shop, KES 450 million.", "KES 450 million", 450_000_000),
    ("One shop, KES 1,250,000.", "KES 1,250,000", 1_250_000),
    ("One shop, KES 0.5M.", "KES 0.5M", 500_000),
])
def test_scale_words_are_applied(text, quote, amount):
    report = check(text, count_quote="One shop", value_quote=quote, amount=amount)
    assert flags(report) == set(), flags(report)


@pytest.mark.parametrize("text, quote, wrong", [
    ("One shop, KES 3.2M.", "KES 3.2M", 3_200),            # million read as thousand
    ("One shop, KES 3.2M.", "KES 3.2M", 3.2),              # scale dropped
    ("One shop, KES 450k.", "KES 450k", 450_000_000),      # thousand read as million
    ("One shop, KES 1.2bn.", "KES 1.2bn", 1_200_000),      # billion read as million
    ("One shop, KES 2 billion.", "KES 2 billion", 2_000_000),
])
def test_wrong_scale_is_rejected(text, quote, wrong):
    report = check(text, count_quote="One shop", value_quote=quote, amount=wrong)
    assert ("amount_in_quote", "reject") in flags(report)


# --- Currency labels -------------------------------------------------------------------------


@pytest.mark.parametrize("quote", ["KES 2m", "KSh 2m", "Ksh. 2m", "Kshs 2m", "Sh 2m", "Sh. 2m", "2m shillings",
                                   "Kenya Shillings 2m", "2,000,000/=", "2,000,000/-"])
def test_kes_labels_establish_kes(quote):
    report = check(f"One shop, {quote}.", count_quote="One shop", value_quote=quote, amount=2_000_000)
    assert flags(report) == set(), flags(report)


@pytest.mark.parametrize("quote", ["TZS 2m", "Kenyan 2m", "UGX 2m", "2m"])
def test_kes_claimed_without_a_kes_marker_is_rejected(quote):
    report = check(f"One shop, {quote}.", count_quote="One shop", value_quote=quote, amount=2_000_000)
    assert ("currency_in_quote", "reject") in flags(report)


def test_unsupported_currency_code_is_rejected():
    report = check("One shop, TZS 2m.", count_quote="One shop", value_quote="TZS 2m", amount=2_000_000, currency="TZS")
    assert ("currency_value", "reject") in flags(report)


@pytest.mark.parametrize("quote, code", [("USD 2m", "USD"), ("$2m", "USD"), ("US$ 2m", "USD"), ("EUR 2m", "EUR"),
                                         ("£2m", "GBP")])
def test_non_kes_currency_is_kept_and_needs_review(quote, code):
    report = check(f"One shop, {quote}.", count_quote="One shop", value_quote=quote, amount=2_000_000, currency=code)
    assert flags(report) == {("currency_not_kes", "review")}


def test_omitted_currency_needs_review_and_never_becomes_kes():
    report = check("One shop insured for 2m.", count_quote="One shop", value_quote="insured for 2m",
                   amount=2_000_000, currency=None)
    assert ("currency_missing", "review") in flags(report)
    assert ("currency_kes", "pass") not in {(c.check, c.result) for c in report.checks}


# --- Mixed currencies ---------------------------------------------------------------------------

MIXED = "One block: USD 2m (about KES 260m)."


def test_the_currency_attached_to_the_amount_is_the_one_that_counts():
    report = check(MIXED, count_quote="One block", value_quote="USD 2m (about KES 260m)", amount=2_000_000)
    assert ("currency_matches_amount", "reject") in flags(report)  # KES claimed for the USD amount


def test_mixed_currencies_in_one_quote_need_review():
    report = check(MIXED, count_quote="One block", value_quote="USD 2m (about KES 260m)", amount=260_000_000)
    assert {("currency_conflict", "review"), ("several_amounts", "review"), ("amount_approximate", "review")} <= flags(report)


def test_currency_marker_just_outside_the_quote_does_not_support_the_claim():
    """The source reads "KES 2m", but the quote is only "2m": the evidence for KES is not in the quote."""
    report = check("One shop, KES 2m.", count_quote="One shop", value_quote="2m", amount=2_000_000)
    assert ("currency_in_quote", "reject") in flags(report)


def test_currency_named_in_the_quote_but_not_beside_the_amount_needs_review():
    report = check("Values in KES. One shop: 2m.", count_quote="One shop", value_quote="Values in KES. One shop: 2m",
                   amount=2_000_000)
    assert ("currency_not_attached", "review") in flags(report)


# --- Values belonging to other items ----------------------------------------------------------------

TWO = "Block A: one office in Westlands, KES 450m.\nBlock B: 40 masonry houses in Kayole, KSh 3.2M each.\n"


def two_items(a_value_quote, a_amount, b_value_quote="KSh 3.2M each", b_amount=3_200_000):
    doc = SourceDocument.from_text(TWO, "exposure_text", "adversarial test", now=T1)
    a = {"item_id": "A", "location": NO_LOCATION, "housing_class": NO_CLASS,
         "building_count": {"value": 1, "status": "stated", "quote": "one office"},
         "insured_value": {"amount": a_amount, "currency": "KES", "basis": "per_building", "quote": a_value_quote},
         "floor_area_m2": missing(), "cost_per_m2": missing("cost"), "unmodelled_terms": []}
    b = {**a, "item_id": "B", "building_count": {"value": 40, "status": "stated", "quote": "40 masonry houses"},
         "insured_value": {"amount": b_amount, "currency": "KES", "basis": "per_building", "quote": b_value_quote}}
    return verify_candidate(doc, json.dumps({"items": [a, b]}))


def test_correct_items_have_no_association_flags():
    report = two_items("KES 450m", 450_000_000)
    assert not [c for c in report.checks if c.check in ("evidence_in_one_sentence", "value_shared")]


def test_value_taken_from_another_items_sentence_needs_review():
    report = two_items("KSh 3.2M each", 3_200_000)
    assert ("A", "evidence_in_one_sentence", "review") in {(c.item_id, c.check, c.result) for c in report.checks}


def test_one_amount_cited_for_two_items_needs_review():
    report = two_items("KSh 3.2M each", 3_200_000)
    shared = {c.item_id for c in report.checks if c.check == "value_shared" and c.result == "review"}
    assert shared == {"A", "B"}


# --- Per-building value versus portfolio total ---------------------------------------------------------


def test_total_hidden_by_a_short_quote_is_rejected():
    report = check("12 houses insured for KSh 6m in total.", count_quote="12 houses", count=12,
                   value_quote="KSh 6m", amount=6_000_000, basis="per_building")
    assert ("basis_matches_source", "reject") in flags(report)


@pytest.mark.parametrize("text, quote", [("A total of KES 6m for 12 houses.", "A total of KES 6m"),
                                         ("KES 6m for all 12 houses.", "KES 6m for all"),
                                         ("12 houses, KES 6m combined.", "KES 6m combined")])
def test_stated_totals_are_kept_as_totals_and_never_split(text, quote):
    report = check(text, count_quote="12 houses", count=12, value_quote=quote, amount=6_000_000, basis="total")
    assert ("total_needs_allocation", "review") in flags(report)
    assert ("basis_matches_source", "reject") not in flags(report)


@pytest.mark.parametrize("amount, basis", [(500_000, "per_building"), (6_000_000, "total")])
def test_both_wordings_beside_the_amounts_need_review(amount, basis):
    text = "12 houses at KES 500k each, KES 6m in total."
    quote = "KES 500k each" if basis == "per_building" else "KES 6m in total"
    report = check(text, count_quote="12 houses", count=12, value_quote=quote, amount=amount, basis=basis)
    assert ("basis_conflict", "review") in flags(report)


def test_split_total_is_rejected():
    report = check("12 houses insured for KSh 6m in total.", count_quote="12 houses", count=12,
                   value_quote="KSh 6m in total", amount=500_000, basis="per_building")
    assert ("amount_in_quote", "reject") in flags(report)


def test_single_building_without_basis_wording_is_accepted():
    report = check("One hotel insured for KES 900m.", count_quote="One hotel", value_quote="insured for KES 900m",
                   amount=900_000_000)
    assert flags(report) == set()


# --- Numbers that are not insured values ------------------------------------------------------------


@pytest.mark.parametrize("text, count_quote, count, value_quote, number", [
    ("40 masonry houses, KSh 3.2M each.", "40 masonry houses", 40, "40 masonry houses, KSh 3.2M each", 40),
    ("One house of 120 m2, KSh 3.2M.", "One house", 1, "One house of 120 m2, KSh 3.2M", 120),
    ("One house of 120m2, KSh 3.2M.", "One house", 1, "One house of 120m2, KSh 3.2M", 120),
    ("One house of 85 sqm, KSh 3.2M.", "One house", 1, "One house of 85 sqm, KSh 3.2M", 85),
    ("One shop, valued in 2026 at KES 3m.", "One shop", 1, "One shop, valued in 2026 at KES 3m", 2026),
    ("One shop surveyed 15/03/2026, KES 3m.", "One shop", 1, "One shop surveyed 15/03/2026, KES 3m", 15),
    ("One shop surveyed 12 March, KES 3m.", "One shop", 1, "One shop surveyed 12 March, KES 3m", 12),
    ("One shop, 40% occupied, KES 3m.", "One shop", 1, "One shop, 40% occupied, KES 3m", 40),
])
def test_counts_areas_dates_and_percentages_are_not_insured_values(text, count_quote, count, value_quote, number):
    report = check(text, count_quote=count_quote, count=count, value_quote=value_quote, amount=number)
    assert ("amount_is_money", "reject") in flags(report), flags(report)


def test_plain_number_without_currency_or_scale_needs_review():
    report = check("One shop, reference 77812, KES 3m.", count_quote="One shop", value_quote="reference 77812",
                   amount=77812, currency=None)
    assert ("amount_is_money", "review") in flags(report)


@pytest.mark.parametrize("text, quote, count, result", [
    ("KES 40m for the houses.", "KES 40m for the houses", 40, "reject"),        # a money amount
    ("One house of 120 m2.", "One house of 120 m2", 120, "reject"),             # an area
    ("Valued in 2026.", "Valued in 2026", 2026, "reject"),                      # a year
    ("Lot 7, KES 3m.", "Lot 7", 7, "review"),                                   # a number, but not of buildings
])
def test_counts_must_be_counts_of_buildings(text, quote, count, result):
    report = check(text, count_quote=quote, count=count)
    assert ("count_is_a_count", result) in flags(report)


@pytest.mark.parametrize("text, quote, value, expected", [
    ("One house of 120 m2.", "One house of 120 m2", 120, None),                        # an area: passes
    ("One house, 120.", "One house, 120", 120, ("value_is_area", "review")),           # no unit
    ("One house, KES 120k.", "One house, KES 120k", 120000, ("value_is_area", "reject")),
])
def test_floor_area_must_be_an_area(text, quote, value, expected):
    report = check(text, area={"value": value, "status": "stated", "quote": quote})
    if expected is None:
        assert not {f for f in flags(report) if f[0].startswith("value")}
    else:
        assert expected in flags(report)


# --- Several monetary values in one quote ------------------------------------------------------------

POLICY = "One office insured for KES 450m, deductible KES 100,000."


def test_several_amounts_in_one_quote_need_review():
    report = check(POLICY, count_quote="One office", value_quote="insured for KES 450m, deductible KES 100,000",
                   amount=450_000_000)
    assert ("several_amounts", "review") in flags(report)


def test_a_deductible_offered_as_the_insured_value_needs_review():
    report = check(POLICY, count_quote="One office", value_quote="deductible KES 100,000", amount=100_000)
    assert ("amount_is_policy_term", "review") in flags(report)


@pytest.mark.parametrize("word", ["limit of", "excess", "premium"])
def test_other_policy_amounts_need_review(word):
    report = check(f"One office, {word} KES 5m.", count_quote="One office", value_quote=f"{word} KES 5m",
                   amount=5_000_000)
    assert ("amount_is_policy_term", "review") in flags(report)


# --- Malformed or ambiguous number expressions --------------------------------------------------------


@pytest.mark.parametrize("quote, amount", [
    ("KES 3,20,000", 320_000),        # non-standard digit grouping
    ("KES 1 000 000", 1_000_000),     # spaces as thousands separators
    ("KES 3.2.5m", 3_250_000),        # malformed decimal
    ("KES 3.2MM", 3_200_000),         # "MM" is not a recognised scale
    ("KES 3.2b", 3_200_000_000),      # "b" is not a recognised scale
])
def test_unparseable_amounts_are_rejected_not_guessed(quote, amount):
    report = check(f"One shop, {quote}.", count_quote="One shop", value_quote=quote, amount=amount)
    assert ("amount_in_quote", "reject") in flags(report)


@pytest.mark.parametrize("text, quote, amount, flag", [
    ("One godown, KES 3m-3.5m.", "KES 3m-3.5m", 3_000_000, "amount_range"),
    ("One godown, KES 3m-3.5m.", "KES 3m-3.5m", 3_500_000, "amount_range"),
    ("One godown, KES 3m to KES 3.5m.", "KES 3m to KES 3.5m", 3_000_000, "amount_range"),
    ("One godown, between KES 2m and KES 3m.", "between KES 2m and KES 3m", 2_000_000, "amount_range"),
    ("One godown, up to KES 5m.", "up to KES 5m", 5_000_000, "amount_approximate"),
    ("One godown, approximately KES 5m.", "approximately KES 5m", 5_000_000, "amount_approximate"),
    ("One godown, ~KES 5m.", "~KES 5m", 5_000_000, "amount_approximate"),
])
def test_ranges_and_approximations_need_review(text, quote, amount, flag):
    report = check(text, count_quote="One godown", value_quote=quote, amount=amount)
    assert (flag, "review") in flags(report)


# --- Housing-class decisions ------------------------------------------------------------------------------

MATERIAL_ONLY = {
    "timber houses": ("semi_permanent", "map-semi-permanent"),
    "iron sheet roofs": ("informal_iron_sheet", "map-iron-sheet"),
    "concrete floors": ("concrete_rcc", "map-concrete"),
    "mud floors": ("semi_permanent", "map-semi-permanent"),
}


@pytest.mark.parametrize("quote", list(MATERIAL_ONLY))
def test_a_material_alone_does_not_support_a_mapped_class(quote):
    doc = SourceDocument.from_text(f"Ten {quote}, KES 2m each.", "exposure_text", "t", now=T1)
    value, rule = MATERIAL_ONLY[quote]
    item = {**one("x")[1], "housing_class": {"value": value, "status": "mapped", "quote": quote, "mapping_rule": rule}}
    report = verify_candidate(doc, json.dumps({"items": [item]}))
    rejected = [c for c in report.checks if c.check == "rule_phrase_in_quote" and c.result == "reject"]
    assert rejected and "ambiguous" in rejected[0].detail


def test_mapping_keeps_source_description_and_rule_and_is_never_approval():
    report = run(item_b())
    confirm = [c for c in report.checks if c.check == "mapping_needs_confirmation"][0]
    assert confirm.result == "review" and "map-masonry" in confirm.detail and "40 masonry houses" in confirm.detail
    assert report.requires_human_approval is True
