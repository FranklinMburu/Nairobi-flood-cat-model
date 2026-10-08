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

Implemented: Checkpoints 1 to 6.

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
- **Checkpoint 6 — Return Periods and EP / Loss Points** (`loss_engine/ep_curve.py`). The
  provisional D-004 return periods, AEP = 1 / T, and five EP / loss points per scenario read from
  the tier summary. See below.

Nothing else is implemented. There is no EAL, PML or TVaR, no run record, and no AI, interface
or human-decision step yet.

Later checkpoints, in order: provenance -> AI analysis -> human decision.

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
applied only after losses are computed (Checkpoint 6).

## Return periods and EP / loss points (Checkpoint 6)

Each hazard tier is assigned a return period T, and its annual exceedance probability is taken
as AEP = 1 / T, stored as a fraction:

| Tier | Return period | AEP |
|---|---:|---:|
| extreme | 10 years | 0.100 |
| severe | 25 years | 0.040 |
| moderate | 50 years | 0.020 |
| occasional | 100 years | 0.010 |
| common | 250 years | 0.004 |

**These return periods are provisional assumptions, not observations.** They come from the
organizers' reference dashboard and were not confirmed (D-004). The hazard files carry no
event-frequency information: the five tiers are cuts through one susceptibility score (D-002).
The mapping is held in one configurable table (`ReturnPeriodMapping`), with its own
deterministic `mapping_id`, so it can be replaced without changing the loss engine.

**What the output is:** a scenario-based EP / loss representation, constructed from five
deterministic hazard tiers assigned provisional return periods. For each scenario there are
exactly five points, each a modelled portfolio loss from Checkpoint 5 at an assigned return
period. Under the reference scenario the loss rises from about KES 468m at the provisional
10-year tier to about KES 5.10bn at the provisional 250-year tier.

**What it is not:** a statistically calibrated, stochastic-event-set or Monte Carlo EP curve, or
a flood-frequency model. Nothing is interpolated between the five points or extrapolated beyond
them, and a tier's hazard return period is assumed, not shown, to be the return period of its
portfolio loss.

**Deferred metrics:**

- **EAL — deferred.** The five points cover only AEP 0.004 to 0.1. Even the rigorous bounds they
  allow leave the reference-scenario EAL anywhere between about KES 104m and 371m, so any single
  figure would reflect the assumption chosen for the missing regions, not the data.
- **PML — deferred.** A loss can only be read off at the five assumed hazard return periods;
  calling it a PML would imply the loss's own return period has been estimated.
- **TVaR — deferred.** It needs the loss distribution beyond 250 years, which does not exist.

## Layout

```
docs/specifications/   Frozen engine specification (Revision 2)
docs/decisions/        Frozen decision record (D-001 to D-005)
docs/reference/        Organizer problem statement, dataset metadata, build guide
data/                  Source data as supplied by the organizers, unchanged
loss_engine/           Python package: validation, configuration, vulnerability, building loss, aggregation, EP points
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
