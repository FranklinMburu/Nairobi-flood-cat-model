"""PDF table extraction for the adapter.

Reads the largest table in a text-based PDF and returns it as an all-string
DataFrame that matches what the CSV reader produces. Scanned (image-only) PDFs
have no text layer, so they yield no tables and raise NoTableFound; there is
no OCR.

Header synonyms (PDF_SYNONYM_MAP) are applied to PDF headers ONLY. Every
rename is recorded in ``df.attrs["notes"]``; profile_file copies those notes
into the profile warnings. Known limitation: the adaptation report still lists
the resulting column mappings with method="exact".
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

MAX_PDF_PAGES = 50
MAX_TABLE_ROWS = 50_000


class NoTableFound(ValueError):
    """The PDF contains no extractable table (for example a scanned PDF)."""


class PdfUnreadable(ValueError):
    """The file starts like a PDF but cannot be parsed (corrupt or encrypted)."""


class PdfLimitExceeded(ValueError):
    """The PDF is larger or more complex than the configured limits."""


PDF_SYNONYM_MAP = {
    "latitude": "lat",
    "longitude": "lon",
    "long": "lon",
    "lng": "lon",
    "id": "loc_id",
    "location_id": "loc_id",
    "ref": "loc_id",
    "construction": "housing_class",
    "building_type": "housing_class",
    "housing_type": "housing_class",
    "class": "housing_class",
    "floor_area": "floor_area_m2",
    "area_m2": "floor_area_m2",
    "gfa": "floor_area_m2",
    "cost_per_m2": "cost_per_m2_kes",
    "rate_per_m2": "cost_per_m2_kes",
    "tiv": "tiv_kes",
    "sum_insured": "tiv_kes",
    "total_insured_value": "tiv_kes",
}


def _cell(value) -> str:
    """None -> "", everything else a stripped string with newlines collapsed."""
    if value is None:
        return ""
    return re.sub(r"\s*\n\s*", " ", str(value)).strip()


def _clean_headers(raw_headers: list[str]) -> tuple[list[str], list[str]]:
    """snake_case the headers; blank -> col_N; duplicates -> _2, _3. Returns (headers, notes)."""
    seen: set[str] = set()
    cleaned_headers: list[str] = []
    notes: list[str] = []
    for idx, raw in enumerate(raw_headers):
        base = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
        if not base:
            base = f"col_{idx}"
            notes.append(f"Blank header at position {idx} named '{base}'")
        name, suffix = base, 2
        while name in seen:
            name = f"{base}_{suffix}"
            suffix += 1
        if name != base:
            notes.append(f"Duplicate header '{base}' renamed '{name}'")
        seen.add(name)
        cleaned_headers.append(name)
    return cleaned_headers, notes


def _apply_synonyms(headers: list[str]) -> tuple[list[str], list[str]]:
    """Rename synonym headers to canonical names. Returns (headers, notes)."""
    out = list(headers)
    taken = set(out)
    notes: list[str] = []
    for i, name in enumerate(headers):
        target = PDF_SYNONYM_MAP.get(name)
        if target is None:
            continue
        if "usd" in name.split("_"):
            notes.append(f"Skipped synonym '{name}' -> '{target}': USD column")
        elif target in taken:
            notes.append(f"Skipped synonym '{name}' -> '{target}': target already present")
        else:
            out[i] = target
            taken.discard(name)
            taken.add(target)
            notes.append(f"Header '{name}' mapped to '{target}'")
    return out, notes


def _tables_per_page(path: Path) -> list[list[list[list[str]]]]:
    """Raw tables for every page: pages -> tables -> rows -> cleaned cell strings."""
    import pdfplumber  # lazy: importing adapter must work without the adapter extra

    try:
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages) > MAX_PDF_PAGES:
                raise PdfLimitExceeded(f"PDF has more than {MAX_PDF_PAGES} pages")
            pages = []
            for page in pdf.pages:
                tables = []
                for table in page.extract_tables() or []:
                    if table and table[0]:
                        ncols = len(table[0])
                        rows = [[_cell(c) for c in row] for row in table]
                        tables.append([(r + [""] * ncols)[:ncols] for r in rows])
                pages.append(tables)
            return pages
    except PdfLimitExceeded:
        raise
    except Exception as exc:  # pdfminer raises many types for corrupt/encrypted files
        raise PdfUnreadable("Could not read the PDF (corrupt or encrypted)") from exc


def _candidates(path: Path) -> list[dict]:
    """All tables in the PDF; tables continuing across consecutive pages are joined."""
    candidates: list[dict] = []
    current: dict | None = None
    for page_no, tables in enumerate(_tables_per_page(path)):
        for pos, rows in enumerate(tables):
            ncols = len(rows[0])
            continues = (
                pos == 0
                and current is not None
                and current["last_page"] == page_no - 1
                and current["at_page_end"]
                and current["ncols"] == ncols
            )
            if continues:
                current["rows"].extend(rows[1:] if rows[0] == current["header"] else rows)
            else:
                current = {"header": rows[0], "rows": rows[1:], "ncols": ncols}
                candidates.append(current)
            current["last_page"] = page_no
            current["at_page_end"] = pos == len(tables) - 1
            if len(current["rows"]) > MAX_TABLE_ROWS:
                raise PdfLimitExceeded(f"Table has more than {MAX_TABLE_ROWS} rows")
    return candidates


def extract_table(path: str | Path) -> pd.DataFrame:
    """Return the largest table (by rows x columns) in the PDF as an all-string DataFrame."""
    candidates = [c for c in _candidates(Path(path)) if c["rows"]]
    if not candidates:
        raise NoTableFound("No tables found in PDF")
    best = max(candidates, key=lambda c: (len(c["rows"]) + 1) * c["ncols"])

    headers, notes = _clean_headers(best["header"])
    headers, synonym_notes = _apply_synonyms(headers)
    df = pd.DataFrame(best["rows"], columns=headers, dtype=str)
    df.attrs["notes"] = notes + synonym_notes
    return df
