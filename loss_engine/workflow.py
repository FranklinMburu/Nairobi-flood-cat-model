"""End-to-end exposure workflow (Checkpoint 8): document -> AI -> verifier -> human -> engine.

Stage 1, `extract_and_verify`: a SourceDocument goes to an AI provider (or a
replay), the response is recorded and verified. Result: the record, the
verification report and what a reviewer must do. Nothing is approved here.

Stage 2, a human decides (approval.decide).

Stage 3, `run_approved`: only an approval that still matches its exact
candidate proceeds. The approved items become exposure rows, the five hazard
scores are sampled from the rasters, and the existing engine (run_record.run_model)
runs three times: the supplied portfolio (baseline), the new account alone,
and the portfolio with the account. The comparison is the difference between
two engine results; no loss is calculated here. A workflow record links every
identifier and hash, from the source document to the run records.

Upload boundary: an upload service hands over extracted text with
`submission_from_upload`; file parsing (PDF, Excel) stays with that service.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from .ai_providers import build_exposure_request, run_extraction
from .ai_records import AIExtractionRecord, AIProvider, SourceDocument, utc_timestamp, write_record
from .approval import ApprovalRecord, Requirements, approval_file_sha256, approval_requirements, check_approval
from .config import ModelConfig
from .ep_curve import ReturnPeriodMapping
from .exposure_assembly import AssembledExposure, assemble, enrich, write_combined, write_exposure
from .exposure_extraction import VerificationReport, verify_extraction
from .hazard_lookup import HazardRasters
from .run_record import ASSUMPTION_STATEMENTS, SYNTHETIC_PROXY_STATEMENT, RunResult, run_model, write_run

WORKFLOW_FORMAT = "workflow-record-1"
COMPARED = ("affected_buildings", "affected_tiv_kes", "portfolio_loss_kes")


def submission_from_upload(text: str, *, original_filename: str, original_file_sha256: str | None,
                           uploaded_by: str | None = None, now: datetime | None = None) -> SourceDocument:
    """The adapter for an upload service: text it has already extracted from a file, plus the file's identity.

    The text is kept exactly as given; its own SHA-256 identifies it. The
    original file's name and SHA-256 are kept as metadata for provenance.
    """
    return SourceDocument.from_text(text, "exposure_text", origin=f"upload: {original_filename}", now=now,
                                    metadata={"original_filename": original_filename,
                                              "original_file_sha256": original_file_sha256,
                                              "uploaded_by": uploaded_by})


@dataclass(frozen=True)
class ExtractionStage:
    """Stage 1 result. status is awaiting_approval, rejected (by the verifier) or failed (provider)."""

    status: str
    document: SourceDocument
    record: AIExtractionRecord
    report: VerificationReport
    requirements: Requirements


def extract_and_verify(document: SourceDocument, provider: AIProvider, gazetteer, *, now: datetime | None = None) -> ExtractionStage:
    record = run_extraction(provider, build_exposure_request(document), now=now)
    report = verify_extraction(record, document)
    requirements = approval_requirements(record, report, gazetteer)
    if record.status != "ok":
        status = "failed"
    elif report.outcome == "rejected":
        status = "rejected"
    else:
        status = "awaiting_approval"
    return ExtractionStage(status, document, record, report, requirements)


@dataclass(frozen=True)
class WorkflowResult:
    """Stage 3 result: the account rows, three engine runs, the comparison and the linking record."""

    status: str
    exposure: AssembledExposure
    runs: dict
    comparison: pd.DataFrame
    ep_comparison: pd.DataFrame
    workflow_record: dict
    output_dir: Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _completed(result: RunResult, label: str) -> RunResult:
    if result.record.status != "completed":
        raise RuntimeError(f"the {label} run stopped at {result.record.failed_at}: {result.record.failure}")
    return result


def compare(baseline: RunResult, combined: RunResult, account: RunResult) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Baseline, account and portfolio-with-account per scenario and tier, and the marginal change.

    Every number is read from an engine output table; the marginal change is
    the portfolio-with-account result minus the baseline result.
    """
    keys = ["scenario_id", "tier"]
    base = baseline.outputs.tier_summary.set_index(keys)[list(COMPARED)]
    both = combined.outputs.tier_summary.set_index(keys)[list(COMPARED)]
    alone = account.outputs.tier_summary.set_index(keys)[list(COMPARED)]
    table = pd.concat({"baseline": base, "account": alone, "with_account": both, "marginal": both - base}, axis=1)
    table.columns = [f"{group}_{column}" for group, column in table.columns]
    ep_keys = keys + ["return_period_years", "annual_exceedance_probability"]
    ep_base = baseline.outputs.ep_points.set_index(ep_keys)["portfolio_loss_kes"]
    ep_both = combined.outputs.ep_points.set_index(ep_keys)["portfolio_loss_kes"]
    ep = pd.DataFrame({"baseline_loss_kes": ep_base, "with_account_loss_kes": ep_both,
                       "marginal_loss_kes": ep_both - ep_base}).reset_index()
    return table.reset_index(), ep


def run_approved(stage: ExtractionStage, approval: ApprovalRecord, *, config: ModelConfig,
                 mapping: ReturnPeriodMapping, data_dir: str | Path, gazetteer, output_dir: str | Path,
                 rasters: HazardRasters | None = None, now: datetime | None = None) -> WorkflowResult:
    """Stage 3. Refuses anything but a current approval; writes every artefact under output_dir."""
    check_approval(approval, stage.document, stage.record, stage.report)
    data_dir, out = Path(data_dir), Path(output_dir)
    out.mkdir(parents=True, exist_ok=False)
    rasters = rasters or HazardRasters.open(data_dir)

    assembled = assemble(stage.document, stage.record, stage.report, approval, gazetteer)
    rows = enrich(assembled.rows, rasters)

    write_record(stage.document, out, "source_document.json")
    write_record(stage.record, out, "ai_extraction.json")
    (out / "verification_report.json").write_text(stage.report.to_json(), encoding="utf-8")
    (out / "approval.json").write_text(approval.to_json(), encoding="utf-8")
    account_csv = write_exposure(rows, out / "account_exposure.csv")
    base_csv = data_dir / "exposure_nairobi_with_hazard.csv"
    combined_csv = write_combined(base_csv, rows, out / "portfolio_with_account.csv")

    runs = {
        "baseline": _completed(run_model(base_csv, config, mapping, now=now), "baseline"),
        "account": _completed(run_model(account_csv, config, mapping, now=now), "account"),
        "with_account": _completed(run_model(combined_csv, config, mapping, now=now), "portfolio-with-account"),
    }
    run_hashes = {name: write_run(result, out / "runs") for name, result in runs.items()}
    comparison, ep_comparison = compare(runs["baseline"], runs["with_account"], runs["account"])
    comparison.to_csv(out / "comparison.csv", index=False, lineterminator="\n")
    ep_comparison.to_csv(out / "ep_comparison.csv", index=False, lineterminator="\n")

    record = stage.record
    workflow_record = {
        "record_format": WORKFLOW_FORMAT,
        "created_at": utc_timestamp(now),
        "status": "completed",
        "source_document": {"document_id": stage.document.document_id, "text_sha256": stage.document.sha256,
                            "origin": stage.document.origin,
                            "original_file_sha256": stage.document.metadata.get("original_file_sha256")},
        "ai_extraction": {"extraction_id": record.extraction_id, "provider": record.provider, "model": record.model,
                          "execution_mode": record.execution_mode, "replay_of": record.replay_of,
                          "prompt_version": record.prompt_version, "prompt_sha256": record.prompt_sha256,
                          "schema_version": record.schema_version, "schema_sha256": record.schema_sha256,
                          "response_sha256": record.response_sha256, "request_id": record.request_id},
        "verification": {"outcome": stage.report.outcome,
                         "report_sha256": hashlib.sha256(stage.report.to_json().encode("utf-8")).hexdigest()},
        "approval": {"approval_id": approval.approval_id, "decision": approval.decision,
                     "approver": approval.approver, "approver_verified": approval.approver_verified,
                     "candidate_sha256": approval.candidate_sha256, "approval_sha256": approval_file_sha256(approval),
                     "corrections": len(approval.corrections), "excluded_items": list(approval.excluded_items)},
        "exposure": {"account_csv_sha256": _sha256(account_csv), "with_account_csv_sha256": _sha256(combined_csv),
                     "baseline_csv_sha256": _sha256(base_csv), "account_buildings": len(rows),
                     "loc_ids": [r["loc_id"] for r in rows], "value_origins": list(assembled.origins)},
        "hazard": {"raster_set_id": rasters.raster_set_id,
                   "rasters": {s.tier: {"file": s.file_name, "sha256": s.sha256} for s in rasters.sources}},
        "runs": {name: {"run_id": r.record.run_id, "record_sha256": run_hashes[name],
                        "input_sha256": r.record.input_sha256, "parameter_set_id": r.record.parameter_set_id,
                        "mapping_id": r.record.mapping_id, "code_version": r.record.to_dict()["code_version"],
                        "output_digests": dict(r.record.output_digests)} for name, r in runs.items()},
        "unmodelled_terms": list(assembled.unmodelled_terms),
        "statements": [SYNTHETIC_PROXY_STATEMENT, *ASSUMPTION_STATEMENTS,
                       "Unmodelled policy terms (deductibles, limits, business interruption) are listed but not "
                       "applied: losses are structural ground-up losses."],
    }
    (out / "workflow_record.json").write_text(json.dumps(workflow_record, sort_keys=True, indent=2,
                                                         ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return WorkflowResult("completed", assembled, runs, comparison, ep_comparison, workflow_record, out)
