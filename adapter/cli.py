"""CLI for nairobi-flood-adapt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import AdapterConfig
from .pipeline import adapt_file, AdaptationResult


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nairobi-flood-adapt",
        description="Adapt arbitrary exposure files to the canonical Nairobi flood model schema.",
    )
    parser.add_argument("input", help="Input file (CSV, XLSX, GeoJSON)")
    parser.add_argument("output_dir", help="Output directory for adapted.csv and adaptation_report.json")
    parser.add_argument(
        "--synthetic",
        choices=["true", "false"],
        help="Explicitly declare synthetic status (required if column missing)",
    )
    parser.add_argument(
        "--fx-rate",
        type=float,
        help="Override KES per USD exchange rate (default from config/adapter.json)",
    )
    parser.add_argument(
        "--accept-uncertain",
        action="store_true",
        help="Accept needs_confirmation proposals without prompting (non-interactive)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to adapter config JSON (default: adapter/config/adapter.json)",
    )
    parser.add_argument(
        "--threshold-accept",
        type=float,
        help="Override accept_at threshold (default 0.85)",
    )
    parser.add_argument(
        "--threshold-refuse",
        type=float,
        help="Override refuse_below threshold (default 0.50)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Override data directory for hazard rasters (default: data/)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args(argv)

    # Load config
    config = AdapterConfig.load(
        config_path=args.config,
        fx_rate=args.fx_rate,
        data_dir=args.data_dir,
        accept_at=args.threshold_accept,
        refuse_below=args.threshold_refuse,
    )

    synthetic = None
    if args.synthetic is not None:
        synthetic = args.synthetic.lower() == "true"

    try:
        result: AdaptationResult = adapt_file(
            input_path=args.input,
            output_dir=args.output_dir,
            config=config,
            synthetic=synthetic,
            accept_uncertain=args.accept_uncertain,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 2

    # Print summary
    print(f"Status: {result.status}")
    print(f"Report: {result.report_path}")
    if result.adapted_path:
        print(f"Adapted: {result.adapted_path}")

    if result.proposals:
        print("\nProposals needing confirmation:")
        for p in result.proposals:
            print(f"  - {p.get('type', 'unknown')}: {json.dumps(p, default=str)[:200]}")

    if result.report.refusal_reasons:
        print("\nRefusal reasons:")
        for r in result.report.refusal_reasons:
            print(f"  - {r}")

    # Exit codes: 0=accepted, 1=needs_confirmation, 2=refused/error
    if result.status == "accepted":
        return 0
    elif result.status == "needs_confirmation":
        return 1
    else:
        return 2


if __name__ == "__main__":
    sys.exit(main())