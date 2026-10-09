"""Write outputs/portfolio.json from the shared engine payload.

Prefer the Django API for the application. This script is a local export only.
It does not calculate loss.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = Path(__file__).resolve().parent
for path in (str(ROOT), str(BACKEND)):
    if path not in sys.path:
        sys.path.insert(0, path)

from portfolio.engine import get_payload  # noqa: E402

OUT = ROOT / "outputs" / "portfolio.json"


def main() -> None:
    payload = get_payload()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    common = payload["tier_summary"]["reference"]["common"]
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes)")
    print(f"reference/common loss {common['portfolio_loss_kes']:.2f}")


if __name__ == "__main__":
    main()
