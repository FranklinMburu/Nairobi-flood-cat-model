# Flood Risk Intelligence Challenge — Build Guide

This is the practical walkthrough behind the problem statement: what each
dataset in `data/` actually contains, and the sequence of steps every team
follows to turn that data into a loss curve. No code here — this is about
what to do and why, not how to write it.

Every cat model, from the simplest hackathon prototype to what RMS or Verisk
sell, is the same four-stage pipeline:

**Hazard** (where does it flood, how badly) → **Vulnerability** (how much
damage at that severity) → **Exposure** (what's actually there, and what's
it worth) → **Financial engine** (turn all three into a loss number and a
return-period curve).

Both teams walk through the same four stages below. Where a step differs
between Team A (Nairobi) and Team B (Nzoia), it's called out explicitly.

---

## 1. Hazard — understand what you're actually holding

### Team B — `data/team_b_nzoia/`

Six files: `nzoia_rp10y.tif`, `nzoia_rp20y.tif`, `nzoia_rp50y.tif`,
`nzoia_rp100y.tif`, `nzoia_rp200y.tif`, `nzoia_rp500y.tif`.

These are real JRC (EU Joint Research Centre) flood hazard maps, clipped to
the lower Nzoia basin around Budalangi. Each file is a **raster** — a grid of
values laid over the map, like a very fine spreadsheet where every cell
corresponds to a patch of ground roughly 900 m on each side (30 arc-seconds). The number in each cell is
**flood water depth in metres** for that scenario.

The "rp" in each filename is the **return period** — how rare that scenario
is. `rp10y` is a flood so common it happens roughly once every 10 years on
average; `rp500y` is a much rarer, much more severe flood. This is the
standard way catastrophe modelling expresses "how bad" — not a single
prediction, but a family of scenarios at different rarities. A cell with
depth 0 in `rp10y` might show 1.5m in `rp500y`: normally dry ground that
only floods in the rare, severe case.

**How you'll use it:** for any building's coordinates, you look up which
cell it falls into in each of the six files, and read off the depth at each
return period. That gives you a mini flood-depth-vs-rarity curve for every
single building.

### Team A — `data/team_a_nairobi/`

Five files: `nairobi_pluvial_proxy_common.tif`, `..._occasional.tif`,
`..._moderate.tif`, `..._severe.tif`, `..._extreme.tif`.

These are **not** real flood depth data — no such dataset exists publicly
for Nairobi. They're a constructed proxy: a score from 0 to 1 at each grid
cell estimating relative flood susceptibility, blended from three real,
open data sources — basin-scale terrain elevation, local terrain depressions,
and distance to real mapped rivers/streams (from OpenStreetMap). The logic:
water flows downhill and collects near river channels in low-lying ground,
so those two things together stand in for "how likely is this spot to
flood." The five files are severity tiers (common → extreme), playing the
same structural role as Team B's return periods, but they are **relative
susceptibility scores, not measured depths in metres.**

This proxy was checked against 24 of Nairobi's real, government-named flood
hotspots (`nairobi_hotspots_geocoded.csv`, see below) and correctly flagged
12 of them — mainly the Eastlands informal-settlement cluster along the
Nairobi River (Kayole, Njiru, Kiambiu, Mwiki, Mathare, Dandora, and others).
The 12 it missed (Kibera, Westlands, Lavington, Kitisuru, and other,
generally more affluent, suburbs) flood for a different reason — localized
drainage-system overload rather than terrain or river proximity — which
this kind of proxy structurally cannot see, no matter how it's tuned. That's
a real, explainable limit, not a bug to quietly patch. Team A should treat
this as a solid starting point and is encouraged to push it further — for
example, by finding data on Nairobi's stormwater drainage network, or by
treating "informal settlement vs. planned suburb" as its own signal.

**How you'll use it:** two options, in increasing order of effort.

1. **Fast start:** use `exposure_nairobi_with_hazard.csv` (see below) —
   every synthetic building already has its hazard score at all five tiers
   attached as columns. No raster lookup needed; go straight to step 2.
2. **From scratch:** use the plain `exposure_nairobi_synthetic.csv` and the
   five `.tif` files directly — for each building's coordinates, look up its
   score in each tier yourself. Do this if you want to build your own
   exposure set, or improve the hazard layer and need to re-sample it.

Either way, you'll need to make and state your own decision about how to
convert a 0–1 susceptibility score into something a damage function can use
(see step 2).

### `nairobi_hotspots_geocoded.csv`

24 of Nairobi's 37 government-flagged flood-prone neighbourhoods, with
their real coordinates (geocoded via OpenStreetMap). This is ground-truth
for checking whether your hazard layer makes sense — not exposure data, and
not a complete list of Nairobi's flood zones (13 of the 37 named areas
aren't in this file; geocoding the rest would strengthen validation further
if Team A has time for it).

### `exposure_nairobi_with_hazard.csv`

The convenience file: `exposure_nairobi_synthetic.csv` with each location's
hazard score at all five tiers already attached as columns
(`hazard_score_common`, `hazard_score_occasional`, ... `hazard_score_extreme`).
This is the same data as looking it up yourself from the five `.tif` files —
it just saves you writing that lookup. Recommended starting point.

---

## 2. Vulnerability — translate hazard severity into damage

This stage needs no downloaded dataset — it's a function you define:
"given this flood depth (or, for Team A, this susceptibility score), what
fraction of a building's value is destroyed?"

The standard shape, used by every real flood model, is a **depth-damage
curve**: damage starts near zero at shallow depth, rises steeply through a
middle range, and flattens out toward a ceiling (a building rarely loses
more than ~80–95% of its value even in a severe flood — the land and
foundation usually survive). The curve should differ by construction type:
an informal iron-sheet structure is damaged badly at a shallow depth; a
concrete/RCC building withstands much more before comparable damage.

The reference source for this shape is JRC's published global flood
depth-damage functions (a link is in the main problem statement) — you're
expected to adapt the *shape and logic* of a published curve, not invent
numbers from nothing, and to say clearly where your parameters are guesses
versus grounded in that reference.

**Team A's extra decision:** since your hazard layer is a 0–1 score, not a
depth in metres, you need to decide how severity maps to damage — for
example, treating the score as a proxy for depth on some assumed scale
(state the scale and why), or building damage tiers directly off the five
severity categories instead of a continuous depth curve. Either is
defensible if you say which you chose and why.

---

## 3. Exposure — what's actually at risk

### `exposure_nairobi_synthetic.csv` and `exposure_nzoia_synthetic.csv`

600 (Nairobi) and 500 (Nzoia) synthetic building locations respectively.
Columns: a location ID, latitude/longitude, a housing/construction class
(informal, semi-permanent, permanent masonry, or concrete/RCC), an
insured value in KES, and a `synthetic` flag set to `True` on every row.

This is **entirely generated data** — there is no real property portfolio
behind it, because none exists to give you (Kenya Re's own portfolio data is
explicitly out of scope for this challenge). Use it as-is, filter or reweight
it, or generate your own from scratch — but never present it, in your demo
or write-up, as if it were a real client's holdings. Say "synthetic" out
loud in your interface, not just in a footnote.

If your AI feature includes turning free-text property descriptions into
structured exposure rows, this file is also a useful reference for the
shape your output should take.

---

## 4. Financial engine — turn hazard + vulnerability + exposure into a number

For each building: look up its hazard severity at each return period (or
tier), run that through your vulnerability function to get a damage ratio,
multiply by the building's insured value to get a loss at that severity.
Do this for every building and sum them — that's your portfolio loss for
one scenario (say, the 1-in-100-year flood).

Do this across all six return periods (Team B) or all five tiers (Team A)
and you get a curve: loss on one axis, rarity (return period) on the other.
This is the **exceedance probability (EP) curve** — the single most
important output of any cat model, and the thing an underwriter actually
wants: "what loss should I budget for at the 1-in-100-year level? The
1-in-250?"

Team B's data already gives six clean return periods to build this curve
directly. Team A's five severity tiers don't carry an explicit "years" label the way
JRC's do. Deciding what return period each tier stands for, and stating
that assumption, is part of the work. Mind the direction: the tiers keep
fewer cells from common to extreme (the top 40% of cells down to the top
5%), and a rarer flood reaches more places. The widest map therefore stands
for the rarest event. Check that your loss rises as the event gets rarer.

---

## 5. The AI intelligence layer

This is the required differentiator — pick at least one of these, or
propose your own, as long as it materially changes the output rather than
just narrating it:

- **Free-text exposure ingestion** — a team member describes a portfolio in
  plain English ("12 iron-sheet shops near the river in Dandora"), and an
  LLM turns that into structured rows matching the exposure file's shape.
- **Natural-language risk briefing** — feed your model's output (stats, EP
  curve, top losses) to an LLM and have it write a short, plain-English
  summary an underwriter could actually read.
- **AI-assisted vulnerability research** — use an LLM to help find, compare,
  or adapt published depth-damage curve parameters, with the reasoning
  shown, not just a number pasted in.
- **Team A specifically** — AI-assisted hazard layer improvement: for
  example, using an LLM to help reason about which additional signals
  (river proximity, drainage infrastructure reports, more hotspot names)
  would improve the proxy, or to help process unstructured hazard-related
  text (news reports, county documents) into usable structured input.

---

## 6. Results interface

Present, at minimum: the stats (total exposure, loss at key return periods),
the EP curve, a breakdown by construction/housing class, and your AI
feature's output — somewhere a non-modeller could open it and understand it
in under two minutes. The framework doesn't matter; honesty about what's
real data versus assumption does.

---

## Quick reference — what's real, what's synthetic

| File | Real or synthetic |
|---|---|
| `team_b_nzoia/nzoia_rp*.tif` | Real (JRC) |
| `team_a_nairobi/nairobi_pluvial_proxy_*.tif` | Derived from real terrain + real river data (OSM); the hazard interpretation is a constructed proxy |
| `team_a_nairobi/nairobi_hotspots_geocoded.csv` | Real (government-named, geocoded — 24 of 37) |
| `exposure_nairobi_synthetic.csv` | Fully synthetic |
| `exposure_nairobi_with_hazard.csv` | Same synthetic locations, with real proxy hazard scores pre-attached — recommended starting file for Team A |
| `exposure_nzoia_synthetic.csv` | Fully synthetic |

Never present any synthetic file, or the proxy hazard layer, as real client
or measured data — say so out loud in your interface, not just in a footnote.
