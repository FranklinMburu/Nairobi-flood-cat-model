"""Tests of the workflow API. The AI step is the bundled hand-written replay; no provider is called."""

import io
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from accounts.models import User

EXAMPLE = Path(settings.WORKFLOW_EXAMPLE_TEXT)
ITEM2_FLAGS = ["item-2|housing_class|mapping_needs_confirmation", "item-2|insured_value|basis_unsupported"]
SPEC_7_3_REFERENCE = {"extreme": 468_443_071.72, "severe": 826_175_003.83, "moderate": 1_972_325_986.45,
                      "occasional": 3_279_344_346.85, "common": 5_103_936_640.53}
APPROVE = {"decision": "approve", "acknowledged": ITEM2_FLAGS, "excluded_items": ["item-3"], "corrections": []}


class WorkflowApiTests(TestCase):
    def setUp(self):
        self.store = Path(tempfile.mkdtemp(prefix="wf-store-"))
        self.addCleanup(shutil.rmtree, self.store, True)
        override = override_settings(WORKFLOW_STORE_DIR=self.store)
        override.enable()
        self.addCleanup(override.disable)
        env = mock.patch.dict("os.environ", {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in ("GEMINI_MODEL", "GEMINI_API_KEY", "ADAPTER_API_KEY"):
            import os
            os.environ.pop(name, None)
        self.user = User.objects.create_user(email="reviewer@example.com", name="Rev Iewer", password="x" * 16)
        self.client = Client()
        self.client.force_login(self.user)

    def post(self, path, payload):
        return self.client.post(path, json.dumps(payload), content_type="application/json")

    def example(self):
        response = self.post("/api/workflow/submissions/", {"source": "example"})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def approve(self, sid, payload=APPROVE):
        return self.post(f"/api/workflow/submissions/{sid}/decisions/", payload)

    # --- access ----------------------------------------------------------------------

    def test_every_endpoint_needs_a_session(self):
        anonymous = Client()
        for method, path in [("get", "/api/workflow/capabilities/"), ("get", "/api/workflow/submissions/"),
                             ("post", "/api/workflow/submissions/"), ("get", "/api/runs/"), ("post", "/api/runs/"),
                             ("post", "/api/ingest/run/"), ("get", "/api/workflow/example/")]:
            self.assertEqual(getattr(anonymous, method)(path).status_code, 401, path)

    def test_posts_need_the_csrf_token(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        response = strict.post("/api/workflow/submissions/", json.dumps({"source": "example"}),
                               content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse((self.store / "submissions").exists())

    def test_capabilities_never_reveal_credentials(self):
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "secret-gemini", "GEMINI_MODEL": "gemini-x",
                                            "ADAPTER_API_KEY": "secret-adapter"}):
            body = self.client.get("/api/workflow/capabilities/").content.decode()
        self.assertNotIn("secret-gemini", body)
        self.assertNotIn("secret-adapter", body)
        data = json.loads(body)
        self.assertTrue(data["live_ai"]["configured"] and data["ingest"]["configured"] and data["replay_example"])

    # --- stage 1 ---------------------------------------------------------------------

    def test_example_submission_is_a_labelled_replay_awaiting_approval(self):
        data = self.example()
        self.assertEqual(data["status"], "awaiting_approval")
        self.assertEqual(data["extraction"]["execution_mode"], "replay")
        self.assertEqual((data["extraction"]["provider"], data["extraction"]["model"]), ("replay", "hand-written-response"))
        self.assertIsNotNone(data["extraction"]["replay_of"])
        self.assertEqual(data["verification_outcome"], "needs_review")
        self.assertEqual(data["document"]["text"], EXAMPLE.read_text(encoding="utf-8"))
        meta = data["document"]["metadata"]
        self.assertEqual(meta["uploaded_by"], "reviewer@example.com")
        self.assertEqual(meta["original_filename"], EXAMPLE.name)
        self.assertEqual(len(meta["original_file_sha256"]), 64)
        self.assertEqual(data["requirements"]["unresolved"], {"item-3": ["tiv_kes_per_building"]})
        self.assertTrue((self.store / "submissions" / data["id"] / "ai_extraction.json").is_file())
        listed = self.client.get("/api/workflow/submissions/").json()["submissions"]
        self.assertEqual([s["id"] for s in listed], [data["id"]])

    def test_uploaded_file_of_the_example_text_replays_too(self):
        upload = SimpleUploadedFile("schedule.txt", EXAMPLE.read_bytes(), content_type="text/plain")
        data = self.client.post("/api/workflow/submissions/", {"file": upload}).json()
        self.assertEqual(data["extraction"]["execution_mode"], "replay")
        self.assertEqual(data["document"]["metadata"]["original_filename"], "schedule.txt")

    def test_other_text_without_a_provider_is_refused_and_not_saved(self):
        response = self.post("/api/workflow/submissions/", {"text": "Two houses in Kayole, KES 1m each."})
        self.assertEqual(response.status_code, 503)
        self.assertIn("No AI provider", response.json()["error"])
        self.assertFalse((self.store / "submissions").exists())

    def test_live_provider_without_key_is_a_recorded_failure_that_cannot_be_approved(self):
        with mock.patch.dict("os.environ", {"GEMINI_MODEL": "gemini-test"}):
            data = self.post("/api/workflow/submissions/", {"text": "Two houses in Kayole, KES 1m each."}).json()
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["extraction"]["execution_mode"], "live")
        self.assertIn("credentials unavailable", data["extraction"]["error"])
        self.assertIsNone(data["document"]["metadata"]["original_file_sha256"])
        response = self.approve(data["id"])
        self.assertEqual(response.status_code, 400)

    def test_non_utf8_file_and_empty_text_are_refused(self):
        bad = SimpleUploadedFile("x.pdf", b"%PDF-\xff\xfe", content_type="application/pdf")
        self.assertEqual(self.client.post("/api/workflow/submissions/", {"file": bad}).status_code, 415)
        self.assertEqual(self.post("/api/workflow/submissions/", {"text": "   "}).status_code, 400)
        self.assertEqual(self.post("/api/workflow/submissions/", {"nothing": 1}).status_code, 400)

    def test_unknown_or_malformed_ids_are_not_found(self):
        for sid in ("ext-20261009T000000Z-00000000", "..%2F..%2Fdata", "x"):
            self.assertEqual(self.client.get(f"/api/workflow/submissions/{sid}/").status_code, 404)
        self.assertEqual(self.client.get("/api/runs/run-1/").status_code, 404)

    # --- approval gate -----------------------------------------------------------------

    def test_model_cannot_run_before_a_valid_approval(self):
        sid = self.example()["id"]
        self.assertEqual(self.client.post(f"/api/workflow/submissions/{sid}/run/").status_code, 409)
        missing_ack = self.approve(sid, {**APPROVE, "acknowledged": ITEM2_FLAGS[:1]})
        self.assertEqual(missing_ack.status_code, 400)
        self.assertIn("acknowledged", missing_ack.json()["error"])
        unsplit_total = self.approve(sid, {**APPROVE, "excluded_items": []})
        self.assertEqual(unsplit_total.status_code, 400)
        no_reason = self.post(f"/api/workflow/submissions/{sid}/decisions/", {"decision": "reject"})
        self.assertEqual(no_reason.status_code, 400)
        bad_correction = self.approve(sid, {**APPROVE, "corrections": [{"item_id": "item-1", "field": "tiv_kes",
                                                                        "value": 1, "reason": "x"}]})
        self.assertEqual(bad_correction.status_code, 400)
        self.assertEqual(self.client.post(f"/api/workflow/submissions/{sid}/run/").status_code, 409)
        self.assertFalse((self.store / "submissions" / sid / "runs").exists())

    def test_review_request_then_rejection_never_run(self):
        sid = self.example()["id"]
        data = self.post(f"/api/workflow/submissions/{sid}/decisions/",
                         {"decision": "request_review", "reason": "check item 2"}).json()
        self.assertEqual(data["status"], "review_requested")
        data = self.post(f"/api/workflow/submissions/{sid}/decisions/",
                         {"decision": "reject", "reason": "not confirmed"}).json()
        self.assertEqual(data["status"], "rejected_by_reviewer")
        self.assertEqual(len(data["decisions"]), 2)
        self.assertEqual(self.client.post(f"/api/workflow/submissions/{sid}/run/").status_code, 409)

    def test_approval_from_another_submission_is_stale(self):
        first, second = self.example()["id"], self.example()["id"]
        self.assertEqual(self.approve(first).status_code, 201)
        decision = next((self.store / "submissions" / first / "decisions").glob("apr-*.json"))
        (self.store / "submissions" / second / "decisions").mkdir()
        shutil.copy(decision, self.store / "submissions" / second / "decisions" / decision.name)
        self.assertEqual(self.client.get(f"/api/workflow/submissions/{second}/").json()["status"], "stale_approval")
        response = self.client.post(f"/api/workflow/submissions/{second}/run/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("stale", response.json()["error"])

    def test_edited_verification_report_blocks_decisions_and_runs(self):
        sid = self.example()["id"]
        path = self.store / "submissions" / sid / "verification_report.json"
        path.write_text(path.read_text(encoding="utf-8").replace("needs_review", "valid_candidate"), encoding="utf-8")
        self.assertEqual(self.client.get(f"/api/workflow/submissions/{sid}/").json()["status"], "integrity_error")
        self.assertEqual(self.approve(sid).status_code, 409)

    # --- approved run -----------------------------------------------------------------

    def test_approved_submission_runs_the_engine_and_links_provenance(self):
        sid = self.example()["id"]
        approved = self.approve(sid).json()
        self.assertEqual(approved["status"], "approved")
        latest = approved["decisions"][-1]
        self.assertEqual(latest["approver"], "Rev Iewer <reviewer@example.com>")
        self.assertIs(latest["approver_verified"], False)
        response = self.client.post(f"/api/workflow/submissions/{sid}/run/")
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(data["status"], "completed")
        run = data["run"]
        reference = {r["tier"]: float(r["baseline_portfolio_loss_kes"]) for r in run["comparison"]
                     if r["scenario_id"] == "reference"}
        for tier, expected in SPEC_7_3_REFERENCE.items():
            self.assertAlmostEqual(reference[tier], expected, delta=1.0)
        for row in run["comparison"]:
            marginal = float(row["with_account_portfolio_loss_kes"]) - float(row["baseline_portfolio_loss_kes"])
            self.assertAlmostEqual(float(row["marginal_portfolio_loss_kes"]), marginal, delta=0.01)
        self.assertEqual(len(run["account_exposure"]), 5)
        self.assertTrue(all(0 <= float(r["hazard_score_common"]) <= 1 for r in run["account_exposure"]))
        record = run["workflow_record"]
        self.assertEqual(record["approval"]["approval_id"], latest["approval_id"])
        self.assertEqual(record["source_document"]["document_id"], data["document"]["document_id"])
        self.assertEqual(record["ai_extraction"]["execution_mode"], "replay")
        self.assertEqual({t["item_status"] for t in record["unmodelled_terms"]}, {"included", "excluded"})
        again = self.client.post(f"/api/workflow/submissions/{sid}/run/")
        self.assertEqual(again.status_code, 409)
        self.assertEqual(len(list((self.store / "submissions" / sid / "runs").iterdir())), 1)

    # --- reference runs ---------------------------------------------------------------

    def test_reference_run_is_recorded_and_reproduces_specification_7_3(self):
        self.assertEqual(self.client.get("/api/runs/").json(), {"runs": []})
        response = self.client.post("/api/runs/")
        self.assertEqual(response.status_code, 201)
        record = response.json()
        self.assertEqual((record["status"], record["row_count"]), ("completed", 600))
        self.assertEqual(len(record["record_sha256"]), 64)
        losses = {r["tier"]: r["portfolio_loss_kes"] for r in record["tier_summary"] if r["scenario_id"] == "reference"}
        for tier, expected in SPEC_7_3_REFERENCE.items():
            self.assertAlmostEqual(losses[tier], expected, delta=1.0)
        self.assertEqual(len([p for p in record["ep_points"] if p["scenario_id"] == "reference"]), 5)
        listed = self.client.get("/api/runs/").json()["runs"]
        self.assertEqual([r["run_id"] for r in listed], [record["run_id"]])
        self.assertEqual(self.client.get(f"/api/runs/{record['run_id']}/").json()["record_sha256"],
                         record["record_sha256"])

    # --- upload service proxy ---------------------------------------------------------

    def test_ingest_without_configuration_or_service_is_unavailable(self):
        upload = SimpleUploadedFile("p.csv", b"a,b\n1,2\n")
        self.assertEqual(self.client.post("/api/ingest/run/", {"file": upload}).status_code, 503)
        with mock.patch.dict("os.environ", {"ADAPTER_API_KEY": "k"}), \
                override_settings(INGEST_API_URL="http://127.0.0.1:9"):
            upload = SimpleUploadedFile("p.csv", b"a,b\n1,2\n")
            response = self.client.post("/api/ingest/run/", {"file": upload})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.post("/api/ingest/other/", {}).status_code, 404)

    def test_ingest_forwards_with_the_server_key_and_adds_the_file_identity(self):
        seen = {}

        class Reply(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout):
            seen["url"], seen["key"], seen["body"] = request.full_url, request.get_header("X-api-key"), request.data
            return Reply(json.dumps({"adapter_status": "accepted", "run_status": "completed"}).encode())

        with mock.patch.dict("os.environ", {"ADAPTER_API_KEY": "server-key"}), \
                mock.patch("workflow.views.urllib.request.urlopen", fake_urlopen):
            upload = SimpleUploadedFile("portfolio.csv", b"loc_id\nA\n")
            response = self.client.post("/api/ingest/run/", {"file": upload, "synthetic": "true"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["run_status"], "completed")
        self.assertEqual(body["original_file"]["filename"], "portfolio.csv")
        self.assertEqual(body["original_file"]["uploaded_by"], "reviewer@example.com")
        self.assertTrue(seen["url"].endswith("/run") and seen["key"] == "server-key")
        self.assertIn(b'name="synthetic"', seen["body"])
        self.assertNotIn("server-key", response.content.decode())
