"""Tests for the provider-neutral AI contract and provenance records (Checkpoint 8.1).

No test calls a real AI provider, needs an AI SDK, or uses the network. The
providers below are fakes that satisfy the AIProvider contract.
"""

import dataclasses
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loss_engine.ai_records import (  # noqa: E402
    EXECUTION_MODES,
    RECORD_FORMAT,
    AIExtractionRecord,
    AIProvider,
    AIProviderResult,
    AIRequest,
    SourceDocument,
    build_extraction_record,
    canonical_json,
    read_record,
    sha256_text,
    write_record,
)
from loss_engine.run_record import CodeVersion  # noqa: E402

T1 = datetime(2026, 10, 8, 12, 15, 30, 123456, tzinfo=timezone.utc)
SUBMISSION = "Account X: 40 masonry houses in Kayole, KSh 3.2M each;\r\n one concrete block in Westlands × KES 450m.  "
EXT_1 = "ext-20261008T121530Z-3f9a1c2e"
SCHEMA = {"type": "object", "properties": {"items": {"type": "array"}}, "required": ["items"]}


def document(text=SUBMISSION, kind="exposure_text", **kwargs):
    return SourceDocument.from_text(text, kind, origin="test submission", now=T1, **kwargs)


def request(documents=None, **changes):
    values = dict(mode="exposure", prompt_version="exposure-prompt-1", prompt_text="Extract stated facts only.",
                  schema_version="exposure-schema-1", schema=SCHEMA,
                  documents=(document(),) if documents is None else documents)
    values.update(changes)
    return AIRequest(**values)


class FakeOnlineProvider:
    """Shaped like an online API provider (for example Gemini), without calling anything."""
    name, model, execution_mode = "gemini", "a-configured-online-model", "live"

    def extract(self, req):
        return AIProviderResult(self.name, self.model, self.execution_mode, "ok", '{"items": []}',
                                stop_reason="STOP", request_id="req-abc123")


class FakeLocalProvider:
    """Shaped like a local open-weight model: no request id from the runtime."""
    name, model, execution_mode = "local", "a-configured-local-model", "local"

    def extract(self, req):
        return AIProviderResult(self.name, self.model, self.execution_mode, "ok", '{"items": []}', stop_reason="stop")


class FakeReplayProvider:
    """Returns a stored response from an earlier extraction."""
    name, model, execution_mode = "replay", "a-configured-online-model", "replay"

    def __init__(self, stored: AIExtractionRecord):
        self.stored = stored

    def extract(self, req):
        return AIProviderResult(self.name, self.stored.model, self.execution_mode, "ok", self.stored.response_text,
                                stop_reason=self.stored.stop_reason, replay_of=self.stored.extraction_id)


def live_record(**kwargs):
    return build_extraction_record(request(), FakeOnlineProvider().extract(request()),
                                   extraction_id=EXT_1, now=T1, **kwargs)


# --- SourceDocument ----------------------------------------------------------------


def test_text_is_preserved_exactly():
    doc = document()
    assert doc.text == SUBMISSION  # CRLF, trailing spaces and non-ASCII kept
    assert doc.text.encode("utf-8") == SUBMISSION.encode("utf-8")


def test_hash_is_the_sha256_of_the_utf8_bytes():
    assert document().sha256 == hashlib.sha256(SUBMISSION.encode("utf-8")).hexdigest()


def test_document_id_is_derived_from_content():
    first, second = document(), SourceDocument.from_text(SUBMISSION, "exposure_text", "elsewhere", now=T1 + timedelta(days=1))
    assert first.document_id == second.document_id == "doc-" + first.sha256[:16]
    assert document(text=SUBMISSION + ".").document_id != first.document_id


def test_whitespace_differences_are_different_documents():
    assert document(text=SUBMISSION.strip()).sha256 != document().sha256


@pytest.mark.parametrize("kind", ["exposure_text", "hazard_source"])
def test_supported_kinds(kind):
    assert document(kind=kind).kind == kind


@pytest.mark.parametrize("kind", ["email", "", None, "EXPOSURE_TEXT"])
def test_unsupported_kinds_are_refused(kind):
    with pytest.raises(ValueError, match="kind"):
        document(kind=kind)


def test_empty_or_non_text_is_refused():
    for bad in ["", None, b"bytes"]:
        with pytest.raises(ValueError):
            document(text=bad)


def test_received_at_is_utc():
    assert document().received_at == "2026-10-08T12:15:30.123456Z"
    nairobi = timezone(timedelta(hours=3))
    doc = SourceDocument.from_text(SUBMISSION, "exposure_text", "test", now=datetime(2026, 10, 8, 15, 15, 30, tzinfo=nairobi))
    assert doc.received_at == "2026-10-08T12:15:30.000000Z"
    with pytest.raises(ValueError, match="timezone-aware"):
        SourceDocument.from_text(SUBMISSION, "exposure_text", "test", now=datetime(2026, 10, 8))


def test_tampered_text_or_id_is_refused():
    doc = document()
    with pytest.raises(ValueError, match="sha256"):
        dataclasses.replace(doc, text=SUBMISSION + " ")
    with pytest.raises(ValueError, match="document_id"):
        dataclasses.replace(doc, document_id="doc-0000000000000000")


@pytest.mark.parametrize("stamp", ["2026-10-08T12:15:30Z", "2026-10-08 12:15:30.000000Z",
                                   "2026-10-08T12:15:30.000000+03:00", "2026-13-08T12:15:30.000000Z",
                                   "2026-10-08T12:15:30.1Z"])  # parseable, but not the canonical six digits
def test_invalid_timestamps_are_refused(stamp):
    with pytest.raises(ValueError):
        dataclasses.replace(document(), received_at=stamp)


def test_document_and_metadata_cannot_be_changed():
    doc = document(metadata={"broker": "example", "pages": [1, 2]})
    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.text = "changed"
    with pytest.raises(TypeError):
        doc.metadata["broker"] = "changed"
    assert doc.metadata["pages"] == (1, 2)


def test_metadata_is_copied_from_the_caller():
    metadata = {"broker": "example"}
    doc = document(metadata=metadata)
    metadata["broker"] = "changed"
    assert doc.metadata["broker"] == "example"


def test_document_round_trip():
    doc = document(metadata={"broker": "example"})
    assert SourceDocument.from_dict(json.loads(doc.to_json())) == doc


# --- AIRequest and the provider contract ----------------------------------------------


def test_prompt_and_schema_hashes():
    req = request()
    assert req.prompt_sha256 == hashlib.sha256(b"Extract stated facts only.").hexdigest()
    assert req.schema_sha256 == hashlib.sha256(json.dumps(SCHEMA, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_schema_hash_ignores_key_order_but_not_content():
    reordered = {"required": ["items"], "properties": {"items": {"type": "array"}}, "type": "object"}
    assert request(schema=reordered).schema_sha256 == request().schema_sha256
    assert request(schema={**SCHEMA, "required": []}).schema_sha256 != request().schema_sha256


@pytest.mark.parametrize("changes", [{"mode": "loss"}, {"prompt_text": ""}, {"schema_version": " "}, {"documents": ()}])
def test_invalid_requests_are_refused(changes):
    with pytest.raises(ValueError):
        request(**changes)


@pytest.mark.parametrize("provider_class", [FakeOnlineProvider, FakeLocalProvider])
def test_fakes_satisfy_the_provider_contract(provider_class):
    provider = provider_class()
    assert isinstance(provider, AIProvider)
    assert isinstance(provider.extract(request()), AIProviderResult)


def test_replay_provider_satisfies_the_contract():
    assert isinstance(FakeReplayProvider(live_record()), AIProvider)


def test_execution_modes():
    assert EXECUTION_MODES == ("live", "local", "replay")


def test_provider_result_carries_text_only():
    """The financial boundary: nothing a provider returns can be a loss, score, TIV or decision."""
    assert [f.name for f in dataclasses.fields(AIProviderResult)] == [
        "provider", "model", "execution_mode", "status", "response_text", "stop_reason", "request_id", "error",
        "replay_of",
    ]


@pytest.mark.parametrize("changes", [
    {"provider": "Gemini"}, {"provider": ""}, {"model": ""}, {"execution_mode": "cloud"}, {"status": "success"},
    {"status": "ok", "response_text": None}, {"status": "error", "error": None},
    {"replay_of": EXT_1},                                           # not a replay
    {"execution_mode": "replay"},                                   # replay without replay_of
    {"execution_mode": "replay", "replay_of": "ext-1"},             # malformed replay_of
    {"request_id": ""},
])
def test_invalid_provider_results_are_refused(changes):
    values = dict(provider="gemini", model="m", execution_mode="live", status="ok", response_text="{}")
    values.update(changes)
    with pytest.raises(ValueError):
        AIProviderResult(**values)


def test_a_failed_call_is_represented_without_a_response():
    result = AIProviderResult("local", "m", "local", "error", None, error="model did not load")
    assert result.response_text is None and result.error == "model did not load"


# --- AIExtractionRecord ----------------------------------------------------------------


def test_online_record():
    record = live_record()
    assert record.record_format == RECORD_FORMAT == "ai-extraction-1"
    assert (record.provider, record.model, record.execution_mode) == ("gemini", "a-configured-online-model", "live")
    assert record.request_id == "req-abc123" and record.stop_reason == "STOP"
    assert record.replay_of is None and record.status == "ok"


def test_local_record_has_no_invented_request_id():
    record = build_extraction_record(request(), FakeLocalProvider().extract(request()), now=T1)
    assert record.execution_mode == "local" and record.request_id is None


def test_replay_record_names_its_original():
    original = live_record()
    replay = build_extraction_record(request(), FakeReplayProvider(original).extract(request()), now=T1)
    assert replay.execution_mode == "replay" and replay.replay_of == EXT_1
    assert replay.extraction_id != original.extraction_id
    assert replay.response_sha256 == original.response_sha256


def test_record_hashes_link_to_request_and_documents():
    req, record = request(), live_record()
    assert record.prompt_sha256 == req.prompt_sha256 and record.schema_sha256 == req.schema_sha256
    assert record.document_ids == (document().document_id,)
    assert record.document_hashes == (document().sha256,)
    assert record.response_sha256 == hashlib.sha256(b'{"items": []}').hexdigest()


def test_several_documents_keep_their_order():
    docs = (document(), document(text="Second schedule."))
    record = build_extraction_record(request(documents=docs), FakeOnlineProvider().extract(request()), now=T1)
    assert record.document_ids == tuple(d.document_id for d in docs)


def test_failed_call_is_recorded_not_filled_in():
    result = AIProviderResult("gemini", "m", "live", "error", None, error="HTTP 503")
    record = build_extraction_record(request(), result, now=T1)
    assert record.status == "error" and record.response_text is None and record.response_sha256 is None


def test_extraction_id_format_uniqueness_and_injection():
    ids = {build_extraction_record(request(), FakeLocalProvider().extract(request()), now=T1).extraction_id
           for _ in range(3)}
    assert len(ids) == 3 and all(re.fullmatch(r"ext-20261008T121530Z-[0-9a-f]{8}", i) for i in ids)
    assert live_record().extraction_id == EXT_1


def test_created_at_is_utc():
    assert live_record().created_at == "2026-10-08T12:15:30.123456Z"


@pytest.mark.parametrize("changes", [
    {"response_text": '{"items": [1]}'},                     # response no longer matches its hash
    {"response_sha256": "0" * 64},
    {"document_hashes": ("0" * 64,)},                        # hash does not match the document id
    {"document_ids": ()},
    {"mode": "loss"},
    {"execution_mode": "cloud"},
    {"extraction_id": "ext-1"},
    {"record_format": "ai-extraction-0"},
    {"created_at": "2026-10-08T12:15:30Z"},
    {"code_version": {"git_commit": None}},
    {"replay_of": EXT_1, "execution_mode": "replay"},        # a replay cannot replay itself
])
def test_inconsistent_records_are_refused(changes):
    with pytest.raises(ValueError):
        dataclasses.replace(live_record(), **changes)


def test_record_round_trip_and_stable_json():
    record = live_record()
    text = record.to_json()
    assert text == live_record().to_json()
    assert AIExtractionRecord.from_dict(json.loads(text)) == record
    keys = list(json.loads(text))
    assert keys == sorted(keys)


def test_record_cannot_be_changed():
    record = live_record()
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.response_text = "{}"
    with pytest.raises(AttributeError):
        record.document_ids.append("doc-x")


def test_code_version_is_recorded():
    assert isinstance(live_record().code_version, CodeVersion)


def test_canonical_json_matches_the_existing_convention():
    assert canonical_json({"b": 1, "a": [1.0, "×"]}) == '{"a":[1.0,"\\u00d7"],"b":1}'
    assert sha256_text("×") == hashlib.sha256("×".encode("utf-8")).hexdigest()


# --- Persistence -----------------------------------------------------------------------


def test_write_and_read_back(tmp_path):
    record = live_record()
    digest = write_record(record, tmp_path / "ai", "extraction.json")
    data = (tmp_path / "ai" / "extraction.json").read_bytes()
    assert digest == hashlib.sha256(data).hexdigest() and digest.encode() not in data
    assert read_record(tmp_path / "ai" / "extraction.json", AIExtractionRecord) == record


def test_source_document_persists_exactly(tmp_path):
    doc = document()
    write_record(doc, tmp_path, "doc.json")
    back = read_record(tmp_path / "doc.json", SourceDocument)
    assert back == doc and back.text == SUBMISSION


def test_existing_file_is_never_overwritten(tmp_path):
    write_record(live_record(), tmp_path, "extraction.json")
    with pytest.raises(FileExistsError):
        write_record(live_record(), tmp_path, "extraction.json")


def test_a_tampered_file_is_refused_on_reading(tmp_path):
    write_record(live_record(), tmp_path, "extraction.json")
    path = tmp_path / "extraction.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["response_text"] = '{"items": ["invented"]}'
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="response_sha256"):
        read_record(path, AIExtractionRecord)


# --- Provider neutrality and dependencies -------------------------------------------------


def test_module_names_no_vendor_and_imports_no_ai_sdk():
    source = (ROOT / "loss_engine" / "ai_records.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+(anthropic|google|openai|ollama|transformers|torch|llama_cpp)",
                         source, re.MULTILINE)
    assert not re.search(r"claude|gemini|openai|anthropic", source, re.IGNORECASE)
