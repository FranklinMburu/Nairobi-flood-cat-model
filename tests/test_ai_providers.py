"""Tests for the AI providers (Checkpoint 8). No test calls a real provider.

Gemini is exercised through a fake `urlopen`; the replay uses the bundled
hand-written fixture, which records provider "fixture" and was never produced
by an AI.
"""

import io
import json
import socket
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loss_engine.ai_providers import (  # noqa: E402
    PROMPT_TEXT,
    PROMPT_VERSION,
    GeminiProvider,
    ReplayProvider,
    build_exposure_request,
    render_prompt,
    run_extraction,
)
from loss_engine.ai_records import AIProviderResult, AIRequest, SourceDocument, read_record, AIExtractionRecord  # noqa: E402
from loss_engine.exposure_extraction import EXPOSURE_SCHEMA, SCHEMA_VERSION  # noqa: E402

EXAMPLES = ROOT / "examples"
FIXTURE = EXAMPLES / "demo_replay_record.json"


@pytest.fixture
def document():
    return SourceDocument.from_text((EXAMPLES / "demo_submission.txt").read_text(encoding="utf-8"),
                                    "exposure_text", "test")


@pytest.fixture
def request_(document):
    return build_exposure_request(document)


# --- The request -------------------------------------------------------------------------


def test_request_carries_fixed_prompt_and_schema(request_, document):
    assert request_.mode == "exposure"
    assert request_.prompt_version == PROMPT_VERSION
    assert request_.schema_version == SCHEMA_VERSION
    assert request_.documents == (document,)
    again = build_exposure_request(document)
    assert (again.prompt_sha256, again.schema_sha256) == (request_.prompt_sha256, request_.schema_sha256)


def test_rendered_prompt_contains_instructions_schema_and_exact_text(request_, document):
    text = render_prompt(request_)
    assert text.startswith(PROMPT_TEXT)
    assert '"insured_value"' in text
    assert document.text in text


def test_prompt_forbids_guessing_and_splitting():
    assert "Never guess" in PROMPT_TEXT and "Never divide a total" in PROMPT_TEXT


# --- Replay ------------------------------------------------------------------------------


def test_fixture_is_labelled_as_hand_written_not_ai():
    recorded = read_record(FIXTURE, AIExtractionRecord)
    assert (recorded.provider, recorded.model, recorded.execution_mode) == ("fixture", "hand-written-response", "replay")
    assert recorded.replay_of is not None


def test_replay_returns_the_recorded_response(request_):
    provider = ReplayProvider.from_file(FIXTURE)
    record = run_extraction(provider, request_)
    recorded = provider.recorded
    assert record.status == "ok"
    assert record.execution_mode == "replay" and record.provider == "replay"
    assert record.replay_of == recorded.extraction_id != record.extraction_id
    assert record.response_text == recorded.response_text
    assert record.response_sha256 == recorded.response_sha256


def test_replay_refuses_another_document(request_):
    other = SourceDocument.from_text("A different submission.", "exposure_text", "test")
    record = run_extraction(ReplayProvider.from_file(FIXTURE), build_exposure_request(other))
    assert record.status == "error" and "documents" in record.error
    assert record.response_text is None


def test_replay_refuses_another_prompt(document):
    changed = AIRequest("exposure", "exposure-prompt-x", PROMPT_TEXT + " Extra.", SCHEMA_VERSION, EXPOSURE_SCHEMA,
                        (document,))
    record = run_extraction(ReplayProvider.from_file(FIXTURE), changed)
    assert record.status == "error" and "prompt" in record.error


def test_only_a_successful_record_can_be_replayed(request_):
    failed = run_extraction(GeminiProvider("m", api_key=""), request_)
    with pytest.raises(ValueError):
        ReplayProvider(failed)


# --- Gemini, with a fake transport -------------------------------------------------------


class FakeResponse:
    def __init__(self, body: bytes, headers=None):
        self._body, self.headers = body, headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake(payload=None, *, raw=None, error=None, calls=None):
    def urlopen(request, timeout):
        if calls is not None:
            calls.append((request, timeout))
        if error is not None:
            raise error
        return FakeResponse(raw if raw is not None else json.dumps(payload).encode(), {"x-request-id": "req-1"})
    return urlopen


def reply(text, finish="STOP"):
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]}


def test_gemini_success_is_recorded_as_live(request_):
    calls = []
    provider = GeminiProvider("gemini-test-model", api_key="secret-key", urlopen=fake(reply('{"items": []}'), calls=calls))
    record = run_extraction(provider, request_)
    assert record.status == "ok" and record.execution_mode == "live" and record.provider == "gemini"
    assert record.model == "gemini-test-model" and record.request_id == "req-1" and record.stop_reason == "STOP"
    assert record.response_text == '{"items": []}'
    (http, timeout), = calls
    assert "gemini-test-model:generateContent" in http.full_url
    assert "secret-key" not in http.full_url
    assert http.get_header("X-goog-api-key") == "secret-key"
    body = json.loads(http.data)
    assert body["generationConfig"] == {"responseMimeType": "application/json", "temperature": 0}
    assert "secret-key" not in record.to_json()


def test_gemini_without_credentials_makes_no_call(request_, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    calls = []
    record = run_extraction(GeminiProvider("m", urlopen=fake(reply("{}"), calls=calls)), request_)
    assert record.status == "error" and "credentials unavailable" in record.error
    assert calls == [] and record.response_text is None


def test_gemini_reads_the_key_from_the_environment(request_, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    calls = []
    run_extraction(GeminiProvider("m", urlopen=fake(reply("{}"), calls=calls)), request_)
    assert calls[0][0].get_header("X-goog-api-key") == "from-env"


def test_a_model_must_be_configured():
    with pytest.raises(ValueError):
        GeminiProvider("  ")


@pytest.mark.parametrize("error, expected", [
    (urllib.error.HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(b"")), "HTTP 429"),
    (urllib.error.URLError("no route"), "unreachable"),
    (socket.timeout("timed out"), "timed out"),
    (TimeoutError("timed out"), "timed out"),
])
def test_gemini_transport_failures_become_error_records(request_, error, expected):
    record = run_extraction(GeminiProvider("m", api_key="k", urlopen=fake(error=error)), request_)
    assert record.status == "error" and expected in record.error and record.response_text is None


def test_gemini_malformed_reply(request_):
    record = run_extraction(GeminiProvider("m", api_key="k", urlopen=fake(raw=b"<html>not json")), request_)
    assert record.status == "error" and "not valid JSON" in record.error


@pytest.mark.parametrize("payload", [{}, {"candidates": []}, {"candidates": [{"finishReason": "STOP"}]}])
def test_gemini_reply_without_candidate_text(request_, payload):
    record = run_extraction(GeminiProvider("m", api_key="k", urlopen=fake(payload)), request_)
    assert record.status == "error" and "no candidate text" in record.error


def test_gemini_truncated_reply_keeps_text_but_is_not_ok(request_):
    record = run_extraction(GeminiProvider("m", api_key="k", urlopen=fake(reply('{"items": [', "MAX_TOKENS"))), request_)
    assert record.status == "truncated" and record.stop_reason == "MAX_TOKENS"


@pytest.mark.parametrize("payload", [reply("", "SAFETY"), {"promptFeedback": {"blockReason": "SAFETY"}}])
def test_gemini_refusal(request_, payload):
    record = run_extraction(GeminiProvider("m", api_key="k", urlopen=fake(payload)), request_)
    assert record.status == "refused" and record.response_text is None


@pytest.mark.parametrize("payload", [reply("   "), reply("{}", "OTHER")])
def test_gemini_empty_text_or_unknown_finish_is_an_error(request_, payload):
    record = run_extraction(GeminiProvider("m", api_key="k", urlopen=fake(payload)), request_)
    assert record.status == "error"


# --- run_extraction ----------------------------------------------------------------------


class Exploding:
    name, model, execution_mode = "local-test", "tiny", "local"

    def extract(self, request):
        raise RuntimeError("model crashed")


class WrongType(Exploding):
    def extract(self, request):
        return {"text": "{}"}


@pytest.mark.parametrize("provider, expected", [(Exploding(), "model crashed"), (WrongType(), "AIProviderResult")])
def test_provider_exceptions_are_recorded_not_raised(request_, provider, expected):
    record = run_extraction(provider, request_)
    assert record.status == "error" and expected in record.error
    assert (record.provider, record.model, record.execution_mode) == ("local-test", "tiny", "local")


def test_result_type_is_the_provider_contract():
    assert AIProviderResult("x", "m", "live", "ok", "{}").status == "ok"
