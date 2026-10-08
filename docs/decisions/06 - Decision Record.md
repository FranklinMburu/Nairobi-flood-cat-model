# 06 - Decision Record

Log of confirmed decisions for the Team A Nairobi flood prototype. Each entry states who decided it and what it rests on. Entries here outrank the Master Specification (Context #2) and any earlier summary in conversation.

Status values: ORGANIZER-CONFIRMED, DATA-ESTABLISHED, SOURCE-VERIFIED, RESEARCH FINDING, PROVISIONAL, TEAM DECISION, OPEN, CLOSED.

Source tags: [O] organizer-provided fact, [D] observed directly in the supplied data, [S] stated in the cited published source, [P] established catastrophe-modelling practice, [A] our own modelling assumption.

---

## D-001 — Insured values (TIV) to use for financial loss

- **Status:** ORGANIZER-CONFIRMED (not an assumption)
- **Recorded:** 2026-10-07
- **Decision:** Use the `tiv_kes` values in the supplied exposure CSVs exactly as supplied for all financial-loss calculations.
- **Source of truth:** `exposure_nairobi_with_hazard.csv` / `exposure_nairobi_synthetic.csv` (identical `tiv_kes` in both).
- **Portfolio TIV:** KES 63,635,075,000 (about KES 63.64 billion), 600 buildings.
- **Do not:** scale, modify or otherwise correct the CSV values.
- **Background:** `Dataset_Metadata.docx` states a total of KES 6,363,470,000 and says `tiv_kes` equals floor area × cost per m². The CSV values are about 10× that. The organizers have confirmed the metadata figure is a documentation inconsistency in the synthetic starter data and the CSV is correct.
- **Consequences to carry forward:**
  - `tiv_kes` does not equal `floor_area_m2` × `cost_per_m2_kes` in the files. Do not recompute or validate TIV from those two columns.
  - The metadata's TIV statistics (minimum 40,000; median 520,000; maximum 159,200,000; total 6.36 bn) must not be quoted. The file values are minimum 410,000; median 5,192,500; maximum 1,592,010,000.
  - The written note and interface should cite the CSV as the value source and still label the portfolio as synthetic.
- **Applies at:** the financial-loss engine. No design change is made on account of this entry.

---

## D-002 — What the five Nairobi hazard tiers are

- **Status:** DATA-ESTABLISHED, consistent with organizer documentation
- **Recorded:** 2026-10-07
- **Finding:** The five rasters are five cuts through one underlying 0–1 susceptibility score. They are not five independently simulated flood events.
- **Organizer statement [O]:** "The five files are cuts through that one score. 'common' keeps the highest-scoring 40% of cells, 'occasional' 30%, 'moderate' 20%, 'severe' 10% and 'extreme' 5%, each rescaled to run from 0 to 1." (`Dataset_Metadata.docx`)
- **Verified in the files [D]:**
  - Share of cells above zero is exactly 40%, 30%, 20%, 10%, 5%.
  - All five share one grid (1,439 × 1,260, EPSG:4326, 1 arc-second).
  - Each narrower tier equals max(0, (c − k) / (1 − k)), where c is the 'common' score and k is 0.0862 (occasional), 0.1729 (moderate), 0.2805 (severe), 0.3742 (extreme). Largest deviation is below 1e-7.
  - Footprints are nested; every cell's and every building's score is non-increasing from common to extreme.
- **What a tier score means:** how far a cell's susceptibility sits above that tier's cut-off, rescaled to 0–1. It is not a depth, a probability or a return period [O].
- **Consequences:**
  - The files hold one layer of hazard information. The five-point loss curve reflects five cut-offs, not five modelled events.
  - The same score value in two tiers refers to different underlying susceptibility.
  - The raw score below the 40% cut-off is not recoverable from the files.
  - Dry is exactly 0; there is no NoData value [O][D].

## D-003 — Tier ordering

- **Status:** DATA-ESTABLISHED, consistent with organizer documentation
- **Recorded:** 2026-10-07
- **Ordering, most frequent to rarest:** extreme → severe → moderate → occasional → common.
- **Organizer statement [O]:** "A rarer flood reaches more places, so the widest map ('common') stands for the rarest event and the narrowest ('extreme') for the most frequent." The names describe how extreme a cell is, not how often it floods.
- **Verified [D]:** buildings flagged are 32, 51, 110, 174, 259 (extreme to common); every building's score is highest in 'common'.
- **Precision on wording:** 'extreme' contains only the most susceptible cells (top 5%), but each building's score value is lowest in 'extreme' and highest in 'common'. Hazard intensity fed to the model therefore rises toward 'common'.
- **Consequence:** with any damage function that does not decrease as score rises, portfolio loss is guaranteed to rise from extreme to common.

## D-004 — Return period per tier

- **Status:** PROVISIONAL. Taken from the organizers' reference dashboard; the organizers did not confirm it before leaving.
- **Recorded:** 2026-10-07
- **Mapping [O, as a stated assumption of the reference dashboard]:** extreme = 10 years, severe = 25, moderate = 50, occasional = 100, common = 250.
- **Nature:** an assumption "made so the loss curve can be drawn" [O]. The files carry no frequency information [D]. The Team A specification requires us to state a return period per tier, so this must appear in our note as an assumption whatever values we use.
- **Blocking?** No. Per-tier loss does not depend on return period. Return period affects only the curve's horizontal axis, the labels on headline figures, and any figure interpolated or integrated from the curve.
- **Rule to carry forward:** hold the mapping as one configurable tier-to-years table, applied after per-tier losses are computed. Key all calculations by tier name. Any replacement mapping must keep the order in D-003.

---

## R-001 — The vulnerability reference source (JRC / Huizinga)

- **Status:** SOURCE-VERIFIED from the published report, read through an automated page reader. Corrections and the numerical check are in R-003; this entry is left as first recorded.
- **Recorded:** 2026-10-07
- **Source:** Huizinga, J., de Moel, H., Szewczyk, W. (2017). *Global flood depth-damage functions: Methodology and the database with guidelines.* EUR 28552 EN, JRC105688, doi:10.2760/16510. Report and Excel database at https://publications.jrc.ec.europa.eu/repository/handle/JRC105688
- **Categories [S]:** residential buildings, commerce, industry, transport, infrastructure, agriculture, given per continent. They are occupancy / land-use classes, not construction-material classes. No Africa commerce function exists.
- **Africa residential function [S] (Table 3-1):** depth 0 / 0.5 / 1 / 1.5 / 2 / 3 / 4 / 5 / 6 m gives damage factor 0 / 0.22 / 0.38 / 0.53 / 0.64 / 0.82 / 0.90 / 0.96 / 1.00. Built from South Africa (small, medium, large house) and Mozambique (urban, rural house). No Kenyan data.
- **Loss concept [S]:** the damage factor is normalised to reach 1 at 6 m and is a fraction of a *maximum damage value*, not of full rebuild cost. Residential maximum damage includes contents (contents taken as 50% of building damage), uses depreciated value (factor 0.60), and the report notes an undamageable part of about 40% for water-resistant materials such as concrete and brick.
- **Construction type [S]:** the report says building material, formal vs informal and urban vs rural justify adjustments to maximum damage, but gives no numeric factors for them.
- **Flood type [S]:** built from fluvial and coastal data and offered for a "generic inundation event". Duration and velocity are not included. Pluvial flooding is not specifically addressed.
- **Uncertainty [S]:** 90% interval on residential maximum damage is about −28% to +53%. No standard deviations are tabulated for the Africa residential function. (Superseded on the second point by R-003.)
- **Consequences:**
  - The source supplies one residential curve shape for all four Nairobi classes. Any difference between classes is our adaptation [A].
  - Our `tiv_kes` is structure rebuild cost only [O]. A factor of 1.00 therefore cannot be read as 100% of TIV; a ceiling below 1 must be stated [A], consistent with the specification's 80–95% cap [O].
  - The source curve is concave: 22% of maximum damage at 0.5 m. It is not the "near zero at low severity" S-shape described in the specification [O].

---

## R-002 — Vulnerability sensitivity experiment

- **Status:** RESEARCH FINDING. Sensitivity scenarios only; no value here is calibrated, measured or a model result. No return periods were used. Superseded for model parameters by D-005.
- **Recorded:** 2026-10-07
- **Data:** `exposure_nairobi_with_hazard.csv`, 600 buildings, TIV as supplied (D-001). Affected means tier score above 0.
- **Methods tested:**
  - **Option 2:** assumed depth [A] = score × Dmax, Dmax of 2, 4, 6 m; JRC Africa residential table [S], linear between points; times class ceiling [A].
  - **Option 1:** severity x = k × score, k = 1/3, 2/3, 1 [A]; smooth curve (1 − e^(−x/0.362)) / (1 − e^(−1/0.362)), fitted by us to the JRC points on a 0–1 axis (largest error 0.021); times class ceiling [A]. k = Dmax / 6, so each Option 1 scenario is the same assumption as the matching Option 2 scenario.
  - **Option 3b:** four score bands (edges 0.10, 0.25, 0.50 [A]); band factor = JRC value at band midpoint × Dmax.
  - **Option 3a:** one factor for every flagged building (JRC value at pooled mean flagged score 0.187 × Dmax: 0.165 / 0.300 / 0.417).
- **Ceiling schemes [A]** (informal / semi-permanent / masonry / RCC): ORG 0.95 / 0.90 / 0.85 / 0.80 (inside the specification's 80–95% range [O]); WIDE 0.95 / 0.85 / 0.70 / 0.60; U60 all 0.60; U100 all 1.00.
- **Portfolio loss, KES bn, ORG ceilings:**

| Tier | Affected buildings | Affected TIV (bn) | O2 low | O2 medium | O2 high | O1 medium | O3b medium | O3a medium |
|---|---|---|---|---|---|---|---|---|
| extreme | 32 | 1.66 | 0.315 | 0.553 | 0.721 | 0.559 | 0.534 | 0.408 |
| severe | 51 | 5.25 | 0.548 | 0.981 | 1.311 | 0.997 | 1.034 | 1.273 |
| moderate | 110 | 11.98 | 1.298 | 2.363 | 3.219 | 2.414 | 2.619 | 2.904 |
| occasional | 174 | 19.75 | 2.193 | 3.935 | 5.386 | 4.054 | 4.222 | 4.783 |
| common | 259 | 31.39 | 3.418 | 6.135 | 8.364 | 6.299 | 7.043 | 7.594 |

- **Findings [D, computed]:**
  - **Monotonic without forcing:** every method and scenario rises from extreme to common.
  - **Severity scale:** high is about 2.3 to 2.5 times low in every tier; medium is about 1.8 times low.
  - **Ceilings:** U100 is about 23% above ORG; WIDE and U60 are about 23% to 26% below.
  - **RCC dominance:** RCC is 73% to 85% of loss in every continuous scenario. Lowering only the RCC ceiling from 0.80 to 0.60 cuts 'common' loss by 21%; lowering the other three ceilings by a quarter cuts it by 3.9%.
  - **Option 1 vs Option 2:** 0.3% to 4.7% apart. The gap is entirely the smooth fit versus straight-line interpolation.
  - **Option 3b vs Option 2:** −5% to +15% at portfolio level; building-level losses differ by 7% to 29% of tier loss in aggregate.
  - **Option 3a vs Option 2:** −29% to +35%; building-level correlation with the continuous result falls to 0.53 in the 'severe' tier.
  - **Curve shape comes from the hazard cut-offs.** Loss as a share of affected TIV is nearly flat across the four wider tiers (about 19% to 20% at O2 medium), so loss tracks affected TIV. The common-to-extreme loss ratio is about 11 for every continuous scenario.
  - **The frequent end rests on very few buildings.** In 'extreme', 3 RCC buildings are flagged, one building is 42% of loss and two are over half. In 'common', 12 buildings make up half.
  - **A class shape adjustment** (severity multiplier 1.5 / 1.25 / 1.0 / 0.75 [A]) moved loss by −15% to −18%, again through RCC.
- **Ranking of what drives the result:** severity scale (about 2.4×), then the RCC ceiling and any RCC shape adjustment (about 20% each), then the choice between continuous and banded (up to 15%, or 35% if binary), then Option 1 vs 2 (under 5%), then the three non-RCC classes (under 4%).

---

## R-003 — Closing the evidence gaps from R-002

- **Status:** RESEARCH FINDING
- **Recorded:** 2026-10-07; updated 2026-10-07

### JRC numerical check

- **Direct Excel check:** attempted twice on 2026-10-07 and not possible. The working environment's network policy blocks the JRC download, and the page reader cannot parse the binary file. Per team instruction, we proceed on the cross-check below and record that the direct check was not done.
- **Cross-check performed:** the values were compared against two independent open-source transcriptions of the JRC database: CLIMADA (`climada_petals`, `river_flood.py`) and OS-Climate `physrisk` (static vulnerability JSON). They agree with each other to three decimals.
- **Africa residential, database precision [S, via transcription]:** depth 0 / 0.5 / 1 / 1.5 / 2 / 3 / 4 / 5 / 6 m gives 0 / 0.220 / 0.378 / 0.531 / 0.636 / 0.817 / 0.903 / 0.957 / 1.000.
- **Comparison with R-001:** the report table in R-001 is the same curve rounded to two decimals. There is no conflict. The database values are the authoritative ones because the report table is a rounded print of them. Using them changes portfolio loss by 0.1% to 0.3% [D, computed].
- **Correction to R-001:** R-001 says no standard deviations exist for the Africa residential function. The database carries them: 0.042 / 0.114 / 0.198 / 0.208 / 0.205 / 0.142 / 0.076 at 0.5 to 5 m [S, via physrisk]. The spread is about ±0.2 in damage factor between 1.5 and 3 m.
- **Unchanged:** depth points, the 0-to-1 normalisation, the maximum-damage concept, the 0.60 depreciation factor and the 50% contents share are as recorded in R-001. Africa has no commerce function in the database either.
- **Still worth doing if anyone can open the file:** confirm the nine values in the Excel itself.

### Nairobi flood-depth evidence

- **Result:** insufficient. No source I could read gives a measured or modelled flood depth for Nairobi.
- **What exists:**
  - Munyi (2024, University of Twente MSc): pluvial susceptibility modelling for Nairobi. Uses citizen-report depth categories of 0.10 m (ankle), 0.50 m (knee), 1.2 m (waist) and above 2.0 m (above head). These are reporting bins, not measurements.
  - Juma, Olang, Hassan et al. (2023, Physics and Chemistry of the Earth 132:103499): hydrodynamic modelling of flood inundation and hazard levels in Kibera. The full text was not accessible, so its depths are not recorded here.
  - WRI (2026), "Flooding in Nairobi's Informal Settlements": describes both river-corridor and drainage flooding; gives no depths.
  - A 2026 systematic review of recurrent flooding in Nairobi (68 sources) reports no depth measurements.
- **Consequence:** no depth scale for the proxy can be supported from evidence. The Juma et al. paper is the one source worth obtaining.

### Structural ceilings

- **JRC [S]:** many functions for concrete and brick buildings level off near 60%, implying about 40% that is not damaged.
- **Englhardt et al. (2019), NHESS 19, 1703–1722 [S]:** structure-only flood vulnerability curves by building material, developed for Ethiopia from a 23-study review and engineering consultation.
  - Class I (non-engineered: mud, adobe, informal) and Class II (wood): reach total loss, damage factor 1.0, at 2.5 m.
  - Class III (unreinforced masonry/concrete): approaches but does not reach 1.0.
  - Class IV (engineered reinforced masonry/concrete, steel): capped at 0.65, because foundations, structural walls and frames do not need replacing.
- **Reading:** two published sources put a structure-only ceiling for reinforced concrete at about 0.60 to 0.65. The 0.80 used in the ORG scheme sits above both. Neither source is Kenyan.

### Uncertainty hierarchy

| Strength | Inputs | Basis |
|---|---|---|
| Strongest | Supplied hazard scores; supplied `tiv_kes`; JRC curve shape and values | [O] files and confirmation; [S] published database, cross-checked |
| Moderate, external | Structural damage ceilings, chiefly the 0.65 for reinforced concrete | [S] two published non-Kenyan sources; applied as [A] |
| Weakest, assumption-driven | Mapping score to severity through H; using one curve shape for all four classes; the three non-RCC ceilings | [A]; no Nairobi evidence |

Strong evidence for an input does not make it a physical measurement: the hazard scores are a proxy [O], and the portfolio is synthetic [O].

---

## D-005 — Vulnerability / damage methodology

- **Status:** TEAM DECISION. Confirmed and locked 2026-10-07.
- **Recorded:** 2026-10-07

### Decision

Continuous damage function. The tier score is placed on the published JRC Africa residential curve through one declared severity-scale parameter, H, and the result is multiplied by a structure-only ceiling for the building's class.

### Locked points

- The hazard score remains a relative susceptibility score. It is not a depth, a probability or a return period [O].
- The curve is the published JRC Africa residential function [S], with straight-line interpolation between its points.
- One declared severity-scale parameter, H [A], labelled everywhere as an assumption and scenario parameter.
- H = 4 is the central/reference scenario. H = 2 and H = 6 are the low and high sensitivity scenarios. H = 4 has no empirical Nairobi calibration and must not be called an estimate.
- H × score is never displayed as a measured or estimated flood depth.
- The ceilings below are structure-only and are not Kenya-calibrated; they must never be described as such.
- The 0.80 RCC ceiling is kept as an explicit sensitivity case.

### Formulation

- s(i,t): hazard score of building i in tier t, 0 to 1 [O].
- H: severity scale, in units of the JRC depth axis per unit score [A].
- f(d): JRC Africa residential damage factor [S], straight-line interpolation between (0, 0), (0.5, 0.220), (1, 0.378), (1.5, 0.531), (2, 0.636), (3, 0.817), (4, 0.903), (5, 0.957), (6, 1.000).
- c(class): structure-only damage ceiling.

  damage_ratio(i,t) = c(class of i) × f(H × s(i,t)), and 0 where s(i,t) = 0
  loss(i,t) = tiv_kes(i) × damage_ratio(i,t)
  portfolio_loss(t) = sum of loss(i,t) over all buildings

### Parameters

| Parameter | Value | Tag |
|---|---|---|
| Curve | JRC Africa residential, nine points above | [S] |
| H, central/reference scenario | 4 | [A]; the specification's own worked example [O]; no Nairobi calibration |
| H, low / high sensitivity scenarios | 2 / 6 | [A] |
| Ceiling, concrete_rcc (reference) | 0.65 | [A], anchored on Englhardt Class IV 0.65 and JRC about 0.60 [S] |
| Ceiling, concrete_rcc (sensitivity case) | 0.80 | [A], bottom of the specification's 80–95% guidance [O] |
| Ceiling, permanent_masonry | 0.80 | [A], between reinforced concrete and the weaker classes |
| Ceiling, semi_permanent | 0.90 | [A], within the specification's range [O] |
| Ceiling, informal_iron_sheet | 0.95 | [A], top of the specification's range [O]; Englhardt Class I reaches total loss [S] |

### Rationale

- Options 1 and 2 give the same answer to within 5% (R-002), so the choice rests on honesty and traceability.
- Using the published table directly keeps every damage value traceable to a printed number. A fitted smooth curve adds a parameter of ours for no gain.
- The scale parameter cannot be avoided by any option (R-001). Declaring it once, with three scenarios shown, is more honest than leaving it implicit.
- Ceilings are the simplest transparent way to separate the four classes, and the RCC ceiling is the only class parameter that moves the result materially (R-002). It is therefore the one anchored on published evidence.

### Uncertainty

The hierarchy in R-003 applies. The weakest inputs, H and the class adaptation, are also the ones that move the result most (R-002). The system exposes this by always showing the low, reference and high scenarios together, by showing the RCC 0.80 case, and by tagging every parameter with its source.

### Vulnerability matrix

Derived from the function, not set separately. Reference scenario, damage ratio at reference scores:

| Class | s = 0.10 | 0.25 | 0.50 | 0.75 | 1.00 |
|---|---|---|---|---|---|
| informal_iron_sheet | 0.167 | 0.359 | 0.604 | 0.776 | 0.858 |
| semi_permanent | 0.158 | 0.340 | 0.572 | 0.735 | 0.813 |
| permanent_masonry | 0.141 | 0.302 | 0.509 | 0.654 | 0.722 |
| concrete_rcc | 0.114 | 0.246 | 0.413 | 0.531 | 0.587 |

The class × tier matrix required by the specification is the mean damage ratio of affected buildings per class per tier, computed from the same function.

### Scenario results [D, computed], portfolio loss in KES

| Tier | Low (H = 2) | Reference (H = 4) | High (H = 6) | Reference with RCC 0.80 |
|---|---|---|---|---|
| extreme | 266,230,031.74 | 468,443,071.72 | 609,294,901.20 | 544,354,996.24 |
| severe | 461,802,507.68 | 826,175,003.83 | 1,103,866,847.82 | 964,742,195.00 |
| moderate | 1,086,205,322.49 | 1,972,325,986.45 | 2,688,400,578.14 | 2,333,078,198.37 |
| occasional | 1,832,007,234.02 | 3,279,344,346.85 | 4,491,451,558.67 | 3,887,799,934.29 |
| common | 2,848,531,482.88 | 5,103,936,640.53 | 6,958,250,832.32 | 6,070,980,379.83 |

The reference scenario's 'common' loss is 8.02% of portfolio TIV. The RCC 0.80 case is 16% to 19% above the reference scenario.

### Alternatives rejected

- **Fitted smooth curve on the score (Option 1 as tested):** same answer, weaker traceability, hides the scale.
- **Showing assumed depths per building (Option 2 as usually presented):** invites reading invented metres as flood depths.
- **Score bands (Option 3b):** up to 15% different, with band edges as extra assumptions.
- **Binary flags (Option 3a):** up to 35% different and loses building-level information.
- **Class-specific curve shapes:** supported in direction by Englhardt, but each needs parameters we cannot source. The three non-RCC classes together are 18% (common) to 30% (extreme) of reference-scenario loss, so this is a real limitation at the frequent end.
- **Monte Carlo, machine-learned vulnerability, local calibration:** no data to support them.

### Limitations

- H is an assumption. No Nairobi depth evidence supports any value (R-003).
- The curve is built from South African and Mozambican houses, from river and coastal flooding; it is applied here to a surface-water susceptibility proxy and to all four classes, including large RCC blocks.
- The ceilings are not Kenyan and are not calibrated. The RCC ceiling alone moves the result by about a fifth.
- One shape for all classes understates how quickly weak structures are damaged.
- Structure only: no contents, no business interruption, no policy terms.
- The published curve has a spread of about ±0.2 in damage factor at mid depths, which is not propagated.
- The curve points were cross-checked against two transcriptions, not against the Excel file itself (R-003).

### Implications for implementation

See `claude/07 - Deterministic Loss Engine Specification`.

---

## O-001 — Converting susceptibility score to damage ratio

- **Status:** CLOSED by D-005 on 2026-10-07.
- **Recorded:** 2026-10-07
- **Options considered:** score directly into a saturating curve; score to an assumed depth then the published curve; damage bands.
- **Checks still worth making, none blocking:** the nine JRC values in the Excel file; Juma et al. (2023) for Kibera depths; the reference dashboard's parameters and the marking rubric, if obtainable.
