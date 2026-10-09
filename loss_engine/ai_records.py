"""Provider-neutral AI contract and provenance records (Checkpoint 8.1).

The AI layer turns unstructured text into structured candidates. It never
calculates loss, hazard, damage, return periods or decisions: those belong to
the deterministic engine (Checkpoints 1 to 7). This module defines

- the provider contract: AIRequest -> AIProvider.extract -> AIProviderResult,
  the same for an online provider ("live"), a local model ("local") and a
  recorded response ("replay");
- the provenance records: SourceDocument (exactly what the AI was shown) and
  AIExtractionRecord (exactly what it returned, from which provider and model).

Every hash is re-checked when a record is built, so a record that does not
match its own content cannot exist. A provider's output is an untrusted
candidate until deterministic verification and human review have passed.

Serialisation reuses the run record's conventions (sorted-key JSON, UTF-8,
read-only nested data, UTC timestamps, code version).
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from .run_record import CodeVersion, _freeze, _thaw, _utc, code_version

RECORD_FORMAT = "ai-extraction-1"

DOCUMENT_KINDS = ("exposure_text", "hazard_source")
EXTRACTION_MODES = ("exposure", "hazard")       # Mode B and Mode A of Checkpoint 8
EXECUTION_MODES = ("live", "local", "replay")
RESULT_STATUSES = ("ok", "refused", "truncated", "error")

EXTRACTION_ID_PATTERN = re.compile(r"^ext-\d{8}T\d{6}Z-[0-9a-f]{8}$")
TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
PROVIDER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


# --- Helpers -------------------------------------------------------------------


def canonical_json(obj) -> str:
    """The fixed JSON form used for identities: sorted keys, no spaces (as canonical_json in config.py)."""
    return json.dumps(_thaw(obj), sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    """SHA-256 of the UTF-8 bytes of text, exactly as given."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_timestamp(now: datetime | None = None) -> str:
    """UTC time in the run record's form, for example 2026-10-08T12:15:30.123456Z."""
    return _utc(now).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_extraction_id(started: datetime) -> str:
    """ext-<UTC start, to the second>-<8 random hex characters>. Unique per extraction call."""
    return f"ext-{_utc(started):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"


def _check_timestamp(name: str, value) -> None:
    if not (isinstance(value, str) and TIMESTAMP_PATTERN.match(value)):
        raise ValueError(f"{name} must be a UTC timestamp like 2026-10-08T12:15:30.123456Z, got {value!r}")
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")  # rejects impossible dates


def _check_choice(name: str, value, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {list(allowed)}, got {value!r}")


def _check_text(name: str, value, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not (isinstance(value, str) and value.strip()):
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")


def _record_dict(record) -> dict:
    """A frozen record as plain JSON values. CodeVersion becomes an object; read-only data is thawed."""
    data = {}
    for f in fields(record):
        value = getattr(record, f.name)
        if isinstance(value, CodeVersion):
            value = {"package_version": value.package_version, "git_commit": value.git_commit,
                     "git_dirty": value.git_dirty}
        data[f.name] = _thaw(value)
    return data


def _record_json(record) -> str:
    """Sorted keys, two-space indent, UTF-8 text, no NaN: the run record's file form."""
    return json.dumps(_record_dict(record), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


# --- Source documents --------------------------------------------------------------


@dataclass(frozen=True)
class SourceDocument:
    """A piece of text shown to the AI, exactly as received.

    `text` is never normalised, so a quote can later be checked as an exact
    substring. `sha256` is the hash of its UTF-8 bytes and `document_id` is
    derived from it: the same text always has the same id.
    """

    document_id: str
    kind: str
    text: str
    sha256: str
    origin: str
    received_at: str
    metadata: Mapping

    def __post_init__(self):
        _check_choice("kind", self.kind, DOCUMENT_KINDS)
        if not isinstance(self.text, str) or self.text == "":
            raise ValueError("text must be a non-empty string")
        if self.sha256 != sha256_text(self.text):
            raise ValueError("sha256 does not match the document text")
        if self.document_id != "doc-" + self.sha256[:16]:
            raise ValueError("document_id must be 'doc-' + the first 16 characters of sha256")
        _check_text("origin", self.origin)
        _check_timestamp("received_at", self.received_at)
        object.__setattr__(self, "metadata", _freeze(dict(self.metadata)))

    @classmethod
    def from_text(cls, text: str, kind: str, origin: str, metadata: Mapping | None = None,
                  *, now: datetime | None = None) -> "SourceDocument":
        digest = sha256_text(text) if isinstance(text, str) else ""
        return cls(document_id="doc-" + digest[:16], kind=kind, text=text, sha256=digest, origin=origin,
                   received_at=utc_timestamp(now), metadata=metadata or {})

    def to_dict(self) -> dict:
        return _record_dict(self)

    def to_json(self) -> str:
        return _record_json(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SourceDocument":
        return cls(**data)


# --- The provider contract ---------------------------------------------------------


@dataclass(frozen=True)
class AIRequest:
    """What the AI is asked to do: one prompt and one output schema over one or more documents."""

    mode: str
    prompt_version: str
    prompt_text: str
    schema_version: str
    schema: Mapping
    documents: tuple[SourceDocument, ...]

    def __post_init__(self):
        _check_choice("mode", self.mode, EXTRACTION_MODES)
        _check_text("prompt_version", self.prompt_version)
        _check_text("prompt_text", self.prompt_text)
        _check_text("schema_version", self.schema_version)
        object.__setattr__(self, "schema", _freeze(dict(self.schema)))
        object.__setattr__(self, "documents", tuple(self.documents))
        if not self.documents or not all(isinstance(d, SourceDocument) for d in self.documents):
            raise ValueError("documents must be one or more SourceDocument")

    @property
    def prompt_sha256(self) -> str:
        return sha256_text(self.prompt_text)

    @property
    def schema_sha256(self) -> str:
        return sha256_text(canonical_json(self.schema))


@dataclass(frozen=True)
class AIProviderResult:
    """What a provider returned. Text only: no field can carry a loss, score or decision.

    `status` is provider-neutral: ok, refused, truncated or error. The
    provider's own stop reason and request id are kept as given, or None
    when the provider has none; they are never invented.
    """

    provider: str
    model: str
    execution_mode: str
    status: str
    response_text: str | None
    stop_reason: str | None = None
    request_id: str | None = None
    error: str | None = None
    replay_of: str | None = None

    def __post_init__(self):
        if not (isinstance(self.provider, str) and PROVIDER_PATTERN.match(self.provider)):
            raise ValueError(f"provider must be a lower-case identifier, got {self.provider!r}")
        _check_text("model", self.model)
        _check_choice("execution_mode", self.execution_mode, EXECUTION_MODES)
        _check_choice("status", self.status, RESULT_STATUSES)
        if self.status == "ok" and not isinstance(self.response_text, str):
            raise ValueError("a result with status 'ok' must have response_text")
        if self.response_text is not None and not isinstance(self.response_text, str):
            raise ValueError("response_text must be a string or None")
        if self.status == "error":
            _check_text("error", self.error)
        _check_text("stop_reason", self.stop_reason, optional=True)
        _check_text("request_id", self.request_id, optional=True)
        if self.execution_mode == "replay":
            if not (isinstance(self.replay_of, str) and EXTRACTION_ID_PATTERN.match(self.replay_of)):
                raise ValueError("a replay must name the extraction_id it replays")
        elif self.replay_of is not None:
            raise ValueError("replay_of is only allowed when execution_mode is 'replay'")


@runtime_checkable
class AIProvider(Protocol):
    """Anything that can answer an AIRequest: an online API, a local model or a recorded replay."""

    name: str
    model: str
    execution_mode: str

    def extract(self, request: AIRequest) -> AIProviderResult:
        ...


# --- The extraction record ----------------------------------------------------------


@dataclass(frozen=True)
class AIExtractionRecord:
    """The provenance of one AI call: inputs by hash, provider and model, and the raw response.

    `response_sha256` is the hash of `response_text`, and each document hash
    must match its content-derived document id; both are re-checked here.
    A failed call is recorded with its status and error and no response.
    """

    record_format: str
    extraction_id: str
    mode: str
    provider: str
    model: str
    execution_mode: str
    prompt_version: str
    prompt_sha256: str
    schema_version: str
    schema_sha256: str
    document_ids: tuple[str, ...]
    document_hashes: tuple[str, ...]
    request_id: str | None
    stop_reason: str | None
    status: str
    error: str | None
    response_text: str | None
    response_sha256: str | None
    created_at: str
    code_version: CodeVersion
    replay_of: str | None

    def __post_init__(self):
        if self.record_format != RECORD_FORMAT:
            raise ValueError(f"record_format must be {RECORD_FORMAT!r}")
        if not (isinstance(self.extraction_id, str) and EXTRACTION_ID_PATTERN.match(self.extraction_id)):
            raise ValueError(f"extraction_id {self.extraction_id!r} does not match ext-<YYYYMMDDTHHMMSSZ>-<8 hex>")
        _check_choice("mode", self.mode, EXTRACTION_MODES)
        # Provider-side fields are checked by the same rules as a provider result.
        AIProviderResult(self.provider, self.model, self.execution_mode, self.status, self.response_text,
                         self.stop_reason, self.request_id, self.error, self.replay_of)
        object.__setattr__(self, "document_ids", tuple(self.document_ids))
        object.__setattr__(self, "document_hashes", tuple(self.document_hashes))
        if not self.document_ids or len(self.document_ids) != len(self.document_hashes):
            raise ValueError("document_ids and document_hashes must be non-empty and the same length")
        for doc_id, digest in zip(self.document_ids, self.document_hashes):
            if doc_id != "doc-" + str(digest)[:16]:
                raise ValueError(f"document {doc_id!r} does not match its hash")
        if self.response_text is None:
            if self.response_sha256 is not None:
                raise ValueError("response_sha256 must be None when there is no response")
        elif self.response_sha256 != sha256_text(self.response_text):
            raise ValueError("response_sha256 does not match response_text")
        if self.replay_of == self.extraction_id:
            raise ValueError("a replay must have its own extraction_id")
        _check_timestamp("created_at", self.created_at)
        if not isinstance(self.code_version, CodeVersion):
            raise ValueError("code_version must be a CodeVersion")

    def to_dict(self) -> dict:
        return _record_dict(self)

    def to_json(self) -> str:
        return _record_json(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AIExtractionRecord":
        values = dict(data)
        values["code_version"] = CodeVersion(**data["code_version"])
        return cls(**values)


def build_extraction_record(
    request: AIRequest,
    result: AIProviderResult,
    *,
    extraction_id: str | None = None,
    now: datetime | None = None,
) -> AIExtractionRecord:
    """The record of one provider call, with hashes taken from the request and the result."""
    started = _utc(now)
    return AIExtractionRecord(
        record_format=RECORD_FORMAT,
        extraction_id=extraction_id if extraction_id is not None else new_extraction_id(started),
        mode=request.mode,
        provider=result.provider,
        model=result.model,
        execution_mode=result.execution_mode,
        prompt_version=request.prompt_version,
        prompt_sha256=request.prompt_sha256,
        schema_version=request.schema_version,
        schema_sha256=request.schema_sha256,
        document_ids=tuple(d.document_id for d in request.documents),
        document_hashes=tuple(d.sha256 for d in request.documents),
        request_id=result.request_id,
        stop_reason=result.stop_reason,
        status=result.status,
        error=result.error,
        response_text=result.response_text,
        response_sha256=None if result.response_text is None else sha256_text(result.response_text),
        created_at=utc_timestamp(started),
        code_version=code_version(),
        replay_of=result.replay_of,
    )


# --- Persistence ---------------------------------------------------------------------


def write_record(record, directory: str | Path, filename: str) -> str:
    """Write record.to_json() to directory/filename and return the SHA-256 of the written file.

    The hash is computed after writing and is not stored in the file. An
    existing file is never overwritten.
    """
    path = Path(directory) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as handle:
        handle.write(record.to_json().encode("utf-8"))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_record(path: str | Path, record_class):
    """Read a record written by write_record back into record_class, re-checking every hash."""
    return record_class.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
