# Provenance

Every value below was measured on the copy in this repository on 2026-10-08. All files were
copied byte-for-byte from the organizer kit and project workspace, and each copy's SHA-256 was
confirmed equal to its source.

## Source dataset

| Field | Value |
|---|---|
| Filename | `data/exposure_nairobi_with_hazard.csv` |
| SHA-256 | `b60aa96590d5a2e71509579d50c18d4ebd3557e26d2c60505f89d20bee69aa48` |
| Row count | 600 |
| Total TIV (`tiv_kes`) | KES 63,635,075,000 |
| Source status | Supplied by the hackathon organizers |
| Synthetic | Yes. `synthetic` is True on every row |
| Step 1 validation | V1 to V11 all pass, no warnings |

Notes:

- The organizers confirmed that the `tiv_kes` values in this file are to be used exactly as
  supplied (D-001). They are not scaled, corrected or recomputed from floor area and cost per m².
- The hazard scores in this file are a relative susceptibility proxy, not flood depths (D-002).

## Frozen documents

The Markdown file is the canonical text of each document. The .docx file is a copy of it.

| File | Revision | SHA-256 |
|---|---|---|
| `docs/specifications/07 - Deterministic Loss Engine Specification (Revision 2).md` | Revision 2 | `8d69563a797fd09c290f3220e770f9a0dcc3714b4311f64a9096b0c45e046e1a` |
| `docs/specifications/07 - Deterministic Loss Engine Specification (Revision 2).docx` | Revision 2 | `071721d369ed2891978e540aa8e8918d824c9a1da62387e151eff1f21cb56e2b` |
| `docs/decisions/06 - Decision Record.md` | D-001 to D-005 | `b39ff2e93009415e05490959b9676ed03113df847fbfdb430f4ec6657c3a2184` |
| `docs/decisions/06 - Decision Record.docx` | D-001 to D-005 | `fcdbbb52c7cbc88bc0f8b3af02268a68d83c79763a606b847f3071a30e4b198e` |

## Other supplied data

All supplied by the hackathon organizers and unchanged.

| File | SHA-256 |
|---|---|
| `data/exposure_nairobi_synthetic.csv` | `9fa684d9a888f9e50638c1283058c3c931b03abfce82504ea0fe672e9378416a` |
| `data/nairobi_hotspots_geocoded.csv` | `b597d843f80a800de82ed482f177f2acfc9339758276d57053eb593343dac7c3` |
| `data/nairobi_pluvial_proxy_extreme.tif` | `dc83cf80780c7d83c0da803f3eb1bfeb6a7bcea8b9debe76d438a864a6466728` |
| `data/nairobi_pluvial_proxy_severe.tif` | `92d6d5ccc2930fcc40edb69d5ff3304a7ce60028586779075666845e01910b74` |
| `data/nairobi_pluvial_proxy_moderate.tif` | `2e6eba35dbbfec973591084f2979f0fbce24eee6af19365881118796c0de22ce` |
| `data/nairobi_pluvial_proxy_occasional.tif` | `1a6f0e0d911ad5ccadc5345af514d5b01b43dedd1c1bfef8e4d07c5f0e710cb1` |
| `data/nairobi_pluvial_proxy_common.tif` | `2071d44cf0ec9b06d240196e56bcd7915829d53cc6d186b033e89fce5afd1834` |

## Reference documents

| File | Source | SHA-256 |
|---|---|---|
| `docs/reference/Team_A_Nairobi_Problem_Statement.docx` | Hackathon organizers | `de599dc57e598928ec62d155df847bbb8946509220a13d3e75fe2e8b8b829857` |
| `docs/reference/Dataset_Metadata.docx` | Hackathon organizers | `b2bf87f176550192820e33f1d26676e008a5dc5d7d85dd2f395bcb502d1c621f` |
| `docs/reference/STEP_BY_STEP_GUIDE.md` | Hackathon organizers | `b2eaffd3213d780b19f7213d43a0b84ea0401980447a2432b400229e9a89d581` |

`Dataset_Metadata.docx` states a total TIV that the organizers have confirmed is a documentation
error. Use the CSV values, not the metadata figure (D-001).
