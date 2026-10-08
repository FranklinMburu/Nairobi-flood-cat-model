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

- Repository bootstrap: complete.
- Step 1, input validation (rules V1 to V11): present in `loss_engine/validation.py`, with tests.
- Checkpoint 1, parameter and scenario configuration: present in `loss_engine/config.py`, with tests.
  It holds the parameters and the four default scenarios of the specification, checks them
  against rules P1 to P9, and gives each parameter set a deterministic `parameter_set_id`.
- Nothing else is implemented. There is no vulnerability, damage or loss calculation, no
  aggregation, no EP analysis, and no AI, interface or human-decision step yet.

The engine is built one checkpoint at a time:
input validation -> parameter configuration -> vulnerability primitives -> building loss
-> aggregation -> EP analysis -> provenance -> AI analysis -> human decision.

## Layout

```
docs/specifications/   Frozen engine specification (Revision 2)
docs/decisions/        Frozen decision record (D-001 to D-005)
docs/reference/        Organizer problem statement, dataset metadata, build guide
data/                  Source data as supplied by the organizers, unchanged
loss_engine/           Python package (currently: input validation and configuration)
tests/                 Tests for the package
PROVENANCE.md          Hashes and facts for every source file
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

Requires Python 3.13 or newer. From the repository root, on Windows:

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

On macOS or Linux use `.venv/bin/python` instead.
