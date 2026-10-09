"""JSON API over the Checkpoint 8 workflow, reference model runs and the ingest service.

Thin adapters only. Extraction, verification, approval rules, exposure
assembly, hazard lookup and every loss come from loss_engine; this module
reads requests, calls it, saves its records (store.py) and returns them.
All endpoints need a signed-in session; POSTs also need the CSRF token.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import secrets
import urllib.error
import urllib.request
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from loss_engine.ai_providers import GeminiProvider, ReplayProvider
from loss_engine.ai_records import AIExtractionRecord, read_record
from loss_engine.approval import ApprovalError, Correction, decide
from loss_engine.config import default_config
from loss_engine.ep_curve import default_return_periods
from loss_engine.exposure_assembly import AssemblyError
from loss_engine.hazard_lookup import HazardLookupError
from loss_engine.run_record import run_model, write_run
from loss_engine.workflow import extract_and_verify, run_approved, submission_from_upload

from . import store

logger = logging.getLogger("workflow_api")

MAX_TEXT_BYTES = 1024 * 1024
MAX_INGEST_BYTES = 10 * 1024 * 1024
BASELINE_CSV = "exposure_nairobi_with_hazard.csv"


def signed_in(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Authentication required."}, status=401)
        return view(request, *args, **kwargs)
    return wrapped


def error(message: str, status: int, **extra) -> JsonResponse:
    return JsonResponse({"error": message, **extra}, status=status)


def read_json(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


# --- Capabilities --------------------------------------------------------------------


def _replay_record() -> AIExtractionRecord | None:
    path = Path(settings.WORKFLOW_REPLAY_RECORD)
    return read_record(path, AIExtractionRecord) if path.is_file() else None


def _gemini_model() -> str | None:
    model = os.environ.get("GEMINI_MODEL", "").strip()
    return model or None


@require_GET
@signed_in
def capabilities(_request):
    """What this server can do right now. Credentials are reported as present or absent, never shown."""
    model = _gemini_model()
    return JsonResponse({
        "replay_example": _replay_record() is not None and Path(settings.WORKFLOW_EXAMPLE_TEXT).is_file(),
        "live_ai": {"provider": "gemini", "model": model,
                    "configured": bool(model and os.environ.get("GEMINI_API_KEY"))},
        "rasters": importlib.util.find_spec("rasterio") is not None,
        "ingest": {"configured": bool(os.environ.get("ADAPTER_API_KEY")), "url": settings.INGEST_API_URL},
    })


@require_GET
@signed_in
def example(_request):
    path = Path(settings.WORKFLOW_EXAMPLE_TEXT)
    if not path.is_file():
        return error("The bundled example submission is not available.", 404)
    return JsonResponse({"filename": path.name, "text": path.read_bytes().decode("utf-8"),
                         "note": "Bundled demonstration submission. Its AI response is a hand-written replay."})


# --- Submissions ---------------------------------------------------------------------


def _status(stage, consistent: bool, decisions: list) -> str:
    if not consistent:
        return "integrity_error"
    if stage.status != "awaiting_approval":
        return stage.status  # failed (provider) or rejected (verifier)
    if not decisions:
        return "awaiting_approval"
    latest = decisions[-1]
    if latest["decision"] == "reject":
        return "rejected_by_reviewer"
    if latest["decision"] == "request_review":
        return "review_requested"
    if not latest["current"]:
        return "stale_approval"
    return "approved"


def _candidate(record):
    if record.response_text is None:
        return None
    try:
        return json.loads(record.response_text)
    except ValueError:
        return None


def _detail(extraction_id: str, *, full: bool = True) -> dict:
    stage, consistent = store.load_submission(extraction_id)
    decisions = [store.approval_summary(a, stage) for a in store.decisions(extraction_id)]
    status = _status(stage, consistent, decisions)
    run = None
    latest = decisions[-1] if decisions else None
    if latest and latest["decision"] == "approve":
        run = store.read_run(extraction_id, latest["approval_id"])
        if run is not None and status == "approved":
            status = "completed"
    document, record = stage.document, stage.record
    summary = {
        "id": extraction_id,
        "status": status,
        "document": {"document_id": document.document_id, "sha256": document.sha256, "origin": document.origin,
                     "received_at": document.received_at, "metadata": dict(document.metadata)},
        "extraction": {"extraction_id": record.extraction_id, "provider": record.provider, "model": record.model,
                       "execution_mode": record.execution_mode, "replay_of": record.replay_of,
                       "status": record.status, "error": record.error, "created_at": record.created_at},
        "verification_outcome": stage.report.outcome,
        "decision_count": len(decisions),
    }
    if not full:
        return summary
    return {
        **summary,
        "document": {**summary["document"], "text": document.text},
        "extraction": {**summary["extraction"], "prompt_version": record.prompt_version,
                       "prompt_sha256": record.prompt_sha256, "schema_version": record.schema_version,
                       "schema_sha256": record.schema_sha256, "response_sha256": record.response_sha256,
                       "stop_reason": record.stop_reason, "request_id": record.request_id},
        "candidate": _candidate(record),
        "verification": stage.report.to_dict(),
        "verification_consistent": consistent,
        "requirements": {"approvable": stage.requirements.approvable,
                         "acknowledge": list(stage.requirements.acknowledge),
                         "unresolved": {k: list(v) for k, v in stage.requirements.unresolved.items()},
                         "reason": stage.requirements.reason},
        "decisions": decisions,
        "run": run,
    }


def _provider_for(document):
    recorded = _replay_record()
    if recorded is not None and recorded.document_hashes == (document.sha256,):
        return ReplayProvider(recorded)
    model = _gemini_model()
    return GeminiProvider(model) if model else None


def _submission_input(request):
    """(text, original filename, original-file SHA-256) from the request, or a JsonResponse error."""
    if request.content_type == "application/json":
        data = read_json(request)
        if data is None:
            return error("Invalid JSON.", 400)
        if data.get("source") == "example":
            path = Path(settings.WORKFLOW_EXAMPLE_TEXT)
            raw = path.read_bytes()
            return raw.decode("utf-8"), path.name, hashlib.sha256(raw).hexdigest()
        text = data.get("text")
        if not isinstance(text, str):
            return error("Send a file, a text field, or source 'example'.", 400)
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            return error("The text exceeds the 1 MB limit.", 413)
        return text, "pasted text", None
    upload = request.FILES.get("file")
    if upload is not None:
        if upload.size > MAX_TEXT_BYTES:
            return error("The file exceeds the 1 MB limit.", 413)
        raw = upload.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return error("Only UTF-8 text files can be submitted here. Structured files (CSV, XLSX, PDF tables, "
                         "GeoJSON) go through the file upload service.", 415)
        return text, Path(upload.name).name[:200], hashlib.sha256(raw).hexdigest()
    text = request.POST.get("text")
    if isinstance(text, str):
        return text, "pasted text", None
    return error("Send a file, a text field, or source 'example'.", 400)


@require_http_methods(["GET", "POST"])
@signed_in
def submissions(request):
    if request.method == "GET":
        items = []
        for extraction_id in store.list_submissions():
            try:
                items.append(_detail(extraction_id, full=False))
            except Exception:  # an unreadable submission is reported, not hidden
                logger.exception("could not load submission %s", extraction_id)
                items.append({"id": extraction_id, "status": "unreadable"})
        return JsonResponse({"submissions": items})

    parsed = _submission_input(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    text, filename, file_sha = parsed
    if not text.strip():
        return error("The submission is empty.", 400)
    document = submission_from_upload(text, original_filename=filename, original_file_sha256=file_sha,
                                      uploaded_by=request.user.email)
    provider = _provider_for(document)
    if provider is None:
        return error("No AI provider is configured on this server (GEMINI_MODEL and GEMINI_API_KEY). "
                     "Only the bundled example can be processed, by replay.", 503)
    stage = extract_and_verify(document, provider, store.gazetteer())
    extraction_id = store.save_submission(stage)
    return JsonResponse(_detail(extraction_id), status=201)


@require_GET
@signed_in
def submission_detail(_request, extraction_id):
    try:
        return JsonResponse(_detail(extraction_id))
    except store.NotFound:
        return error("Submission not found.", 404)


def _corrections(raw):
    if not isinstance(raw, list):
        raise ApprovalError("corrections must be a list")
    found = []
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"item_id", "field", "value", "reason"}:
            raise ApprovalError("each correction needs item_id, field, value and reason")
        found.append(Correction(item.get("item_id"), item.get("field"), item.get("value"), item.get("reason")))
    return found


def _strings(raw, name):
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ApprovalError(f"{name} must be a list of strings")
    return raw


@require_POST
@signed_in
def decision(request, extraction_id):
    data = read_json(request)
    if data is None:
        return error("Invalid JSON.", 400)
    try:
        stage, consistent = store.load_submission(extraction_id)
    except store.NotFound:
        return error("Submission not found.", 404)
    if not consistent:
        return error("The verification report no longer matches the saved one; no decision can be recorded.", 409)
    user = request.user
    try:
        approval = decide(
            data.get("decision"), document=stage.document, record=stage.record, report=stage.report,
            approver=f"{user.name} <{user.email}>", gazetteer=store.gazetteer(),
            reason=(data.get("reason") or None), acknowledged=_strings(data.get("acknowledged", []), "acknowledged"),
            corrections=_corrections(data.get("corrections", [])),
            excluded_items=_strings(data.get("excluded_items", []), "excluded_items"),
        )
    except ApprovalError as exc:
        return error(str(exc), 400)
    store.save_decision(extraction_id, approval)
    return JsonResponse(_detail(extraction_id), status=201)


@require_POST
@signed_in
def run(request, extraction_id):
    """Run the model for the latest decision, which must be a current approval. Synchronous (a few seconds)."""
    try:
        stage, consistent = store.load_submission(extraction_id)
        approvals = store.decisions(extraction_id)
    except store.NotFound:
        return error("Submission not found.", 404)
    if not consistent:
        return error("The verification report no longer matches the saved one; the model will not run.", 409)
    if not approvals or approvals[-1].decision != "approve":
        return error("A current approval is required before the model can run.", 409)
    approval = approvals[-1]
    final = store.run_dir(extraction_id, approval.approval_id)
    if (final / "workflow_record.json").is_file():
        return error("This approval has already been run.", 409, submission=_detail(extraction_id))
    partial = final.parent / f".{approval.approval_id}.partial-{secrets.token_hex(4)}"
    try:
        run_approved(stage, approval, config=default_config(), mapping=default_return_periods(),
                     data_dir=store.data_dir(), gazetteer=store.gazetteer(), output_dir=partial)
    except ApprovalError as exc:
        return error(str(exc), 409)
    except (AssemblyError, HazardLookupError) as exc:
        return error(str(exc), 422)
    except ImportError:
        return error("The hazard rasters cannot be read: the 'geo' extra (rasterio) is not installed.", 503)
    except RuntimeError as exc:  # an engine run stopped on a failed rule
        return error(str(exc), 422)
    partial.rename(final)
    return JsonResponse(_detail(extraction_id), status=201)


# --- Reference portfolio runs -------------------------------------------------------------


@require_http_methods(["GET", "POST"])
@signed_in
def reference_runs(request):
    if request.method == "GET":
        return JsonResponse({"runs": store.list_reference_runs()})
    result = run_model(store.data_dir() / BASELINE_CSV, default_config(), default_return_periods())
    write_run(result, store.reference_runs_dir())
    record = store.read_reference_run(result.record.run_id)
    return JsonResponse(record, status=201 if result.record.status == "completed" else 422)


@require_GET
@signed_in
def reference_run_detail(_request, run_id):
    try:
        return JsonResponse(store.read_reference_run(run_id))
    except store.NotFound:
        return error("Run not found.", 404)


# --- File upload service (structured files) --------------------------------------------


def _multipart(fields: dict, file_field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = "----dira" + secrets.token_hex(12)
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="upload"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


@require_POST
@signed_in
def ingest(request, action):
    """Forward an uploaded file to the upload service (api/main.py) with the server's key.

    The service owns parsing, adaptation and its own engine call; this view
    only keeps the key on the server and adds the original file's identity.
    """
    if action not in ("adapt", "run"):
        return error("Unknown action.", 404)
    key = os.environ.get("ADAPTER_API_KEY", "")
    if not key:
        return error("The file upload service is not configured on this server (ADAPTER_API_KEY).", 503)
    upload = request.FILES.get("file")
    if upload is None:
        return error("Choose a file to upload.", 400)
    if upload.size > MAX_INGEST_BYTES:
        return error("File exceeds the 10 MB limit", 413)
    content = upload.read()
    fields = {k: request.POST[k] for k in ("synthetic", "fx_rate", "accept_uncertain") if request.POST.get(k)}
    body, content_type = _multipart(fields, "file", upload.name, content)
    http = urllib.request.Request(f"{settings.INGEST_API_URL.rstrip('/')}/{action}", data=body, method="POST",
                                  headers={"Content-Type": content_type, "X-API-Key": key})
    try:
        with urllib.request.urlopen(http, timeout=120) as response:
            status, payload = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return error("The file upload service is not reachable.", 503)
    try:
        body_json = json.loads(payload)
    except ValueError:
        return error("The file upload service returned an unreadable response.", 502)
    if not isinstance(body_json, dict):
        return error("The file upload service returned an unexpected response.", 502)
    body_json["original_file"] = {"filename": Path(upload.name).name[:200],
                                  "sha256": hashlib.sha256(content).hexdigest(),
                                  "uploaded_by": request.user.email}
    if status == 401:
        return error("The upload service refused this server's key.", 502)
    return JsonResponse(body_json, status=status)
