# Nairobi Urban Flood Catastrophe Model


Team A solution to the Nairobi Urban Flood Challenge, Kenya Re AI4Insurance Hackathon 2026.

An auditable catastrophe-risk prototype, built in this order:

```
Hazard -> Exposure -> Vulnerability -> Financial Loss -> Portfolio Risk -> AI Analysis -> Human Decision
```

**The portfolio is synthetic.** The 600 buildings and their values were supplied by the
hackathon organizers and do not describe real properties. **The hazard layers are a
susceptibility proxy.** They are not flood depths, probabilities or modelled events.

## Current status

Implemented: Checkpoints 1 to 5.

- **Checkpoint 1 — Input Validation** (`loss_engine/validation.py`). Reads the exposure file and
  checks it against rules V1 to V11. A failed rule stops the run; nothing is ever corrected.
- **Checkpoint 2 — Configuration** (`loss_engine/config.py`). Holds the parameters and the four
  default scenarios of the specification, each value with its source tag, and checks them against
  rules P1 to P9. An invalid configuration cannot be built.
- **Checkpoint 3 — Vulnerability Primitives** (`loss_engine/vulnerability.py`). Hazard score ->
  curve position -> damage factor -> ceiling -> damage ratio, for one scenario.
- **Checkpoint 4 — Building Financial Loss** (`loss_engine/building_loss.py`). Loss = `tiv_kes` ×
  damage ratio, with `tiv_kes` as supplied, and the building-results table of specification 4.1
  (one row per building, tier and scenario) without `run_id`.
- **Checkpoint 5 — Portfolio Aggregation** (`loss_engine/aggregation.py`). Tier and
  class × tier summaries of specification 4.2 and 4.3, built from the building results, and
  output checks C1 to C8. A failed check stops the run.

Nothing else is implemented. There are no return periods or EP analysis, no run record, and
no AI, interface or human-decision step yet.

Later checkpoints, in order: EP analysis -> provenance -> AI analysis -> human decision.

## Deterministic configuration

Every configuration has a `parameter_set_id`: the SHA-256 of a fixed JSON text of every parameter
value and source tag. The same parameters always give the same id, and any change gives a new one.
No time, randomness or machine detail goes into it. The id will link each future loss result to
the exact parameters that produced it.

## Main modelling assumptions

Each is stated and tagged in the frozen specification and decision record.

- The hazard score is a relative susceptibility score, not a flood depth (D-002).
- The score is placed on the published JRC Africa residential curve through one scale parameter,
  H. H is an assumption with no Nairobi calibration; it is run at 2, 4 and 6 (D-005).
- One curve shape is used for all four housing classes. Classes differ only through
  structure-only damage ceilings, which are team assumptions, not Kenya-calibrated values (D-005).
- `tiv_kes` is used exactly as supplied (D-001).

## Out of scope by design

Contents, business interruption, deductibles, limits and reinsurance; raster lookup (the
pre-attached scores are used); and any randomness. Return periods (D-004, provisional) are
applied only at the later EP checkpoint.

## Layout

```
docs/specifications/   Frozen engine specification (Revision 2)
docs/decisions/        Frozen decision record (D-001 to D-005)
docs/reference/        Organizer problem statement, dataset metadata, build guide
data/                  Source data as supplied by the organizers, unchanged
loss_engine/           Python package: validation, configuration, vulnerability, building loss, aggregation
tests/                 Tests for the package
PROVENANCE.md          Hashes and facts for every source file
requirements-lock.txt  Exact dependency versions the tests passed with
.github/workflows/     Continuous integration: runs the test suite
```

Generated outputs belong in `outputs/`, which Git ignores.

## Frozen documents

These are frozen. Do not edit them. The Markdown files are the canonical text and the
.docx files are copies of them.

- `docs/specifications/07 - Deterministic Loss Engine Specification (Revision 2).md`
- `docs/decisions/06 - Decision Record.md`

The supplied data in `data/` is also never edited. `.gitattributes` stops Git from changing
line endings in `data/` and `docs/`.

## Setup and tests

Tested on Python 3.13. From the repository root, on Windows:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-lock.txt
.venv\Scripts\python -m pip install -e . --no-deps
.venv\Scripts\python -m pytest
```

On macOS or Linux use `.venv/bin/python` instead. `requirements-lock.txt` pins the exact
versions the test suite passed with. GitHub Actions runs the same steps on every push and
pull request (`.github/workflows/tests.yml`).
