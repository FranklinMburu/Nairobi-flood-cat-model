"""End-to-end tests of the Checkpoint 8 workflow: document -> replay -> verifier -> approval -> engine.

Every AI response here is the bundled hand-written replay fixture or a test
double; no provider is called. Needs the optional "geo" extra (rasterio).
"""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("rasterio")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loss_engine import demo  # noqa: E402
from loss_engine.ai_providers import GeminiProvider, ReplayProvider  # noqa: E402
from loss_engine.ai_records import AIProviderResult, SourceDocument  # noqa: E402
from loss_engine.approval import ApprovalError, decide  # noqa: E402
from loss_engine.config import default_config  # noqa: E402
from loss_engine.ep_curve import default_return_periods  # noqa: E402
from loss_engine.exposure_assembly import enrich, load_gazetteer, write_exposure  # noqa: E402
from loss_engine.hazard_lookup import HazardRasters  # noqa: E402
from loss_engine.run_record import read_run_record, run_model  # noqa: E402
from loss_engine.validation import SCORE_COLUMNS  # noqa: E402
from loss_engine.workflow import extract_and_verify, run_approved, submission_from_upload  # noqa: E402

DATA = ROOT / "data"
EXAMPLES = ROOT / "examples"
BASE = DATA / "exposure_nairobi_with_hazard.csv"
ITEM2_FLAGS = ("item-2|housing_class|mapping_needs_confirmation", "item-2|insured_value|basis_unsupported")
TIERS = ("extreme", "severe", "moderate", "occasional", "common")
# Specification 7.3, reference scenario (H = 4), KES.
SPEC_7_3_REFERENCE = (468_443_071.72, 826_175_003.83, 1_972_325_986.45, 3_279_344_346.85, 5_103_936_640.53)


class Canned:
    """A test double standing in for a live provider."""

    name, model, execution_mode = "test-double", "canned", "local"

    def __init__(self, text):
        self.text = text

    def extract(self, request):
        return AIProviderResult(self.name, self.model, self.execution_mode, "ok", self.text)


@pytest.fixture(scope="module")
def gazetteer():
    return load_gazetteer(DATA)


@pytest.fixture(scope="module")
def rasters():
    return HazardRasters.open(DATA)


def submission():
    raw = (EXAMPLES / "demo_submission.txt").read_bytes()
    import hashlib
    return submission_from_upload(raw.decode("utf-8"), original_filename="demo_submission.txt",
                                  original_file_sha256=hashlib.sha256(raw).hexdigest(), uploaded_by="test")


@pytest.fixture(scope="module")
def stage(gazetteer):
    return extract_and_verify(submission(), ReplayProvider.from_file(EXAMPLES / "demo_replay_record.json"), gazetteer)


def approve(stage, gazetteer, decision="approve", **kwargs):
    options = dict(acknowledged=ITEM2_FLAGS, excluded_items=("item-3",))
    options.update(kwargs)
    return decide(decision, document=stage.document, record=stage.record, report=stage.report,
                  approver="Reviewer", gazetteer=gazetteer, **options)


def run(stage, approval, gazetteer, rasters, out):
    return run_approved(stage, approval, config=default_config(), mapping=default_return_periods(),
                        data_dir=DATA, gazetteer=gazetteer, output_dir=out, rasters=rasters)


@pytest.fixture(scope="module")
def completed(stage, gazetteer, rasters, tmp_path_factory):
    return run(stage, approve(stage, gazetteer), gazetteer, rasters, tmp_path_factory.mktemp("wf") / "out")


# --- The upload boundary ----------------------------------------------------------------


def test_upload_adapter_keeps_text_exactly_and_the_original_file_identity():
    document = submission()
    assert document.text == (EXAMPLES / "demo_submission.txt").read_text(encoding="utf-8")
    assert document.kind == "exposure_text" and document.origin == "upload: demo_submission.txt"
    assert len(document.metadata["original_file_sha256"]) == 64 and document.metadata["uploaded_by"] == "test"


# --- Stage 1 ------------------------------------------------------------------------------


def test_stage_1_stops_awaiting_approval(stage):
    assert stage.status == "awaiting_approval"
    assert stage.record.execution_mode == "replay" and stage.record.replay_of is not None
    assert stage.report.outcome == "needs_review" and stage.requirements.approvable


def test_a_provider_failure_is_a_failed_stage_not_an_empty_result(gazetteer, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    failed = extract_and_verify(submission(), GeminiProvider("gemini-test"), gazetteer)
    assert failed.status == "failed" and failed.record.status == "error"
    assert failed.report.outcome == "rejected" and not failed.requirements.approvable


def test_a_rejected_candidate_stops_at_stage_1(stage, gazetteer):
    bad = json.loads(stage.record.response_text)
    bad["items"][0]["building_count"]["value"] = 40  # not stated
    rejected = extract_and_verify(submission(), Canned(json.dumps(bad)), gazetteer)
    assert rejected.status == "rejected" and not rejected.requirements.approvable
    with pytest.raises(ApprovalError):
        approve(rejected, gazetteer)


# --- Stage 3 refuses anything but a current approval ------------------------------------


@pytest.mark.parametrize("decision", ["reject", "request_review"])
def test_no_engine_run_without_approval(stage, gazetteer, rasters, tmp_path, decision):
    no = approve(stage, gazetteer, decision=decision, reason="not confirmed")
    with pytest.raises(ApprovalError, match="nothing may proceed"):
        run(stage, no, gazetteer, rasters, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_a_stale_approval_is_refused(stage, gazetteer, rasters, tmp_path):
    approval = approve(stage, gazetteer)
    edited = json.loads(stage.record.response_text)
    edited["items"][1]["unmodelled_terms"] = []
    other = extract_and_verify(submission(), Canned(json.dumps(edited)), gazetteer)
    with pytest.raises(ApprovalError, match="stale"):
        run(other, approval, gazetteer, rasters, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_an_existing_output_directory_is_never_reused(stage, gazetteer, rasters, tmp_path):
    (tmp_path / "out").mkdir()
    with pytest.raises(FileExistsError):
        run(stage, approve(stage, gazetteer), gazetteer, rasters, tmp_path / "out")


# --- A completed workflow -----------------------------------------------------------------


def test_three_completed_runs(completed):
    assert completed.status == "completed"
    assert {name: r.record.row_count for name, r in completed.runs.items()} == \
        {"baseline": 600, "account": 5, "with_account": 605}
    assert all(r.record.status == "completed" for r in completed.runs.values())


def test_baseline_reproduces_specification_7_3(completed):
    summary = completed.runs["baseline"].outputs.tier_summary
    reference = summary[summary["scenario_id"] == "reference"].set_index("tier")["portfolio_loss_kes"]
    for tier, expected in zip(TIERS, SPEC_7_3_REFERENCE):
        assert reference[tier] == pytest.approx(expected, abs=1.0)


def test_marginal_is_with_account_minus_baseline_and_equals_the_account_alone(completed):
    table = completed.comparison
    assert (table["marginal_portfolio_loss_kes"]
            == table["with_account_portfolio_loss_kes"] - table["baseline_portfolio_loss_kes"]).all()
    # Buildings are independent, so the portfolio-with-account adds exactly the account's own loss.
    assert table["marginal_portfolio_loss_kes"].to_numpy() == pytest.approx(
        table["account_portfolio_loss_kes"].to_numpy(), abs=0.01)
    assert (table["marginal_affected_buildings"] == table["account_affected_buildings"]).all()
    ep = completed.ep_comparison
    assert (ep["marginal_loss_kes"] == ep["with_account_loss_kes"] - ep["baseline_loss_kes"]).all()
    assert set(ep["return_period_years"]) and (ep["annual_exceedance_probability"] == 1 / ep["return_period_years"]).all()


def test_account_loss_is_positive_but_bounded_by_its_tiv(completed):
    account = completed.runs["account"].outputs.tier_summary
    assert (account["portfolio_loss_kes"] >= 0).all()
    assert (account["portfolio_loss_kes"] <= 4 * 6_500_000 + 3_200_000).all()


def test_every_artefact_is_written(completed):
    out = completed.output_dir
    for name in ("source_document.json", "ai_extraction.json", "verification_report.json", "approval.json",
                 "account_exposure.csv", "portfolio_with_account.csv", "comparison.csv", "ep_comparison.csv",
                 "workflow_record.json"):
        assert (out / name).is_file(), name
    for r in completed.runs.values():
        assert read_run_record(out / "runs" / r.record.run_id / "run_record.json") == r.record


def test_workflow_record_links_document_to_runs(completed, stage):
    record = json.loads((completed.output_dir / "workflow_record.json").read_text(encoding="utf-8"))
    assert record == json.loads(json.dumps(completed.workflow_record, default=str))
    assert record["source_document"]["document_id"] == stage.document.document_id
    assert record["source_document"]["original_file_sha256"] == stage.document.metadata["original_file_sha256"]
    ai = record["ai_extraction"]
    assert ai["execution_mode"] == "replay" and ai["provider"] == "replay" and ai["model"] == "hand-written-response"
    assert ai["response_sha256"] == stage.record.response_sha256
    assert record["approval"]["approver_verified"] is False and record["approval"]["excluded_items"] == ["item-3"]
    assert record["exposure"]["account_buildings"] == 5 and len(record["exposure"]["loc_ids"]) == 5
    assert record["exposure"]["baseline_csv_sha256"] == completed.runs["baseline"].record.input_sha256
    assert record["exposure"]["account_csv_sha256"] == completed.runs["account"].record.input_sha256
    assert record["exposure"]["with_account_csv_sha256"] == completed.runs["with_account"].record.input_sha256
    assert len(record["hazard"]["rasters"]) == 5 and len(record["hazard"]["raster_set_id"]) == 64
    assert [(t["item_id"], t["item_status"], t["kind"]) for t in record["unmodelled_terms"]] == [
        ("item-2", "included", "deductible"), ("item-3", "excluded", "business_interruption")]
    assert any("not applied" in s for s in record["statements"])
    assert len({run["parameter_set_id"] for run in record["runs"].values()}) == 1


def test_the_supplied_files_are_unchanged_by_the_workflow(completed):
    import hashlib
    recorded = {"exposure_nairobi_with_hazard.csv": "b60aa96590d5a2e71509579d50c18d4ebd3557e26d2c60505f89d20bee69aa48",
                "nairobi_hotspots_geocoded.csv": "b597d843f80a800de82ed482f177f2acfc9339758276d57053eb593343dac7c3"}
    for name, digest in recorded.items():  # as recorded in PROVENANCE.md
        assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == digest
    for tier, source in completed.workflow_record["hazard"]["rasters"].items():
        assert hashlib.sha256((DATA / source["file"]).read_bytes()).hexdigest() == source["sha256"]


# --- Raster wiring regression -----------------------------------------------------------


def test_raster_enriched_copy_of_the_600_buildings_reproduces_the_reference_losses(rasters, tmp_path):
    supplied = pd.read_csv(BASE, dtype=str, keep_default_na=False)
    rows = [{k: v for k, v in row.items() if k not in SCORE_COLUMNS} for row in supplied.to_dict("records")]
    rows = [{**r, "lat": float(r["lat"]), "lon": float(r["lon"])} for r in rows]
    path = write_exposure(enrich(rows, rasters), tmp_path / "re-enriched.csv")
    config, mapping = default_config(), default_return_periods()
    again = run_model(path, config, mapping).outputs.tier_summary
    original = run_model(BASE, config, mapping).outputs.tier_summary
    assert len(again) == len(original) == 20
    assert again["portfolio_loss_kes"].to_numpy() == pytest.approx(original["portfolio_loss_kes"].to_numpy(), abs=1.0)
    assert (again["affected_buildings"].to_numpy() == original["affected_buildings"].to_numpy()).all()


# --- The demonstration -------------------------------------------------------------------


def test_demo_without_approver_stops_awaiting_approval(capsys):
    assert demo.main([]) == 0
    out = capsys.readouterr().out
    assert "awaiting_approval" in out and "no AI provider was called" in out


def test_demo_with_approver_completes(tmp_path, capsys):
    assert demo.main(["--approver", "Test Reviewer", "--out", str(tmp_path / "demo")]) == 0
    out = capsys.readouterr().out
    assert "status     completed" in out and (tmp_path / "demo" / "workflow_record.json").is_file()


def test_demo_with_gemini_but_no_key_fails_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert demo.main(["--gemini-model", "gemini-test"]) == 1
    assert "credentials unavailable" in capsys.readouterr().out
