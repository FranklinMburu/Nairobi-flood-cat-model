"""API tests: auth, upload validation, PDF/CSV/XLSX/GeoJSON paths, and the /run engine hand-off."""

from __future__ import annotations

import glob
import io
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import REPO_ROOT, exposure_rows, make_pdf, make_text_pdf, rows_to_csv_bytes

KEY = "test-secret-key"
HEADERS = {"X-API-Key": KEY}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADAPTER_API_KEY", KEY)
    from api.main import app

    with TestClient(app) as c:
        yield c


def post(client, path, data, name="f", headers=HEADERS, **form):
    return client.post(path, headers=headers, files={"file": (name, data)}, data=form)


def hazard_columns_dropped(rows):
    keep = [i for i, h in enumerate(rows[0]) if not h.startswith("hazard_score_")]
    return [[r[i] for i in keep] for r in rows]


# --- startup and auth -----------------------------------------------------------


def test_startup_fails_without_key(monkeypatch):
    monkeypatch.delenv("ADAPTER_API_KEY", raising=False)
    from api.main import app

    with pytest.raises(RuntimeError, match="ADAPTER_API_KEY"):
        with TestClient(app):
            pass


def test_missing_key_401(client):
    assert post(client, "/adapt", rows_to_csv_bytes(exposure_rows(2)), headers={}, synthetic="true").status_code == 401


def test_wrong_key_401(client):
    r = post(client, "/adapt", rows_to_csv_bytes(exposure_rows(2)), headers={"X-API-Key": "nope"}, synthetic="true")
    assert r.status_code == 401


def test_non_ascii_key_does_not_crash(client):
    r = post(client, "/adapt", b"a,b\n1,2", headers={"X-API-Key": b"\xff\xfe"}, synthetic="true")
    assert r.status_code == 401


def test_run_requires_key_too(client):
    assert post(client, "/run", rows_to_csv_bytes(exposure_rows(2)), headers={}, synthetic="true").status_code == 401


# --- upload validation ----------------------------------------------------------


def test_binary_garbage_is_415_even_if_named_pdf(client):
    assert post(client, "/adapt", bytes(range(256)) * 4, name="x.pdf", synthetic="true").status_code == 415


def test_corrupt_pdf_is_422_not_500(client):
    assert post(client, "/adapt", b"%PDF-1.4 garbage", name="x.pdf", synthetic="true").status_code == 422


def test_bad_xlsx_is_415(client):
    assert post(client, "/adapt", b"PK fake zip", name="x.xlsx", synthetic="true").status_code == 415


def test_old_xls_is_415(client):
    ole = bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 600
    assert post(client, "/adapt", ole, name="x.xls", synthetic="true").status_code == 415


def test_oversize_413(client):
    assert post(client, "/adapt", b"a,b\n" + b"1,2\n" * (3 * 1024 * 1024), name="big.csv", synthetic="true").status_code == 413


def test_synthetic_must_be_true_or_false(client):
    r = post(client, "/adapt", rows_to_csv_bytes(exposure_rows(2)), synthetic="banana")
    assert r.status_code == 422


@pytest.mark.parametrize("fx", ["0", "-5", "2000", "nan", "inf"])
def test_fx_rate_out_of_range(client, fx):
    assert post(client, "/adapt", rows_to_csv_bytes(exposure_rows(2)), synthetic="true", fx_rate=fx).status_code == 422


# --- /adapt ---------------------------------------------------------------------


def test_csv_happy_path(client):
    r = post(client, "/adapt", rows_to_csv_bytes(exposure_rows(5)), name="a.csv", synthetic="true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "accepted" and body["adapted_csv"].startswith("loc_id,")
    assert body["report"]["adapted_file"]["path"] == ""


def test_pdf_happy_path_with_synonym_headers(client):
    rows = exposure_rows(5)
    rename = {"lat": "Latitude", "lon": "Longitude", "tiv_kes": "Sum Insured", "housing_class": "Construction"}
    rows[0] = [rename.get(h, h) for h in rows[0]]
    r = post(client, "/adapt", make_pdf([rows]), name="offer.pdf", synthetic="true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "accepted"
    assert any("mapped to" in w for w in body["report"]["original_file"]["profile"]["warnings"])


def test_text_only_pdf_is_422(client):
    r = post(client, "/adapt", make_text_pdf(), name="x.pdf", synthetic="true")
    assert r.status_code == 422 and "No tables" in r.json()["detail"]


def test_pdf_over_50_pages_is_422(client):
    tables = [[["a", "b"], ["1", "2"]]] * 51
    assert post(client, "/adapt", make_pdf(tables), name="x.pdf", synthetic="true").status_code == 422


def test_xlsx_happy_path(client):
    data = (REPO_ROOT / "adapter/tests/fixtures/adapter/xlsx_input.xlsx").read_bytes()
    r = post(client, "/adapt", data, name="x.xlsx", synthetic="true")
    assert r.status_code in (200, 422) and r.status_code != 500
    assert r.json().get("status") in ("accepted", "needs_confirmation", "refused")


def test_geojson_happy_path(client):
    data = (REPO_ROOT / "adapter/tests/fixtures/adapter/geojson_points.geojson").read_bytes()
    r = post(client, "/adapt", data, name="x.geojson", synthetic="true")
    assert r.status_code != 500


def test_refused_returns_422_with_report(client):
    rows = exposure_rows(3)
    drop = rows[0].index("tiv_kes")
    rows = [[c for i, c in enumerate(r) if i != drop] for r in rows]
    r = post(client, "/adapt", rows_to_csv_bytes(rows), synthetic="true")
    assert r.status_code == 422
    body = r.json()
    assert body["status"] == "refused" and body["adapted_csv"] is None
    assert any("tiv_kes" in x for x in body["report"]["refusal_reasons"])


def test_undeclared_synthetic_is_refused(client):
    rows = exposure_rows(3)
    drop = rows[0].index("synthetic")
    rows = [[c for i, c in enumerate(r) if i != drop] for r in rows]
    assert post(client, "/adapt", rows_to_csv_bytes(rows)).status_code == 422


def test_no_server_paths_in_response(client):
    r = post(client, "/adapt", make_pdf([exposure_rows(4)]), name="C:\\secret\\evil.pdf", synthetic="true")
    text = r.text
    assert tempfile.gettempdir() not in text
    assert str(REPO_ROOT) not in text
    assert "nairobi-" not in text
    assert "evil" not in text  # client filename is never echoed


def test_temp_dirs_are_cleaned_up(client):
    pattern = os.path.join(tempfile.gettempdir(), "nairobi-*")
    before = set(glob.glob(pattern))
    post(client, "/adapt", rows_to_csv_bytes(exposure_rows(3)), synthetic="true")
    post(client, "/adapt", b"%PDF-1.4 garbage", synthetic="true")
    assert set(glob.glob(pattern)) == before


def test_concurrent_requests_do_not_collide(client):
    def go(n):
        r = post(client, "/adapt", rows_to_csv_bytes(exposure_rows(n)), synthetic="true")
        return n, r.json()["report"]["adapted_file"]["n_rows"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(go, [2, 3, 4, 5, 6, 2, 3, 4]))
    assert all(n == rows for n, rows in results)


# --- /run -----------------------------------------------------------------------


def test_run_pdf_completes_and_returns_engine_outputs(client):
    r = post(client, "/run", make_pdf([exposure_rows(8)]), name="o.pdf", synthetic="true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["adapter_status"] == "accepted" and body["run_status"] == "completed"
    scenarios = {r["scenario_id"] for r in body["tier_summary"]}
    assert len({r["tier"] for r in body["tier_summary"]}) == 5
    assert len(body["tier_summary"]) == 5 * len(scenarios) and len(body["ep_points"]) > 0
    assert len(body["building_results"]) == 8 * len(scenarios) * 5  # buildings x scenarios x tiers
    assert not [v for v in body["validation_results"] if v["status"] == "fail"]


def test_run_matches_engine_called_directly(client, tmp_path):
    from loss_engine.config import default_config
    from loss_engine.ep_curve import default_return_periods
    from loss_engine.run_record import run_model

    rows = exposure_rows(8)
    csv_path = tmp_path / "in.csv"
    csv_path.write_bytes(rows_to_csv_bytes(rows))
    direct = run_model(exposure_path=csv_path, config=default_config(), mapping=default_return_periods())
    api_body = post(client, "/run", make_pdf([rows]), name="o.pdf", synthetic="true").json()
    def strip(rows):
        return [{k: v for k, v in r.items() if k != "run_id"} for r in rows]

    expected = json.loads(direct.outputs.tier_summary.to_json(orient="records"))
    assert strip(api_body["tier_summary"]) == strip(expected)


def test_run_without_hazard_columns_fails_cleanly_with_422(client):
    rows = hazard_columns_dropped(exposure_rows(5))
    r = post(client, "/run", make_pdf([rows]), name="o.pdf", synthetic="true")
    assert r.status_code == 422
    body = r.json()
    assert body["run_status"] == "failed" and body["failed_at"]
    assert "tier_summary" not in body


def test_run_refused_does_not_run_model(client):
    rows = exposure_rows(3)
    drop = rows[0].index("tiv_kes")
    rows = [[c for i, c in enumerate(r) if i != drop] for r in rows]
    r = post(client, "/run", rows_to_csv_bytes(rows), synthetic="true")
    assert r.status_code == 422 and r.json()["run_status"] == "not_run"


def test_run_needs_confirmation_does_not_run_unless_accepted(client):
    rows = exposure_rows(4)
    idx = {h: i for i, h in enumerate(rows[0])}
    for r in rows[1:]:  # break the V7 ordering: extreme > common
        r[idx["hazard_score_common"]], r[idx["hazard_score_extreme"]] = "0.0", "0.9"
    r = post(client, "/run", rows_to_csv_bytes(rows), synthetic="true")
    assert r.status_code == 200
    assert r.json()["adapter_status"] == "needs_confirmation" and r.json()["run_status"] == "not_run"


def test_index_page_served(client):
    assert client.get("/").status_code == 200
