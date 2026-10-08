# 07 - Deterministic Loss Engine Specification

**Status:** DRAFT FOR REVIEW, revision 2. No code has been written.
**Date:** 2026-10-07; revised 2026-10-08
**Implements:** D-001 (TIV), D-002 and D-003 (tiers), D-005 (vulnerability) from `claude/06 - Decision Record`. No decision is reopened or changed by this revision.

Source tags: [O] organizer, [D] data observation, [S] published source, [P] established practice, [A] team assumption.

**Revision 2 (audit corrections, wording and schema only):** `curve_position` defined as a scenario-derived severity coordinate (3.3); `affected` defined as proxy-flagged (3.1); tier monotonicity written out explicitly (V7); run record expanded for provenance (4.4); basis of the RCC ceiling stated (2.2); common-curve limitation stated (3.4). No formula, parameter value, scenario, test value or reference total has changed.

---

## 1. Scope

The engine takes the supplied exposure file and a parameter set, and returns the structural loss for every building in every hazard tier under every scenario, with the intermediate values that produced it.

**In scope:** Hazard score → curve position → damage factor → damage ratio → building loss → tier and class totals.

**Out of scope for this checkpoint:**

- Return periods and the EP curve (D-004 is applied later, to this engine's output). This document contains no return-period mapping and no EP calculation; that is the next checkpoint.
- AI, agents, frontend, scenario-analysis interface.
- Raster lookup. The engine reads the pre-attached scores, which were verified identical to the rasters [D].
- Contents, business interruption, deductibles, limits, reinsurance.
- Any randomness. The same inputs must always give the same outputs.

---

## 2. Inputs

### 2.1 Exposure file

`exposure_nairobi_with_hazard.csv` [O]. SHA-256 of the supplied file: `b60aa96590d5a2e71509579d50c18d4ebd3557e26d2c60505f89d20bee69aa48`.

| Column | Type | Used for |
|---|---|---|
| `loc_id` | text | Key |
| `lat`, `lon` | float | Carried through; not used in the calculation |
| `housing_class` | text | Selects the ceiling |
| `floor_area_m2`, `cost_per_m2_kes` | integer | Carried through only. Never used to compute or check TIV (D-001) |
| `tiv_kes` | float | Value at risk, used exactly as supplied (D-001) |
| `synthetic` | boolean | Carried through to every output |
| `source` | text | Carried through |
| `hazard_score_extreme`, `_severe`, `_moderate`, `_occasional`, `_common` | float | Hazard score per tier |

The hazard score is a relative susceptibility score from 0 to 1. It is not a depth, a probability or a return period [O] (D-002).

### 2.2 Parameters

Held in one configuration object. Every value carries its source tag, and the tag is written to the outputs.

| Parameter | Value | Tag |
|---|---|---|
| Tier order, most frequent to rarest | extreme, severe, moderate, occasional, common | [O][D] |
| Curve depth points (the published curve's own axis) | 0, 0.5, 1, 1.5, 2, 3, 4, 5, 6 | [S] |
| Curve damage factors | 0, 0.220, 0.378, 0.531, 0.636, 0.817, 0.903, 0.957, 1.000 | [S] |
| Curve name | JRC Africa residential (Huizinga et al. 2017) | [S] |
| Ceiling `informal_iron_sheet` | 0.95 | [A] |
| Ceiling `semi_permanent` | 0.90 | [A] |
| Ceiling `permanent_masonry` | 0.80 | [A] |
| Ceiling `concrete_rcc`, reference | 0.65 | [A], informed by [S]; see below |
| Ceiling `concrete_rcc`, sensitivity case | 0.80 | [A] |

**Basis of the RCC ceiling.** The 0.65 reference ceiling is a team adaptation assumption [A]. It is informed by two published, non-Kenyan sources: approximately 0.65 for engineered reinforced concrete and masonry (Class IV) in Englhardt et al. (2019), developed for Ethiopia, and approximately 0.60 implied by the JRC-based relationship for concrete and brick buildings (Huizinga et al. 2017). The 0.80 sensitivity case is also a team assumption [A]. Neither value is a Kenya-specific calibrated RCC parameter, and neither may be described as one.

**All four ceilings** are structure-only team assumptions [A]. None is calibrated to Kenyan loss data.

### 2.3 Scenarios

A scenario is a named pair of H and a ceiling set. The default run computes four.

| Scenario id | Label | H | RCC ceiling |
|---|---|---|---|
| `reference` | Central/reference scenario | 4 | 0.65 |
| `low` | Low sensitivity scenario | 2 | 0.65 |
| `high` | High sensitivity scenario | 6 | 0.65 |
| `reference_rcc80` | Reference scenario, RCC ceiling 0.80 | 4 | 0.80 |

H is an assumption and scenario parameter [A]. It has no empirical Nairobi calibration. No scenario is an estimate of Nairobi conditions.

---

## 3. Formulas

### 3.1 Building level

For building *i*, tier *t*, scenario *k*:

1. **Score:** s = `hazard_score_<t>` of building i.
2. **Affected (proxy-flagged) flag:** affected = (s > 0).
3. **Curve position:** p = H(k) × s.
4. **Damage factor:** f = straight-line interpolation of the curve at p. If p is above the last depth point, f = the last damage factor (1.000).
5. **Ceiling:** c = ceiling of the building's class in scenario k.
6. **Damage ratio:** DR = c × f. Where s = 0, DR = 0.
7. **Building loss:** loss = `tiv_kes` × DR.

In one line: damage_ratio(i,t) = ceiling(class of i) × f(H × hazard_score(i,t)), and 0 where the score is 0; loss(i,t) = tiv_kes(i) × damage_ratio(i,t).

**Meaning of `affected`.** A building is considered affected by the proxy only in the computational sense that its supplied hazard score is greater than zero. This does not establish that physical flooding occurred at that building. The field name `affected` is retained in the outputs for continuity with the existing schemas and tests; wherever it appears, in this document, in outputs and in any display, it means proxy-flagged, not confirmed flooded. The same reading applies to `affected_buildings`, `affected_tiv` and every field derived from them.

### 3.2 Aggregates

For tier *t*, scenario *k*:

- affected_buildings = count of proxy-flagged buildings.
- affected_tiv = sum of `tiv_kes` over proxy-flagged buildings.
- portfolio_loss = sum of loss over all buildings.
- loss_pct_portfolio = portfolio_loss ÷ total TIV.
- loss_pct_affected = portfolio_loss ÷ affected_tiv (undefined when no building is flagged; report as empty).
- avg_loss_per_affected = portfolio_loss ÷ affected_buildings (same rule).

### 3.3 Precision and naming

**Precision:** calculate in double precision and never round intermediate values. Round only when displaying.

**Meaning of `curve_position`.** curve_position = H × hazard_score. It is not a measured flood depth, and it is not a probability of flooding. It is a scenario-derived severity coordinate used to interrogate the published JRC depth-damage relationship.

The scenario-derived curve position is expressed in metres only because the published JRC vulnerability function is parameterized on a depth axis. It must not be interpreted as a measured, observed, or calibrated flood depth for Nairobi.

**Naming rule:** p is called `curve_position`. It must not be named, labelled or displayed as depth for any building (D-005). The engine uses the JRC depth axis mathematically for interpolation and for nothing else.

### 3.4 Methodology assumptions and limitations

- **One curve shape for four classes.** The model uses the JRC Africa residential vulnerability curve for all four housing classes because Kenya-specific depth-damage functions and sufficiently detailed class-specific African curves are unavailable for this prototype. Housing classes therefore differ through structural damage ceilings rather than through separate curve shapes.
- This is a deliberate prototype assumption (D-005). It is not presented as a Kenya-calibrated vulnerability model.
- The common curve shape is a limitation: it does not represent how much faster weak structures are damaged than strong ones at the same severity.
- The class-specific ceilings are assumptions [A] (2.2), not calibrated values.
- **Score to curve position.** The mapping through H is an assumption [A]. No Nairobi depth evidence supports any value of H, so curve positions carry the limitation stated in 3.3.
- **Curve origin.** The published curve was built from South African and Mozambican houses and from river and coastal flooding [S]. It is applied here to a surface-water susceptibility proxy.
- **Reading the results.** Every loss from this engine is a scenario result on a synthetic portfolio and a proxy hazard. Results should be interpreted as scenario-conditional and compared across the low, reference and high scenarios, not read as estimates of Nairobi flood loss.

---

## 4. Outputs

Every output table carries `run_id`, which links each result to its run record (4.4).

### 4.1 Building results (one row per building, tier and scenario)

For the supplied file and the default scenarios: 600 × 5 × 4 = 12,000 rows.

| Field | Meaning |
|---|---|
| `scenario_id` | From 2.3 |
| `loc_id`, `housing_class`, `tiv_kes`, `synthetic` | From the input |
| `tier` | One of the five |
| `hazard_score` | s |
| `affected` | s > 0. Proxy-flagged; not confirmed flooded (3.1) |
| `curve_position` | p. Scenario-derived severity coordinate; not a flood depth (3.3) |
| `damage_factor` | f |
| `ceiling` | c |
| `damage_ratio` | DR |
| `loss_kes` | loss |

These columns are the visible intermediate outputs the briefing asks for: a judge can follow one building from score to loss.

### 4.2 Tier summary (one row per tier and scenario)

`scenario_id`, `tier`, `affected_buildings`, `affected_tiv_kes`, `portfolio_loss_kes`, `loss_pct_portfolio`, `loss_pct_affected`, `avg_loss_per_affected_kes`.

### 4.3 Class summary (one row per class, tier and scenario)

`scenario_id`, `tier`, `housing_class`, `buildings`, `tiv_kes`, `affected_buildings`, `affected_tiv_kes`, `loss_kes`, `loss_ratio` (loss ÷ class TIV), `share_of_tier_loss`, `mean_damage_ratio_affected`.

The last field, laid out as class × tier, is the vulnerability matrix the specification requires [O].

### 4.4 Run record

**Purpose:** reproducibility and auditability. A future reviewer must be able to identify exactly which data, model version, parameter set and execution produced a loss result. One run record is written per execution, and it holds at minimum:

| Field | Content |
|---|---|
| `run_id` | Unique identifier of this execution. Appears on every output table |
| `model_version` | Version of the methodology, i.e. of this specification and the decisions it implements |
| `code_version` | Version identifier of the implementation that executed the run |
| `timestamp` | Date and time the run started, in UTC |
| `parameter_set_id` | Identifier of the complete parameter set. Any change to any parameter value must give a different identifier |
| Input filename | Name of the exposure file read |
| Input SHA-256 | Hash of the exposure file as read |
| Row count | Number of buildings read |
| Total TIV | Sum of `tiv_kes` as read |
| Parameter values | Every parameter in 2.2, with its value |
| Parameter source tags | The source tag of every parameter |
| Scenario definitions | Each scenario id, label, H and ceiling set (2.3) |
| Validation results | Each rule in section 5: pass or fail, and every warning |
| Synthetic/proxy statement | A statement that the portfolio is synthetic and the hazard is a proxy |

A run stopped by a validation fail still writes its run record, with the failing rule, and no loss outputs.

This specification does not prescribe a database or storage mechanism for the run record.

---

## 5. Validation rules

A **fail** stops the run before any loss is calculated. A **warn** is recorded in the run record and the run continues.

### 5.1 Exposure file

| # | Rule | Action |
|---|---|---|
| V1 | All required columns are present | Fail |
| V2 | No missing value in `loc_id`, `housing_class`, `tiv_kes` or any hazard score | Fail |
| V3 | `loc_id` is unique | Fail |
| V4 | `housing_class` is one of the four configured classes | Fail |
| V5 | `tiv_kes` is a finite number above 0 | Fail |
| V6 | Every hazard score is a finite number from 0 to 1 inclusive | Fail |
| V7 | For every building: common ≥ occasional ≥ moderate ≥ severe ≥ extreme, within tolerance 1e-9 | Fail |
| V8 | `synthetic` is True on every row | Warn |
| V9 | File SHA-256 matches the supplied file | Warn |
| V10 | When V9 passes: 600 rows and total TIV of exactly 63,635,075,000 | Fail |
| V11 | `lat` between −1.45 and −1.10 and `lon` between 36.60 and 37.00 (the raster extent) | Warn |

**V7 in full.** For every building, with each term being that building's `hazard_score_<tier>`:

`common >= occasional >= moderate >= severe >= extreme`

Each adjacent pair is tested, and a pair passes when the left score is at least the right score minus 1e-9.

This ordering follows from D-002 and D-003. The five rasters are cuts of the same underlying susceptibility score, each rescaled to 0–1 above its own cut-off, and the common tier has the widest footprint and the lowest cut-off. A building's score can therefore only fall or stay equal as the cut-off rises from common to extreme.

V7 is a fail because a violation means the tier columns are mislabelled or corrupted, and the loss ordering (D-003) would no longer hold. The data is never silently corrected, reordered or clipped to make V7 pass.

### 5.2 Parameters

| # | Rule | Action |
|---|---|---|
| P1 | Curve depth points start at 0 and strictly increase | Fail |
| P2 | Curve damage factors start at 0, never decrease, and stay within 0 to 1 | Fail |
| P3 | Depth and damage-factor lists have equal length | Fail |
| P4 | H is a finite number above 0 | Fail |
| P5 | H is at most the last depth point (6) | Warn; positions above 6 take the last damage factor |
| P6 | Every ceiling is above 0 and at most 1 | Fail |
| P7 | A ceiling exists for every class | Fail |
| P8 | The tier list has exactly the five tiers in the D-003 order | Fail |
| P9 | Scenario ids are unique | Fail |

### 5.3 Output checks (run after calculation)

| # | Check | Action |
|---|---|---|
| C1 | Every damage ratio is between 0 and the building's ceiling | Fail |
| C2 | Every loss is between 0 and ceiling × `tiv_kes` | Fail |
| C3 | Loss is 0 exactly where the score is 0 | Fail |
| C4 | For each building and scenario, loss does not decrease from extreme to common | Fail |
| C5 | For each scenario, portfolio loss does not decrease from extreme to common | Fail |
| C6 | Class losses sum to the tier's portfolio loss (tolerance KES 1) | Fail |
| C7 | Building results have exactly buildings × 5 × scenarios rows | Fail |
| C8 | For each tier, low ≤ reference ≤ high, and reference ≤ reference_rcc80 | Fail |

C4, C5 and C8 are checks, not corrections. Nothing is adjusted to make them pass.

---

## 6. Edge cases

| Case | Behaviour |
|---|---|
| Score exactly 0 | Not proxy-flagged (`affected` false); damage ratio 0; loss 0. 341 of the 600 buildings are 0 in every tier [D] |
| Very small positive score (smallest in the file is 0.000418) | Proxy-flagged (`affected` true); counted; loss is small but not zero |
| Score exactly 1 | Curve position = H; allowed. Does not occur at any building in the file (highest is 0.685) [D] |
| Curve position above 6 | Damage factor held at 1.000; warning P5. Cannot occur with H ≤ 6 |
| Curve position exactly on a depth point | Returns that point's damage factor |
| Score below 0, above 1, or not a number | Fail V6 |
| Unknown housing class | Fail V4. No default ceiling is applied |
| TIV of 0, negative or missing | Fail V5 |
| Duplicate `loc_id` | Fail V3 |
| A tier with no proxy-flagged building | Loss 0; the two ratio fields that divide by affected values are empty, not 0 |
| A different portfolio file | V9 warns and V10 is skipped; all other rules apply |
| Extra columns in the file | Ignored and carried through |
| TIV disagrees with floor area × cost | Not checked. This is expected (D-001) |

---

## 7. Test cases

Tolerances: 1e-6 on damage factors and ratios; KES 0.01 on building losses; KES 1 on totals.

In every table below, "affected" means proxy-flagged (3.1) and "curve position" is the severity coordinate of 3.3, not a flood depth.

### 7.1 Curve interpolation

| Position | Expected damage factor | Why |
|---|---|---|
| 0 | 0.000000 | First point |
| 0.25 | 0.110000 | Midway 0 to 0.5 |
| 0.5 | 0.220000 | On a point |
| 0.75 | 0.299000 | Midway 0.5 to 1 |
| 1.2 | 0.439200 | Within 1 to 1.5 |
| 2.5 | 0.726500 | Midway 2 to 3 |
| 3.5 | 0.860000 | Midway 3 to 4 |
| 6 | 1.000000 | Last point |
| 7 | 1.000000 | Above range, held |

### 7.2 Single buildings, `reference` scenario

| Building | Class | TIV (KES) | Tier | Score | Curve position | Damage factor | Ceiling | Damage ratio | Loss (KES) |
|---|---|---|---|---|---|---|---|---|---|
| NBO-0001 | informal_iron_sheet | 625,000 | common | 0 | 0 | 0 | 0.95 | 0 | 0.00 |
| NBO-0000 | semi_permanent | 5,170,000 | common | 0.0232857969 | 0.093143 | 0.040983 | 0.90 | 0.036885 | 190,693.91 |
| NBO-0000 | semi_permanent | 5,170,000 | moderate | 0 | 0 | 0 | 0.90 | 0 | 0.00 |
| NBO-0002 | semi_permanent | 3,300,000 | common | 0.4584057033 | 1.833623 | 0.601061 | 0.90 | 0.540955 | 1,785,150.55 |
| NBO-0002 | semi_permanent | 3,300,000 | moderate | 0.3452048898 | 1.380820 | 0.494531 | 0.90 | 0.445078 | 1,468,756.43 |
| NBO-0002 | semi_permanent | 3,300,000 | extreme | 0.1346025914 | 0.538410 | 0.232138 | 0.90 | 0.208924 | 689,448.90 |
| NBO-0005 | informal_iron_sheet | 1,570,000 | common | 0.6697713137 | 2.679085 | 0.758914 | 0.95 | 0.720969 | 1,131,920.87 |
| NBO-0005 | informal_iron_sheet | 1,570,000 | extreme | 0.4723374248 | 1.889350 | 0.612763 | 0.95 | 0.582125 | 913,936.67 |
| NBO-0316 | concrete_rcc | 522,650,000 | common | 0.6274335384 | 2.509734 | 0.728262 | 0.65 | 0.473370 | 247,406,947.15 |
| NBO-0316 | concrete_rcc | 522,650,000 | extreme | 0.4046871066 | 1.618748 | 0.555937 | 0.65 | 0.361359 | 188,864,365.08 |
| NBO-0130 | concrete_rcc | 958,965,000 | common | 0.2919898331 | 1.167959 | 0.429396 | 0.65 | 0.279107 | 267,653,950.92 |
| NBO-0130 | concrete_rcc | 958,965,000 | extreme | 0 | 0 | 0 | 0.65 | 0 | 0.00 |

These cover: a building with a zero score in every tier, a building flagged only in the widest tier, one building across three tiers, each of three classes, and the two largest contributors.

### 7.3 Portfolio totals, all scenarios (KES)

| Tier | `low` | `reference` | `high` | `reference_rcc80` |
|---|---|---|---|---|
| extreme | 266,230,031.74 | 468,443,071.72 | 609,294,901.20 | 544,354,996.24 |
| severe | 461,802,507.68 | 826,175,003.83 | 1,103,866,847.82 | 964,742,195.00 |
| moderate | 1,086,205,322.49 | 1,972,325,986.45 | 2,688,400,578.14 | 2,333,078,198.37 |
| occasional | 1,832,007,234.02 | 3,279,344,346.85 | 4,491,451,558.67 | 3,887,799,934.29 |
| common | 2,848,531,482.88 | 5,103,936,640.53 | 6,958,250,832.32 | 6,070,980,379.83 |

Two further combinations, for a test that scenarios are freely configurable (KES): H = 2 with RCC 0.80 gives 309,239,672.62 / 538,678,237.49 / 1,283,493,518.05 / 2,171,392,798.76 / 3,386,495,169.47; H = 6 with RCC 0.80 gives 707,127,001.41 / 1,289,768,761.10 / 3,181,688,855.74 / 5,328,379,145.57 / 8,280,759,673.94 (extreme to common).

### 7.4 Tier summary, `reference` scenario

| Tier | Affected buildings | Affected TIV (KES) | Loss ÷ portfolio TIV | Loss ÷ affected TIV | Average loss per affected building (KES) |
|---|---|---|---|---|---|
| extreme | 32 | 1,659,425,000 | 0.7361% | 28.2292% | 14,638,845.99 |
| severe | 51 | 5,254,505,000 | 1.2983% | 15.7232% | 16,199,509.88 |
| moderate | 110 | 11,978,060,000 | 3.0994% | 16.4662% | 17,930,236.24 |
| occasional | 174 | 19,754,335,000 | 5.1534% | 16.6006% | 18,846,806.59 |
| common | 259 | 31,394,010,000 | 8.0206% | 16.2577% | 19,706,319.08 |

Affected counts and affected TIV are the same in every scenario.

### 7.5 Class summary, `reference` scenario, two tiers

| Tier | Class | Buildings | Class TIV (KES) | Affected | Affected TIV (KES) | Loss (KES) | Loss ÷ class TIV | Share of tier loss |
|---|---|---|---|---|---|---|---|---|
| common | informal_iron_sheet | 179 | 198,110,000 | 74 | 81,800,000 | 20,602,488.80 | 10.3995% | 0.404% |
| common | semi_permanent | 181 | 850,710,000 | 74 | 362,000,000 | 81,540,194.91 | 9.5850% | 1.598% |
| common | permanent_masonry | 156 | 8,451,170,000 | 66 | 3,525,445,000 | 811,271,086.49 | 9.5995% | 15.895% |
| common | concrete_rcc | 84 | 54,135,085,000 | 45 | 27,424,765,000 | 4,190,522,870.32 | 7.7409% | 82.104% |
| extreme | informal_iron_sheet | 179 | 198,110,000 | 10 | 10,235,000 | 3,257,421.29 | 1.6442% | 0.695% |
| extreme | semi_permanent | 181 | 850,710,000 | 8 | 32,095,000 | 7,146,264.84 | 0.8400% | 1.526% |
| extreme | permanent_masonry | 156 | 8,451,170,000 | 11 | 552,725,000 | 129,087,712.68 | 1.5275% | 27.557% |
| extreme | concrete_rcc | 84 | 54,135,085,000 | 3 | 1,064,370,000 | 328,951,672.92 | 0.6076% | 70.222% |

### 7.6 Vulnerability matrix, `reference` scenario (mean damage ratio of affected buildings)

| Class | extreme | severe | moderate | occasional | common |
|---|---|---|---|---|---|
| informal_iron_sheet | 0.2963 | 0.3420 | 0.2375 | 0.2372 | 0.2519 |
| semi_permanent | 0.2405 | 0.2242 | 0.2256 | 0.2329 | 0.2320 |
| permanent_masonry | 0.2696 | 0.3184 | 0.2259 | 0.2434 | 0.2345 |
| concrete_rcc | 0.2532 | 0.1621 | 0.1860 | 0.1620 | 0.1636 |

Tolerance 1e-4. The values do not rise steadily across tiers because the mix of proxy-flagged buildings changes from tier to tier; that is expected and is not a fault.

### 7.7 Validation and edge-case tests

Each uses a small hand-made input, not the supplied file.

| Test | Input | Expected |
|---|---|---|
| Missing column | File without `tiv_kes` | Fail V1 |
| Missing value | One blank hazard score | Fail V2 |
| Duplicate key | Two rows with the same `loc_id` | Fail V3 |
| Unknown class | `housing_class` = `timber` | Fail V4 |
| Zero value | `tiv_kes` = 0 | Fail V5 |
| Score out of range | A score of 1.2, and a score of −0.1 | Fail V6 |
| Tier order broken | Extreme score above common score | Fail V7 |
| Tier order broken, adjacent pair | Severe score above moderate score, all else in order | Fail V7 |
| Tier order within tolerance | Occasional above common by 5e-10 | V7 passes |
| Not flagged synthetic | `synthetic` = False | Warn V8; run completes |
| Different file | Any modified copy | Warn V9; V10 skipped |
| Curve not increasing | Damage factors 0, 0.3, 0.2 | Fail P2 |
| H too large | H = 8, one building with score 1 | Warn P5; damage factor 1.000; damage ratio equals the ceiling |
| Ceiling out of range | Ceiling 1.2 | Fail P6 |
| No affected building | All scores 0 | All losses 0; ratio fields empty; run completes |
| Score of exactly 1, H = 4, RCC, TIV 1,000,000 | — | Damage factor 0.903; damage ratio 0.58695; loss 586,950.00 |
| Determinism | Run the supplied file twice | Identical outputs and identical `parameter_set_id`; only `run_id` and `timestamp` differ |
| Input untouched | Run on the supplied file | `tiv_kes` in the output equals the input on every row; total 63,635,075,000 |
| Run record complete | Run on the supplied file | Every field in 4.4 is present and non-empty; input SHA-256 equals the value in 2.1; every output row carries the run's `run_id` |
| Run record on failure | Any input that fails a rule | Run record written with the failing rule; no loss outputs |

---

## 8. Points for review before coding

1. **Language and form.** This specification is language-neutral. A plain Python module with pandas would be the simplest fit; confirm, or name the stack you want.
2. **Default scenario set.** Four scenarios as in 2.3. The other two combinations are supported but not run by default.
3. **V7 as a hard stop.** A tier-order violation stops the run. The alternative is a warning.
4. **V10 tied to the file hash.** The 600-row and total-TIV checks apply only to the supplied file, so other portfolios can be run later.
5. **Storage.** Outputs and the run record are specified as tables and fields only. Where they are stored is left to the next step.
6. **`affected` field name kept.** The name is retained and defined as proxy-flagged (3.1). The alternative is renaming it to `hazard_flagged` across all outputs and tests.
7. **`run_id` on every output table.** Added so a loss result can be traced to its run record (4.4). Confirm.
