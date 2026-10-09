"""Demonstration of the Checkpoint 8 workflow on the bundled example submission.

    python -m loss_engine.demo                         # stage 1 only: prints what a reviewer must do
    python -m loss_engine.demo --approver "A. Reviewer" # also approves with examples/demo_review.json and runs

By default the AI step is a replay of examples/demo_replay_record.json, a
HAND-WRITTEN response (provider "fixture", model "hand-written-response"):
no AI was called to produce it. `--gemini-model NAME` calls Gemini instead,
which needs GEMINI_API_KEY; without it the run stops with a recorded error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .ai_providers import GeminiProvider, ReplayProvider
from .approval import ApprovalError, Correction, decide
from .config import default_config
from .ep_curve import default_return_periods
from .exposure_assembly import AssemblyError, load_gazetteer
from .workflow import extract_and_verify, run_approved, submission_from_upload

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m loss_engine.demo", description=__doc__.splitlines()[0])
    parser.add_argument("--submission", type=Path, default=EXAMPLES / "demo_submission.txt")
    parser.add_argument("--replay", type=Path, default=EXAMPLES / "demo_replay_record.json")
    parser.add_argument("--gemini-model", help="call Gemini live with this model instead of the replay")
    parser.add_argument("--review", type=Path, default=EXAMPLES / "demo_review.json")
    parser.add_argument("--approver", help="the reviewer's name; without it the workflow stops before approval")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--out", type=Path, help="output directory (must not exist); default outputs/workflow-<UTC time>")
    args = parser.parse_args(argv)

    raw = args.submission.read_bytes()
    document = submission_from_upload(raw.decode("utf-8"), original_filename=args.submission.name,
                                      original_file_sha256=hashlib.sha256(raw).hexdigest(), uploaded_by="demo")
    provider = GeminiProvider(args.gemini_model) if args.gemini_model else ReplayProvider.from_file(args.replay)
    gazetteer = load_gazetteer(args.data)
    stage = extract_and_verify(document, provider, gazetteer)

    record = stage.record
    print(f"document   {document.document_id}  ({args.submission.name})")
    print(f"extraction {record.extraction_id}  provider={record.provider} model={record.model} "
          f"mode={record.execution_mode} status={record.status}"
          + (f" replay_of={record.replay_of}" if record.replay_of else ""))
    if record.execution_mode == "replay":
        print("           replayed response: no AI provider was called in this run")
    print(f"verifier   {stage.report.outcome}")
    for check in stage.report.checks:
        if check.result != "pass":
            print(f"  [{check.result}] {check.item_id or '*'}.{check.field} {check.check}: {check.detail}")
    if stage.status != "awaiting_approval":
        print(f"status     {stage.status}: {record.error or stage.requirements.reason}")
        return 1
    print("to approve, acknowledge:", *stage.requirements.acknowledge, sep="\n  ")
    if stage.requirements.unresolved:
        print("unresolved (correct or exclude):", *(f"{k}: {', '.join(v)}" for k, v in stage.requirements.unresolved.items()),
              sep="\n  ")
    if not args.approver:
        print("status     awaiting_approval (pass --approver NAME to approve with", args.review.name + ")")
        return 0

    review = json.loads(args.review.read_text(encoding="utf-8"))
    try:
        approval = decide("approve", document=document, record=record, report=stage.report, approver=args.approver,
                          gazetteer=gazetteer, reason=review.get("reason"), acknowledged=review["acknowledged"],
                          corrections=[Correction(**c) for c in review["corrections"]],
                          excluded_items=review["excluded_items"])
    except ApprovalError as error:
        print(f"status     approval refused: {error}")
        return 1
    print(f"approval   {approval.approval_id} by {approval.approver} (not authenticated)")

    out = args.out or ROOT / "outputs" / f"workflow-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    try:
        result = run_approved(stage, approval, config=default_config(), mapping=default_return_periods(),
                              data_dir=args.data, gazetteer=gazetteer, output_dir=out)
    except (ApprovalError, AssemblyError, RuntimeError) as error:
        print(f"status     failed: {error}")
        return 1
    print(f"account    {len(result.exposure.rows)} buildings: {', '.join(r['loc_id'] for r in result.exposure.rows)}")
    for name, run in result.runs.items():
        print(f"run        {name:<12} {run.record.run_id}  input {run.record.input_sha256[:12]}  rows {run.record.row_count}")
    table = result.comparison
    ref = table[table["scenario_id"] == "reference"]
    print("\nreference scenario, portfolio loss (KES, structural ground-up, synthetic proxy hazard):")
    print(f"  {'tier':<11}{'baseline':>18}{'account':>16}{'with account':>18}{'marginal':>16}")
    for _, row in ref.iterrows():
        print(f"  {row['tier']:<11}{row['baseline_portfolio_loss_kes']:>18,.2f}{row['account_portfolio_loss_kes']:>16,.2f}"
              f"{row['with_account_portfolio_loss_kes']:>18,.2f}{row['marginal_portfolio_loss_kes']:>16,.2f}")
    if result.exposure.unmodelled_terms:
        print("not modelled:", *(f"{t['item_id']} ({t['item_status']}): {t['kind']} - {t['quote']!r}" for t in result.exposure.unmodelled_terms),
              sep="\n  ")
    print(f"\nstatus     completed; workflow record: {result.output_dir / 'workflow_record.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
