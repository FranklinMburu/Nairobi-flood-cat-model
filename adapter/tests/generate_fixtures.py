"""Generate test fixtures for adapter tests."""

import pandas as pd
import numpy as np
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "adapter"
FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

# Load clean data (first 20 rows for testing)
df = pd.read_csv("data/exposure_nairobi_with_hazard.csv", dtype=str, keep_default_na=False)
df_small = df.head(20).copy()

# 1. clean.csv (already created)
df_small.to_csv(FIXTURE_DIR / "clean.csv", index=False, lineterminator="\n")
print("Created clean.csv")

# 2. scrambled_rename.csv - rename columns with synonyms
rename_map = {
    "loc_id": "location_id",
    "lat": "latitude",
    "lon": "longitude",
    "housing_class": "building_type",
    "floor_area_m2": "floor_area_sqm",
    "cost_per_m2_kes": "construction_cost_per_sqm_kes",
    "tiv_kes": "total_sum_insured_kes",
    "synthetic": "is_synthetic",
    "source": "data_source",
    "hazard_score_common": "hazard_common",
    "hazard_score_occasional": "hazard_occasional",
    "hazard_score_moderate": "hazard_moderate",
    "hazard_score_severe": "hazard_severe",
    "hazard_score_extreme": "hazard_extreme",
}
df_rename = df_small.rename(columns=rename_map)
df_rename.to_csv(FIXTURE_DIR / "scrambled_rename.csv", index=False, lineterminator="\n")
print("Created scrambled_rename.csv")

# 3. scrambled_units.csv - change units (exact decimals where possible)
df_units = df_small.copy()
# floor_area_m2 -> sqft (exact conversion: 1 m² = 10.7639104 sqft)
# Only convert rows where exact conversion gives clean numbers
# Actually, let's use exact decimal conversion for all
df_units["floor_area_m2"] = (df_units["floor_area_m2"].astype(float) * 10.7639104).round(4).astype(str)
# cost_per_m2_kes -> USD (divide by 130)
df_units["cost_per_m2_kes"] = (df_units["cost_per_m2_kes"].astype(float) / 130).round(2).astype(str)
# tiv_kes -> thousands (divide by 1000, only where divisible)
tiv_vals = df_units["tiv_kes"].astype(float)
df_units["tiv_kes"] = (tiv_vals / 1000).astype(str)  # All divisible by 1000 in test data
# hazard scores unchanged
df_units.to_csv(FIXTURE_DIR / "scrambled_units.csv", index=False, lineterminator="\n")
print("Created scrambled_units.csv")

# 4. shuffled_rows.csv - row shuffle
df_shuffle = df_small.sample(frac=1, random_state=42).reset_index(drop=True)
df_shuffle.to_csv(FIXTURE_DIR / "shuffled_rows.csv", index=False, lineterminator="\n")
print("Created shuffled_rows.csv")

# 5. messy_headers.csv - messy headers + junk columns
df_messy = df_small.copy()
messy_rename = {
    "loc_id": "Loc ID",
    "lat": "Latitude",
    "lon": "Longitude",
    "housing_class": "House Type",
    "floor_area_m2": "Floor Area (m2)",
    "cost_per_m2_kes": "Cost/m2 (KES)",
    "tiv_kes": "TSI",
    "synthetic": "Synthetic Flag",
    "source": "Source Dataset",
    "hazard_score_common": "Hazard Common",
    "hazard_score_occasional": "Hazard Occasional",
    "hazard_score_moderate": "Hazard Moderate",
    "hazard_score_severe": "Hazard Severe",
    "hazard_score_extreme": "Hazard Extreme",
}
df_messy = df_messy.rename(columns=messy_rename)
# Add junk columns
df_messy["junk_col_1"] = "ignore"
df_messy["random_data"] = np.random.randint(0, 100, len(df_messy))
df_messy["notes"] = "some notes"
df_messy.to_csv(FIXTURE_DIR / "messy_headers.csv", index=False, lineterminator="\n")
print("Created messy_headers.csv")

# 6. missing_class.csv - no housing_class column
df_missing_class = df_small.drop(columns=["housing_class"])
df_missing_class.to_csv(FIXTURE_DIR / "missing_class.csv", index=False, lineterminator="\n")
print("Created missing_class.csv")

# 7. missing_hazard.csv - no hazard score columns
df_missing_hazard = df_small.drop(columns=[
    "hazard_score_common", "hazard_score_occasional", "hazard_score_moderate",
    "hazard_score_severe", "hazard_score_extreme"
])
df_missing_hazard.to_csv(FIXTURE_DIR / "missing_hazard.csv", index=False, lineterminator="\n")
print("Created missing_hazard.csv")

# 8. swapped_coords.csv - lat/lon swapped
df_swapped = df_small.copy()
df_swapped["lat"], df_swapped["lon"] = df_swapped["lon"], df_swapped["lat"]
df_swapped.to_csv(FIXTURE_DIR / "swapped_coords.csv", index=False, lineterminator="\n")
print("Created swapped_coords.csv")

# 9. xlsx_input.xlsx
df_small.to_excel(FIXTURE_DIR / "xlsx_input.xlsx", index=False)
print("Created xlsx_input.xlsx")

# 10. geojson_points.geojson
import json
features = []
for _, row in df_small.iterrows():
    features.append({
        "type": "Feature",
        "properties": {k: v for k, v in row.items() if k not in ["lat", "lon"]},
        "geometry": {
            "type": "Point",
            "coordinates": [float(row["lon"]), float(row["lat"])]
        }
    })
geojson = {
    "type": "FeatureCollection",
    "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
    "features": features
}
with open(FIXTURE_DIR / "geojson_points.geojson", "w") as f:
    json.dump(geojson, f)
print("Created geojson_points.geojson")

# 11. geojson_polygons.geojson - simple square polygons around each point
features_poly = []
for _, row in df_small.iterrows():
    lon = float(row["lon"])
    lat = float(row["lat"])
    # Small square ~200m x 200m (~0.002 deg)
    d = 0.001
    coords = [[
        [lon - d, lat - d],
        [lon + d, lat - d],
        [lon + d, lat + d],
        [lon - d, lat + d],
        [lon - d, lat - d]
    ]]
    features_poly.append({
        "type": "Feature",
        "properties": {k: v for k, v in row.items() if k not in ["lat", "lon"]},
        "geometry": {
            "type": "Polygon",
            "coordinates": coords
        }
    })
geojson_poly = {
    "type": "FeatureCollection",
    "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
    "features": features_poly
}
with open(FIXTURE_DIR / "geojson_polygons.geojson", "w") as f:
    json.dump(geojson_poly, f)
print("Created geojson_polygons.geojson")

# 12. missing_tiv.csv - no tiv_kes column
df_missing_tiv = df_small.drop(columns=["tiv_kes"])
df_missing_tiv.to_csv(FIXTURE_DIR / "missing_tiv.csv", index=False, lineterminator="\n")
print("Created missing_tiv.csv")

# 13. undeclared_synthetic.csv - no synthetic column
df_no_synth = df_small.drop(columns=["synthetic"])
df_no_synth.to_csv(FIXTURE_DIR / "undeclared_synthetic.csv", index=False, lineterminator="\n")
print("Created undeclared_synthetic.csv")

# 14. usd_cost.csv - cost_per_m2_kes in USD (values ~50-600)
df_usd = df_small.copy()
df_usd["cost_per_m2_kes"] = (df_usd["cost_per_m2_kes"].astype(float) / 130).round(2).astype(str)
df_usd.to_csv(FIXTURE_DIR / "usd_cost.csv", index=False, lineterminator="\n")
print("Created usd_cost.csv")

print("\nAll fixtures created!")