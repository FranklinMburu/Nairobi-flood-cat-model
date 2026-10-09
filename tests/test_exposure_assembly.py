"""Tests for turning an approved extraction into engine-ready exposure rows (Checkpoint 8)."""

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loss_engine.ai_providers import ReplayProvider, build_exposure_request, run_extraction  # noqa: E402
from loss_engine.ai_records import SourceDocument  # noqa: E402
from loss_engine.approval import ApprovalError, Correction, decide  # noqa: E402
from loss_engine.exposure_assembly import (  # noqa: E402
    AssemblyError,
    assemble,
    enrich,
    load_gazetteer,
    write_combined,
    write_exposure,
)
from loss_engine.exposure_extraction import verify_extraction  # noqa: E402
from loss_engine.validation import REQUIRED_COLUMNS, load_and_validate_exposure  # noqa: E402

DATA = ROOT / "data"
EXAMPLES = ROOT / "examples"
BASE = DATA / "exposure_nairobi_with_hazard.csv"
ITEM2_FLAGS = ("item-2|housing_class|mapping_needs_confirmation", "item-2|insured_value|basis_unsupported")
ITEM3_FLAGS = ("item-3|housing_class|mapping_needs_confirmation", "item-3|insured_value|total_needs_allocation")


@pytest.fixture(scope="module")
def gazetteer():
    return load_gazetteer(DATA)


@pytest.fixture(scope="module")
def case():
    document = SourceDocument.from_text((EXAMPLES / "demo_submission.txt").read_text(encoding="utf-8"),
                                        "exposure_text", "test")
    record = run_extraction(ReplayProvider.from_file(EXAMPLES / "demo_replay_record.json"),
                            build_exposure_request(document))
    return document, record, verify_extraction(record, document)


def approval_for(case, gazetteer, decision="approve", **kwargs):
    document, record, report = case
    options = dict(acknowledged=ITEM2_FLAGS, excluded_items=("item-3",))
    options.update(kwargs)
    return decide(decision, document=document, record=record, report=report, approver="Reviewer",
                  gazetteer=gazetteer, **options)


@pytest.fixture(scope="module")
def assembled(case, gazetteer):
    return assemble(*case, approval_for(case, gazetteer), gazetteer)


def test_gazetteer_has_the_24_organizer_hotspots(gazetteer):
    assert len(gazetteer) == 24
    assert gazetteer["kayole"] == ("Kayole", -1.2667475, 36.9193623)


def test_one_row_per_building_with_stated_values(assembled, case):
    document = case[0]
    rows = assembled.rows
    prefix = f"SUB-{document.sha256[:8]}"
    assert [r["loc_id"] for r in rows] == [f"{prefix}-01-00{i}" for i in range(1, 5)] + [f"{prefix}-02-001"]
    assert [r["tiv_kes"] for r in rows] == [6_500_000] * 4 + [3_200_000]
    assert [r["housing_class"] for r in rows] == ["concrete_rcc"] * 4 + ["permanent_masonry"]
    assert all(r["synthetic"] == "True" and document.document_id in r["source"] for r in rows)
    assert [r["floor_area_m2"] for r in rows] == [90] * 4 + [""]
    assert all(r["cost_per_m2_kes"] == "" for r in rows)  # missing stays missing; TIV never from area x cost


def test_location_comes_from_the_text_or_the_gazetteer_with_its_origin(assembled):
    kayole, masonry = assembled.rows[0], assembled.rows[4]
    assert (kayole["lat"], kayole["lon"]) == (-1.2667475, 36.9193623)
    assert (masonry["lat"], masonry["lon"]) == (-1.2822, 36.8634)
    assert assembled.origins[0]["lat_lon"].startswith("gazetteer:") and "[A]" in assembled.origins[0]["lat_lon"]
    assert assembled.origins[4]["lat_lon"].startswith("ai, verified quote")
    assert "map-masonry [A]" in assembled.origins[4]["housing_class"]
    assert "confirmed by Reviewer" in assembled.origins[4]["housing_class"]


def test_excluded_item_and_unmodelled_terms(assembled):
    assert assembled.excluded_items == ("item-3",)
    assert not any("-03-" in r["loc_id"] for r in assembled.rows)
    # Terms are kept for every item, excluded ones included, and never become engine columns.
    assert [(t["item_id"], t["item_status"], t["kind"]) for t in assembled.unmodelled_terms] == [
        ("item-2", "included", "deductible"), ("item-3", "excluded", "business_interruption")]
    assert all(set(r) == set(REQUIRED_COLUMNS) - set(c for c in REQUIRED_COLUMNS if c.startswith("hazard_score_"))
               for r in assembled.rows)


def test_a_total_is_never_split_only_a_human_value_is_used(case, gazetteer):
    entered = Correction("item-3", "tiv_kes_per_building", 800_000, "per-building schedule from the broker")
    approval = approval_for(case, gazetteer, acknowledged=ITEM2_FLAGS + ITEM3_FLAGS, excluded_items=(),
                            corrections=[entered])
    result = assemble(*case, approval, gazetteer)
    item3 = [r for r in result.rows if "-03-" in r["loc_id"]]
    assert len(item3) == 12 and all(r["tiv_kes"] == 800_000 for r in item3)  # never 9,600,000 / 12
    assert all(o["tiv_kes"].startswith("human:") for o in result.origins if o["item_id"] == "item-3")


def test_human_corrections_take_precedence_and_are_labelled(case, gazetteer):
    corrections = [Correction("item-1", "lat", -1.27, "surveyed"), Correction("item-1", "lon", 36.91, "surveyed"),
                   Correction("item-1", "building_count", 2, "two of the four are insured")]
    result = assemble(*case, approval_for(case, gazetteer, corrections=corrections), gazetteer)
    item1 = [r for r in result.rows if "-01-" in r["loc_id"]]
    assert len(item1) == 2 and (item1[0]["lat"], item1[0]["lon"]) == (-1.27, 36.91)
    assert result.origins[0]["lat_lon"] == "human: surveyed"
    assert result.origins[0]["building_count"] == "human: two of the four are insured"


def test_assembly_refuses_anything_but_a_current_approval(case, gazetteer):
    rejected = approval_for(case, gazetteer, decision="reject", reason="not confirmed")
    with pytest.raises(ApprovalError):
        assemble(*case, rejected, gazetteer)


def test_written_account_csv_passes_checkpoint_1_after_enrichment(assembled, tmp_path):
    rasterio_needed = pytest.importorskip("rasterio")  # noqa: F841
    from loss_engine.hazard_lookup import HazardRasters
    rows = enrich(assembled.rows, HazardRasters.open(DATA))
    path = write_exposure(rows, tmp_path / "account.csv")
    exposure = load_and_validate_exposure(path)
    assert exposure.row_count == 5 and exposure.total_tiv == 4 * 6_500_000 + 3_200_000
    with open(path, encoding="utf-8", newline="") as handle:
        assert next(csv.reader(handle)) == list(REQUIRED_COLUMNS)


def test_enrichment_failure_names_the_building(assembled):
    pytest.importorskip("rasterio")
    from loss_engine.hazard_lookup import HazardRasters
    outside = [{**assembled.rows[0], "lat": 0.5, "lon": 30.0}]
    with pytest.raises(AssemblyError, match=assembled.rows[0]["loc_id"] + ".*out_of_bounds"):
        enrich(outside, HazardRasters.open(DATA))


def test_writers_never_overwrite(assembled, tmp_path):
    rows = [{**r, **{c: 0.0 for c in REQUIRED_COLUMNS if c.startswith("hazard_score_")}} for r in assembled.rows]
    write_exposure(rows, tmp_path / "a.csv")
    with pytest.raises(FileExistsError):
        write_exposure(rows, tmp_path / "a.csv")
    write_combined(BASE, rows, tmp_path / "b.csv")
    with pytest.raises(FileExistsError):
        write_combined(BASE, rows, tmp_path / "b.csv")


def test_combined_file_keeps_the_supplied_rows_byte_for_byte(assembled, tmp_path):
    rows = [{**r, **{c: 0.0 for c in REQUIRED_COLUMNS if c.startswith("hazard_score_")}} for r in assembled.rows]
    combined = write_combined(BASE, rows, tmp_path / "combined.csv").read_bytes().splitlines()
    supplied = BASE.read_bytes().splitlines()
    assert combined[:len(supplied)] == supplied
    assert len(combined) == len(supplied) + 5


def test_combined_refuses_a_loc_id_already_in_the_portfolio(assembled, tmp_path):
    clash = [{**assembled.rows[0], "loc_id": "NBO-0000",
              **{c: 0.0 for c in REQUIRED_COLUMNS if c.startswith("hazard_score_")}}]
    with pytest.raises(AssemblyError, match="NBO-0000"):
        write_combined(BASE, clash, tmp_path / "c.csv")
