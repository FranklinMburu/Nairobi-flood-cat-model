"""PDF table extraction and its use through the adapter pipeline."""

from __future__ import annotations

import pytest

from adapter.pdf_tables import MAX_PDF_PAGES, NoTableFound, PdfLimitExceeded, PdfUnreadable, extract_table
from adapter.pipeline import adapt_file
from adapter.profile import profile_file, read_file
from conftest import exposure_rows, make_pdf, make_text_pdf


def test_clean_table(pdf_path):
    rows = exposure_rows(4)
    df = extract_table(pdf_path(make_pdf([rows])))
    assert list(df.columns) == rows[0]
    assert len(df) == 4
    assert df.iloc[0]["loc_id"] == "NBO-0000"
    assert all(str(t) in ("str", "string", "object") or "str" in str(t).lower() for t in df.dtypes)


def test_synonym_headers_mapped_and_recorded(pdf_path):
    data = [["Location ID", "Latitude", "Longitude", "Building Type", "Sum Insured"], ["N1", "-1.3", "36.8", "concrete_rcc", "5"]]
    df = extract_table(pdf_path(make_pdf([data])))
    assert list(df.columns) == ["loc_id", "lat", "lon", "housing_class", "tiv_kes"]
    notes = " ".join(df.attrs["notes"])
    assert "'tiv' " not in notes and "sum_insured" in notes and "tiv_kes" in notes


def test_multiline_and_symbol_headers_cleaned(pdf_path):
    data = [["Loc\nID", "Cost per m2 (KES)", "Lat/Long"], ["N1", "100", "x"]]
    df = extract_table(pdf_path(make_pdf([data])))
    assert list(df.columns) == ["loc_id", "cost_per_m2_kes", "lat_long"]


def test_blank_and_duplicate_headers(pdf_path):
    data = [["loc_id", "", "lat", "lat"], ["N1", "x", "1", "2"]]
    df = extract_table(pdf_path(make_pdf([data])))
    assert list(df.columns) == ["loc_id", "col_1", "lat", "lat_2"]


def test_empty_cell_is_empty_string_not_none_or_nan(pdf_path):
    data = [["loc_id", "lat", "lon"], ["N1", "", "36.8"]]
    df = extract_table(pdf_path(make_pdf([data])))
    assert df.iloc[0]["lat"] == ""


def test_synonym_collision_skipped(pdf_path):
    data = [["tiv", "tiv_kes", "loc_id"], ["1", "2", "N1"]]
    df = extract_table(pdf_path(make_pdf([data])))
    assert list(df.columns) == ["tiv", "tiv_kes", "loc_id"]
    assert any("target already present" in n for n in df.attrs["notes"])


def test_two_synonyms_for_same_target_only_first_applied(pdf_path):
    data = [["id", "ref", "lat"], ["1", "2", "3"]]
    df = extract_table(pdf_path(make_pdf([data])))
    assert list(df.columns) == ["loc_id", "ref", "lat"]


def test_usd_columns_never_mapped(pdf_path):
    data = [["loc_id", "tiv_usd"], ["N1", "10"]]
    df = extract_table(pdf_path(make_pdf([data])))
    assert "tiv_usd" in df.columns and "tiv_kes" not in df.columns


def test_largest_table_wins_over_cover_table(pdf_path):
    cover = [["item", "value"], ["Insured", "Acme Ltd"]]
    data = exposure_rows(5)
    df = extract_table(pdf_path(make_pdf([cover, data], page_break_between=False)))
    assert list(df.columns) == data[0] and len(df) == 5


def test_multipage_continuation_with_repeated_header(pdf_path):
    rows = [["loc_id", "lat", "lon"]] + [[f"N{i}", "-1.3", "36.8"] for i in range(150)]
    df = extract_table(pdf_path(make_pdf([rows], repeat_rows=1, font_size=12)))
    assert len(df) == 150 and df["loc_id"].is_unique


def test_multipage_continuation_without_header(pdf_path):
    rows = [["loc_id", "lat", "lon"]] + [[f"N{i}", "-1.3", "36.8"] for i in range(150)]
    df = extract_table(pdf_path(make_pdf([rows], repeat_rows=0, font_size=12)))
    assert len(df) == 150 and df["loc_id"].is_unique


def test_text_only_pdf_raises_no_table(pdf_path):
    with pytest.raises(NoTableFound):
        extract_table(pdf_path(make_text_pdf()))


def test_over_page_limit_raises_not_truncates(pdf_path):
    tables = [[["a", "b"], ["1", "2"]]] * (MAX_PDF_PAGES + 1)
    with pytest.raises(PdfLimitExceeded):
        extract_table(pdf_path(make_pdf(tables)))


def test_corrupt_pdf_raises_unreadable(pdf_path):
    with pytest.raises(PdfUnreadable):
        extract_table(pdf_path(b"%PDF-1.4 this is not really a pdf"))


def test_read_file_dispatches_pdf(pdf_path):
    df, fmt = read_file(pdf_path(make_pdf([exposure_rows(3)])))
    assert fmt == "pdf" and len(df) == 3


def test_profile_carries_notes_as_warnings(pdf_path):
    data = [["Latitude", "Longitude"], ["-1.3", "36.8"]]
    profile = profile_file(pdf_path(make_pdf([data])))
    assert profile.format == "pdf"
    assert any("mapped to 'lat'" in w for w in profile.warnings)


def test_pdf_through_adapter_is_accepted(pdf_path, tmp_path):
    rows = exposure_rows(6)
    result = adapt_file(pdf_path(make_pdf([rows])), tmp_path / "out")
    assert result.status == "accepted", result.report.refusal_reasons
    assert result.adapted_path.exists()
    assert result.report.original_file["profile"]["format"] == "pdf"


def test_pdf_with_synonym_headers_through_adapter(pdf_path, tmp_path):
    rows = exposure_rows(6)
    rename = {"loc_id": "Location ID", "lat": "Latitude", "lon": "Longitude", "housing_class": "Construction",
              "floor_area_m2": "Floor Area", "cost_per_m2_kes": "Rate per m2", "tiv_kes": "Sum Insured"}
    rows[0] = [rename.get(h, h) for h in rows[0]]
    result = adapt_file(pdf_path(make_pdf([rows])), tmp_path / "out")
    assert result.status == "accepted", result.report.refusal_reasons


def test_pdf_missing_required_field_is_refused_not_guessed(pdf_path, tmp_path):
    rows = exposure_rows(4)
    drop = rows[0].index("tiv_kes")
    rows = [[c for i, c in enumerate(r) if i != drop] for r in rows]
    result = adapt_file(pdf_path(make_pdf([rows])), tmp_path / "out")
    assert result.status == "refused"
    assert any("tiv_kes" in r for r in result.report.refusal_reasons)
