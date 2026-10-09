"""FastAPI ingest API: upload an exposure file (PDF table, CSV, XLSX, GeoJSON),
adapt it to the canonical schema, and optionally run the deterministic loss engine.

The API layer never computes or alters a loss value: /run only calls
loss_engine.run_record.run_model on the adapted CSV and returns its outputs.

Start:  ADAPTER_API_KEY=<secret> uvicorn api.main:app   (from the repo root)
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import secrets
import tempfile
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Security, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader

from adapter.config import AdapterConfig
from adapter.pipeline import AdaptationResult, adapt_file

logger = logging.getLogger("nairobi_api")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_BUILDING_ROWS = 1000
REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    key = os.environ.get("ADAPTER_API_KEY", "")
    if not key:
        raise RuntimeError("ADAPTER_API_KEY must be set (any long random string you choose)")
    app.state.api_key = key
    yield


app = FastAPI(title="Nairobi Flood Model ingest API", version="1.0.0", lifespan=lifespan)


def require_key(request: Request, key: str | None = Security(api_key_header)) -> None:
    expected: str = request.app.state.api_key
    if not key or not secrets.compare_digest(key.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


# --- Upload handling -------------------------------------------------------------


def _read_upload(upload: UploadFile) -> bytes:
    """Read the body in chunks and stop at the size cap."""
    chunks, total = [], 0
    while True:
        chunk = upload.file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds the 10 MB limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _sniff_format(raw: bytes) -> str:
    """Detect the type from content only; the client filename and Content-Type are ignored."""
    if raw.startswith(b"%PDF-"):
        return "pdf"
    if raw.startswith(b"PK"):
        return "xlsx"
    if raw.lstrip().startswith(b"{"):
        return "geojson"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "unknown"
    return "unknown" if "\x00" in text else "csv"


def _check_xlsx(raw: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = set(zf.namelist())
            if "xl/workbook.xml" not in names:
                raise ValueError("not a workbook")
            if sum(i.file_size for i in zf.infolist()) > MAX_XLSX_UNCOMPRESSED_BYTES:
                raise HTTPException(status_code=413, detail="Workbook is too large when unpacked")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=415, detail="Not a valid XLSX workbook")


def _parse_form(synthetic: str | None, fx_rate: float | None) -> tuple[bool | None, float | None]:
    synthetic_value = None
    if synthetic is not None and synthetic.strip() != "":
        s = synthetic.strip().lower()
        if s not in ("true", "false"):
            raise HTTPException(status_code=422, detail="synthetic must be 'true' or 'false'")
        synthetic_value = s == "true"
    if fx_rate is not None and not (math.isfinite(fx_rate) and 0 < fx_rate <= 1000):
        raise HTTPException(status_code=422, detail="fx_rate must be greater than 0 and at most 1000")
    return synthetic_value, fx_rate


def _scrub_report(report: dict, label: str) -> dict:
    """Remove server paths from the adaptation report."""
    report["original_file"]["path"] = label
    report["adapted_file"]["path"] = ""
    report.get("config", {}).pop("data_dir", None)
    return report


def _run_adapter(
    raw: bytes, fmt: str, synthetic: bool | None, fx_rate: float | None,
    accept_uncertain: bool, workdir: Path,
) -> tuple[AdaptationResult, dict]:
    input_path = workdir / f"input.{fmt}"  # server-generated name
    input_path.write_bytes(raw)
    data_dir = Path(os.environ.get("DATA_DIR", REPO_ROOT / "data")).resolve()
    config = AdapterConfig.load(fx_rate=fx_rate, data_dir=data_dir)
    try:
        result = adapt_file(
            input_path=input_path,
            output_dir=workdir / "out",
            config=config,
            synthetic=synthetic,
            accept_uncertain=accept_uncertain,
        )
    except ValueError as exc:  # unreadable file / no table / limits: the user's file is the problem
        raise HTTPException(status_code=422, detail=str(exc)[:300])
    label = f"upload.{fmt}"
    return result, _scrub_report(result.report.to_dict(), label)


def _handle(
    request: Request, file: UploadFile, synthetic: str | None, fx_rate: float | None,
    accept_uncertain: bool, run: bool,
) -> JSONResponse:
    started = time.monotonic()
    synthetic_value, fx_value = _parse_form(synthetic, fx_rate)
    raw = _read_upload(file)
    fmt = _sniff_format(raw)
    if fmt == "unknown":
        raise HTTPException(status_code=415, detail="Unsupported file. Allowed: PDF, CSV, XLSX, GeoJSON")
    if fmt == "xlsx":
        _check_xlsx(raw)
    if fmt == "geojson":
        try:
            json.loads(raw)
        except ValueError:
            raise HTTPException(status_code=415, detail="Invalid GeoJSON")

    sha = hashlib.sha256(raw).hexdigest()
    body: dict
    status_code = 200
    try:
        with tempfile.TemporaryDirectory(prefix="nairobi-") as tmp:
            result, report = _run_adapter(raw, fmt, synthetic_value, fx_value, accept_uncertain, Path(tmp))
            adapted_csv = None
            if result.adapted_path and result.adapted_path.exists():
                adapted_csv = result.adapted_path.read_text(encoding="utf-8")
            if not run:
                body = {"status": result.status, "adapted_csv": adapted_csv, "report": report}
                status_code = 422 if result.status == "refused" else 200
            elif result.status == "refused":
                body = {"adapter_status": "refused", "run_status": "not_run", "adaptation_report": report}
                status_code = 422
            elif result.status == "needs_confirmation":
                body = {"adapter_status": result.status, "run_status": "not_run", "adaptation_report": report,
                        "message": "Adapter needs confirmation; resend with accept_uncertain=true to run."}
            else:
                status_code, run_part = _run_engine(result.adapted_path)
                body = {"adapter_status": result.status, "adaptation_report": report, **run_part}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error (sha256=%s)", sha)
        raise HTTPException(status_code=500, detail="Internal server error")
    logger.info("request sha256=%s fmt=%s http=%s seconds=%.2f", sha, fmt, status_code, time.monotonic() - started)
    return JSONResponse(status_code=status_code, content=body)


def _records(df) -> list[dict]:
    return json.loads(df.to_json(orient="records"))


def _run_engine(adapted_path: Path) -> tuple[int, dict]:
    """Call the deterministic engine on the adapted CSV. No loss value is computed here."""
    from loss_engine.config import default_config
    from loss_engine.ep_curve import default_return_periods
    from loss_engine.run_record import run_model

    result = run_model(exposure_path=adapted_path, config=default_config(), mapping=default_return_periods())
    record = json.loads(result.record.to_json())
    if result.outputs is None:
        return 422, {
            "run_status": record["status"], "failed_at": record["failed_at"],
            "failure": record["failure"], "validation_results": record["validation_results"],
        }
    out = result.outputs
    return 200, {
        "run_status": record["status"], "run_id": record["run_id"],
        "validation_results": record["validation_results"],
        "total_tiv_kes": record["total_tiv_kes"], "row_count": record["row_count"],
        "tier_summary": _records(out.tier_summary),
        "class_summary": _records(out.class_summary),
        "ep_points": _records(out.ep_points),
        "building_results": _records(out.building_results.head(MAX_BUILDING_ROWS)),
        "building_results_truncated": len(out.building_results) > MAX_BUILDING_ROWS,
    }


@app.post("/adapt", dependencies=[Depends(require_key)])
def adapt_endpoint(
    request: Request,
    file: UploadFile = File(...),
    synthetic: str | None = Form(None),
    fx_rate: float | None = Form(None),
    accept_uncertain: bool = Form(False),
) -> JSONResponse:
    """Adapt an exposure file to the canonical schema (no model run)."""
    return _handle(request, file, synthetic, fx_rate, accept_uncertain, run=False)


@app.post("/run", dependencies=[Depends(require_key)])
def run_endpoint(
    request: Request,
    file: UploadFile = File(...),
    synthetic: str | None = Form(None),
    fx_rate: float | None = Form(None),
    accept_uncertain: bool = Form(False),
) -> JSONResponse:
    """Adapt the file, then run the deterministic loss engine on it."""
    return _handle(request, file, synthetic, fx_rate, accept_uncertain, run=True)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
