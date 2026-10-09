"""Wrapper: run adapter then existing run_model, storing adaptation report alongside run record."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from adapter.config import AdapterConfig
from adapter.pipeline import adapt_file
from loss_engine.run_record import run_model, write_run, RunResult
from loss_engine.config import default_config, ModelConfig
from loss_engine.ep_curve import default_return_periods, ReturnPeriodMapping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nairobi-flood-run-adaptive",
        description="Run adaptive adapter then deterministic loss engine.",
    )
    parser.add_argument("input", help="Input file (CSV, XLSX, GeoJSON)")
    parser.add_argument("output_dir", help="Output directory for run artifacts")
    parser.add_argument(
        "--synthetic",
        choices=["true", "false"],
        help="Explicitly declare synthetic status",
    )
    parser.add_argument(
        "--fx-rate",
        type=float,
        help="Override KES per USD exchange rate",
    )
    parser.add_argument(
        "--accept-uncertain",
        action="store_true",
        help="Accept needs_confirmation proposals",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to adapter config JSON",
    )
    parser.add_argument(
        "--scenario",
        choices=["reference", "low", "high", "reference_rcc80"],
        default="reference",
        help="Scenario to run (default: reference)",
    )

    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Run adapter
    print("Running adapter...")
    adapter_result = adapt_file(
        input_path=args.input,
        output_dir=output_dir / "adapted",
        synthetic=(args.synthetic.lower() == "true") if args.synthetic else None,
        fx_rate=args.fx_rate,
        accept_uncertain=args.accept_uncertain,
        config=AdapterConfig.load(config_path=args.config),
    )

    if adapter_result.status == "refused":
        print(f"Adapter refused: {adapter_result.report.refusal_reasons}", file=sys.stderr)
        return 2
    elif adapter_result.status == "needs_confirmation" and not args.accept_uncertain:
        print("Adapter needs confirmation (use --accept-uncertain to proceed)", file=sys.stderr)
        return 1

    adapted_csv = adapter_result.adapted_path
    if not adapted_csv or not adapted_csv.exists():
        print("Adapter did not produce adapted.csv", file=sys.stderr)
        return 2

    print(f"Adapter {adapter_result.status}. Running loss engine...")

    # Step 2: Run existing loss engine
    config: ModelConfig = default_config()
    mapping: ReturnPeriodMapping = default_return_periods()

    run_result: RunResult = run_model(
        exposure_path=adapted_csv,
        config=config,
        mapping=mapping,
    )

    # Step 3: Write run record
    record_sha256 = write_run(run_result, output_dir / "runs")
    print(f"Run record written. SHA-256: {record_sha256}")

    # Step 4: Copy adaptation report next to run record
    run_dir = output_dir / "runs" / run_result.record.run_id
    shutil.copy2(adapter_result.report_path, run_dir / "adaptation_report.json")
    print(f"Adaptation report copied to {run_dir / 'adaptation_report.json'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())