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

Implemented: Checkpoints 1 to 7.

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
- **Checkpoint 7 — Provenance and Run Record** (`loss_engine/run_record.py`). `run_model` runs
  Checkpoints 1 to 6 and returns the specification 4.4 run record, with `run_id` on every output
  table; `write_run` saves it. See below.

Nothing else is implemented. There is no EAL, PML or TVaR, and no AI, interface or
human-decision step yet.

Later checkpoints, in order: AI analysis -> human decision.

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

## Run record (Checkpoint 7)

`run_model(exposure_path, config, mapping)` runs the whole chain unchanged and returns a
`RunRecord` (specification 4.4) plus the four output tables, each with `run_id` as its first
column. `write_run(result)` saves them, and returns `record_sha256`, the SHA-256 of the saved
record file:

```
outputs/<run_id>/run_record.json      the run record
outputs/<run_id>/building_results.csv
outputs/<run_id>/tier_summary.csv
outputs/<run_id>/class_summary.csv
outputs/<run_id>/ep_points.csv
```

The record holds the input file name, SHA-256, row count and exact total TIV; `model_version`
and the hashes of the frozen specification and decision record; the code version (package
version, Git commit, uncommitted changes, or null when Git is unavailable); every parameter with
its source tag, held once as the canonical form behind `parameter_set_id`; the return-period
mapping and `mapping_id`; every V, P, RP and C rule result, including warnings; the tier summary
and EP points; a SHA-256 digest of each output table; and fixed statements of the main
assumptions.

- **Identity.** `run_id` (`run-<UTC time>-<8 random hex characters>`) and `timestamp` identify
  one execution and differ on every run. What was computed is identified by the input SHA-256,
  `parameter_set_id`, `mapping_id`, `model_version` and the code version; two runs that share them
  produce identical output digests.
- **Output digests.** Each digest is the SHA-256 of a deterministic Checkpoint 4–6 calculation
  table, written as canonical CSV, before `run_id` is added; `run_id` and `timestamp` are never
  included. The saved CSVs carry `run_id` as their first column, as execution metadata only. To
  check a saved CSV against its digest, remove that first `run_id` column and hash the remaining
  canonical CSV text.
- **Failures.** A run stopped by a failed rule still returns and saves its record, with status
  `failed`, the failing rule as a `fail` entry in `validation_results` (for the re-check before
  the EP points, stage `ep_points`, rule C5), and no loss outputs. When input validation fails,
  `row_count` and `total_tiv_kes` are recorded as unavailable (null) rather than inferred from the
  invalid input; the input remains identified by its filename and SHA-256. Stages that were never
  reached have no entries: their rules are not recorded as passed or not run. An invalid
  `ModelConfig` or `ReturnPeriodMapping` cannot be built at all, so it never reaches a run.
- **Integrity.** `record_sha256` is computed after the file is written and is not stored inside
  it. An existing run directory is never overwritten.

## Layout

```
docs/specifications/   Frozen engine specification (Revision 2)
docs/decisions/        Frozen decision record (D-001 to D-005)
docs/reference/        Organizer problem statement, dataset metadata, build guide
data/                  Source data as supplied by the organizers, unchanged
loss_engine/           Python package: validation, configuration, vulnerability, building loss, aggregation, EP points, run record
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

## Ingest API (PDF, CSV, XLSX, GeoJSON)

```
pip install -e ".[adapter,api]" --no-deps   # plus the extras' dependencies
export ADAPTER_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uvicorn api.main:app          # from the repo root; open http://localhost:8000/
```

- `POST /adapt` adapts a file and returns the adapted CSV and the adaptation report.
- `POST /run` adapts the file, then runs the deterministic loss engine and returns its tables. The API never computes a loss itself.
- Every request needs the `X-API-Key` header. The server refuses to start without `ADAPTER_API_KEY`.
- Form fields: `file`, `synthetic` (`true`/`false`; required unless the file has a `synthetic` column), `fx_rate` (0 to 1000), `accept_uncertain`.
- PDFs must contain a real table (no OCR for scans), at most 50 pages. The largest table is used and tables continuing across pages are joined. PDF headers are matched to canonical names through a synonym map (`adapter/pdf_tables.py`); each rename is listed in the report warnings. The synonym map applies to PDFs only.
- The five `hazard_score_*` columns must be in the file; they are not derived from the rasters yet.
- Limits: 10 MB upload, type detected from content (not filename), temp files deleted after each request, no server paths in responses.
