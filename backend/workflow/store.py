"""Files behind the workflow API. Every decision and calculation is made in loss_engine.

Layout, under settings.WORKFLOW_STORE_DIR (default outputs/workflow):

    submissions/<extraction_id>/source_document.json     the text exactly as received
    submissions/<extraction_id>/ai_extraction.json       the AI (or replay) record
    submissions/<extraction_id>/verification_report.json the report when it was first made
    submissions/<extraction_id>/decisions/<approval_id>.json
    submissions/<extraction_id>/runs/<approval_id>/      run_approved output (workflow_record.json, ...)
    reference_runs/<run_id>/                             write_run output for the supplied portfolio

Files are written once and never overwritten. The verification report is
recomputed from the document and the record each time a submission is
loaded; if it no longer matches the saved one, the submission is flagged.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

from django.conf import settings

from loss_engine.ai_records import AIExtractionRecord, SourceDocument, read_record, write_record
from loss_engine.approval import ApprovalRecord, approval_file_sha256, candidate_sha256, report_sha256
from loss_engine.exposure_assembly import load_gazetteer
from loss_engine.run_record import read_run_record
from loss_engine.workflow import ExtractionStage, load_stage

SAFE_ID = re.compile(r"^(ext|apr|run)-\d{8}T\d{6}Z-[0-9a-f]{8}$")


class NotFound(LookupError):
    pass


def root() -> Path:
    return Path(settings.WORKFLOW_STORE_DIR)


def data_dir() -> Path:
    return Path(settings.MODEL_DATA_DIR)


_GAZETTEER = None


def gazetteer():
    global _GAZETTEER
    if _GAZETTEER is None:
        _GAZETTEER = load_gazetteer(data_dir())
    return _GAZETTEER


def _submission_dir(extraction_id: str) -> Path:
    if not SAFE_ID.match(extraction_id or "") or not extraction_id.startswith("ext-"):
        raise NotFound(extraction_id)
    path = root() / "submissions" / extraction_id
    if not path.is_dir():
        raise NotFound(extraction_id)
    return path


def save_submission(stage: ExtractionStage) -> str:
    path = root() / "submissions" / stage.record.extraction_id
    path.mkdir(parents=True, exist_ok=False)
    write_record(stage.document, path, "source_document.json")
    write_record(stage.record, path, "ai_extraction.json")
    with open(path / "verification_report.json", "x", encoding="utf-8") as handle:
        handle.write(stage.report.to_json())
    return stage.record.extraction_id


def load_submission(extraction_id: str) -> tuple[ExtractionStage, bool]:
    """The stage, and whether the recomputed report still equals the saved one."""
    path = _submission_dir(extraction_id)
    document = read_record(path / "source_document.json", SourceDocument)
    record = read_record(path / "ai_extraction.json", AIExtractionRecord)
    stage = load_stage(document, record, gazetteer())
    saved = (path / "verification_report.json").read_text(encoding="utf-8")
    return stage, saved == stage.report.to_json()


def list_submissions() -> list[str]:
    folder = root() / "submissions"
    if not folder.is_dir():
        return []
    return sorted((p.name for p in folder.iterdir() if p.is_dir() and SAFE_ID.match(p.name)), reverse=True)


def save_decision(extraction_id: str, approval: ApprovalRecord) -> None:
    folder = _submission_dir(extraction_id) / "decisions"
    folder.mkdir(exist_ok=True)
    with open(folder / f"{approval.approval_id}.json", "x", encoding="utf-8") as handle:
        handle.write(approval.to_json())


def decisions(extraction_id: str) -> list[ApprovalRecord]:
    """Every decision on the submission, oldest first."""
    folder = _submission_dir(extraction_id) / "decisions"
    if not folder.is_dir():
        return []
    found = [ApprovalRecord.from_dict(json.loads(p.read_text(encoding="utf-8"))) for p in folder.glob("apr-*.json")]
    return sorted(found, key=lambda a: (a.decided_at, a.approval_id))


def run_dir(extraction_id: str, approval_id: str) -> Path:
    if not SAFE_ID.match(approval_id or "") or not approval_id.startswith("apr-"):
        raise NotFound(approval_id)
    return _submission_dir(extraction_id) / "runs" / approval_id


def _csv_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_run(extraction_id: str, approval_id: str) -> dict | None:
    """A completed workflow run as saved on disk, or None."""
    out = run_dir(extraction_id, approval_id)
    if not (out / "workflow_record.json").is_file():
        return None
    return {
        "workflow_record": json.loads((out / "workflow_record.json").read_text(encoding="utf-8")),
        "comparison": _csv_rows(out / "comparison.csv"),
        "ep_comparison": _csv_rows(out / "ep_comparison.csv"),
        "account_exposure": _csv_rows(out / "account_exposure.csv"),
    }


def approval_summary(approval: ApprovalRecord, stage: ExtractionStage) -> dict:
    current = (approval.document_sha256 == stage.document.sha256
               and approval.response_sha256 == stage.record.response_sha256
               and approval.candidate_sha256 == (candidate_sha256(stage.record) if stage.record.response_text else "")
               and approval.verification_sha256 == report_sha256(stage.report))
    return {**approval.to_dict(), "approval_sha256": approval_file_sha256(approval), "current": current}


def reference_runs_dir() -> Path:
    return root() / "reference_runs"


def list_reference_runs() -> list[dict]:
    folder = reference_runs_dir()
    if not folder.is_dir():
        return []
    runs = []
    for path in sorted(folder.glob("run-*/run_record.json"), reverse=True):
        record = read_run_record(path)
        runs.append({**record.to_dict(), "record_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return runs


def read_reference_run(run_id: str) -> dict:
    if not SAFE_ID.match(run_id or "") or not run_id.startswith("run-"):
        raise NotFound(run_id)
    path = reference_runs_dir() / run_id / "run_record.json"
    if not path.is_file():
        raise NotFound(run_id)
    record = read_run_record(path)
    return {**record.to_dict(), "record_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
