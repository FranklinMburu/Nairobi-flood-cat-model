"""Shared test helpers: real exposure rows and reportlab-generated PDFs (no binary fixtures)."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
EXPOSURE_CSV = REPO_ROOT / "data" / "exposure_nairobi_with_hazard.csv"


def exposure_rows(n: int = 6) -> list[list[str]]:
    """Header + n rows from the supplied dataset, with a short source so PDF cells do not wrap."""
    with EXPOSURE_CSV.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1 : n + 1]
    src = header.index("source")
    for r in body:
        r[src] = "test portfolio"
    return [header] + body


def rows_to_csv_bytes(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode("utf-8")


def make_pdf(
    tables: list[list[list[str]]],
    *,
    page_break_between: bool = True,
    repeat_rows: int = 0,
    font_size: int = 6,
) -> bytes:
    """Build a PDF containing the given tables (each a list of rows, header first)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.platypus import PageBreak, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A3))
    flow = []
    for i, data in enumerate(tables):
        t = Table(data, repeatRows=repeat_rows)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ]))
        flow.append(t)
        if i < len(tables) - 1:
            flow.append(PageBreak() if page_break_between else Spacer(1, 40))
    doc.build(flow)
    return buf.getvalue()


def make_text_pdf(text: str = "No table here, only a sentence.") -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(100, 750, text)
    c.save()
    return buf.getvalue()


@pytest.fixture
def pdf_path(tmp_path):
    def _write(data: bytes, name: str = "t.pdf") -> Path:
        p = tmp_path / name
        p.write_bytes(data)
        return p
    return _write
