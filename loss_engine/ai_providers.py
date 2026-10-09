"""AI providers for exposure extraction (Checkpoint 8, Mode B).

Turns a SourceDocument into an AIRequest, runs it through any AIProvider and
records the result as an AIExtractionRecord. The provider's answer is an
untrusted candidate: it goes to the deterministic verifier and a human next.

- ReplayProvider returns a previously recorded response. It is the default
  for tests, CI and the demonstration, and never claims to be a live call.
- GeminiProvider calls the Gemini REST API with the standard library (no SDK).
  It needs GEMINI_API_KEY and an explicitly chosen model; it is never called
  by the tests.

Every failure (no credentials, HTTP error, timeout, malformed reply, refusal,
truncation) becomes a recorded result with status error, refused or truncated.
Nothing is guessed or filled in.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .ai_records import (
    AIExtractionRecord,
    AIProvider,
    AIProviderResult,
    AIRequest,
    SourceDocument,
    build_extraction_record,
    read_record,
)
from .exposure_extraction import EXPOSURE_SCHEMA, SCHEMA_VERSION

PROMPT_VERSION = "exposure-prompt-1"
PROMPT_TEXT = """You extract facts from an insurance exposure submission. Return JSON only, matching the schema.

Rules:
1. Extract only what the submission states. Never guess, infer, geocode, calculate or fill a gap.
2. Every value you give must have a "quote": an exact, unaltered substring of the submission that states it.
3. If a fact is not stated, use status "missing" (basis "missing" for insured_value) with null value and null quote.
4. If a fact is stated unclearly, use status "ambiguous" with null value and the quote that shows the ambiguity.
5. housing_class: use status "stated" only if the text names the class itself (for example "reinforced concrete");
   use "mapped" with a mapping_rule when you read the class from a description; otherwise "missing" or "ambiguous".
6. insured_value: give the amount exactly as written (apply only the stated scale: k, m, million, bn), the currency
   only if a currency marker is written next to it (KES for KES, KSh, Sh, shillings or /=), and the basis:
   "per_building" only if the text says each/per building, "total" if it is a total. Never divide a total.
7. building_count: the number of buildings stated for the item, as a whole number.
8. Coordinates: only if decimal latitude and longitude are written in the text.
9. Policy terms (deductible, limit, business interruption, contents) go to unmodelled_terms with their quote.
10. One item per building or group of buildings described together."""

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def build_exposure_request(document: SourceDocument) -> AIRequest:
    """The extraction request for one exposure submission: fixed prompt and schema, plus the document."""
    return AIRequest("exposure", PROMPT_VERSION, PROMPT_TEXT, SCHEMA_VERSION, EXPOSURE_SCHEMA, (document,))


def render_prompt(request: AIRequest) -> str:
    """The full text sent to a live model: instructions, schema, then the submission between markers."""
    schema = json.dumps(json.loads(json.dumps(dict(request.schema), default=_thawed)), indent=1)
    documents = "\n\n".join(f"SUBMISSION {d.document_id}:\n<<<\n{d.text}\n>>>" for d in request.documents)
    return f"{request.prompt_text}\n\nJSON schema:\n{schema}\n\n{documents}"


def _thawed(value):
    """JSON fallback for the read-only mappings and tuples inside a frozen schema."""
    if hasattr(value, "items"):
        return dict(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(type(value).__name__)


class ReplayProvider:
    """Returns the response of an earlier, recorded extraction of the same request.

    It refuses (status error) if the request's documents, prompt or schema do
    not match the recording, so a replay can never be attached to other inputs.
    """

    name = "replay"
    execution_mode = "replay"

    def __init__(self, recorded: AIExtractionRecord):
        if recorded.status != "ok" or recorded.response_text is None:
            raise ValueError("only a recorded extraction with status 'ok' can be replayed")
        self.recorded = recorded
        self.model = recorded.model

    @classmethod
    def from_file(cls, path: str | Path) -> "ReplayProvider":
        return cls(read_record(path, AIExtractionRecord))

    def extract(self, request: AIRequest) -> AIProviderResult:
        r = self.recorded
        mismatches = [label for label, ok in (
            ("documents", r.document_hashes == tuple(d.sha256 for d in request.documents)),
            ("prompt", r.prompt_sha256 == request.prompt_sha256),
            ("schema", r.schema_sha256 == request.schema_sha256),
        ) if not ok]
        if mismatches:
            return AIProviderResult(self.name, self.model, self.execution_mode, "error", None,
                                    error=f"the recording does not match this request ({', '.join(mismatches)})",
                                    replay_of=r.extraction_id)
        return AIProviderResult(self.name, self.model, self.execution_mode, "ok", r.response_text,
                                stop_reason=r.stop_reason, replay_of=r.extraction_id)


class GeminiProvider:
    """Gemini over its REST API, with JSON output requested. Needs GEMINI_API_KEY; the model is configured."""

    name = "gemini"
    execution_mode = "live"

    def __init__(self, model: str, *, api_key: str | None = None, timeout: float = 120.0, urlopen=None):
        if not (isinstance(model, str) and model.strip()):
            raise ValueError("a Gemini model name must be configured explicitly")
        self.model = model
        self._api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY")
        self._timeout = timeout
        self._urlopen = urlopen or urllib.request.urlopen

    def _error(self, message: str) -> AIProviderResult:
        return AIProviderResult(self.name, self.model, self.execution_mode, "error", None, error=message)

    def extract(self, request: AIRequest) -> AIProviderResult:
        if not self._api_key:
            return self._error("credentials unavailable: GEMINI_API_KEY is not set")
        body = json.dumps({
            "contents": [{"role": "user", "parts": [{"text": render_prompt(request)}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        }).encode("utf-8")
        http = urllib.request.Request(GEMINI_ENDPOINT.format(model=self.model), data=body, method="POST",
                                      headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key})
        try:
            with self._urlopen(http, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                request_id = response.headers.get("x-request-id") if getattr(response, "headers", None) else None
        except urllib.error.HTTPError as error:
            return self._error(f"HTTP {error.code} from the provider")
        except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
            return self._error(f"provider unreachable or timed out: {error}")
        except (ValueError, UnicodeDecodeError) as error:
            return self._error(f"the provider's reply is not valid JSON: {error}")

        if (payload.get("promptFeedback") or {}).get("blockReason"):
            return AIProviderResult(self.name, self.model, self.execution_mode, "refused", None,
                                    stop_reason=str(payload["promptFeedback"]["blockReason"]), request_id=request_id)
        try:
            candidate = payload["candidates"][0]
            text = "".join(part.get("text", "") for part in candidate["content"]["parts"])
        except (KeyError, IndexError, TypeError):
            return self._error("the provider's reply has no candidate text")
        finish = candidate.get("finishReason")
        status = {"STOP": "ok", "MAX_TOKENS": "truncated", "SAFETY": "refused", "RECITATION": "refused"}.get(finish, "error")
        if status == "ok" and not text.strip():
            status = "error"
        return AIProviderResult(self.name, self.model, self.execution_mode, status,
                                text if status in ("ok", "truncated") else None,
                                stop_reason=finish, request_id=request_id,
                                error=None if status != "error" else f"unexpected finish reason {finish!r} or empty text")


def run_extraction(provider: AIProvider, request: AIRequest, *, extraction_id: str | None = None,
                   now: datetime | None = None) -> AIExtractionRecord:
    """Run one provider call and record it. An exception inside the provider is recorded as an error."""
    try:
        result = provider.extract(request)
        if not isinstance(result, AIProviderResult):
            raise TypeError("the provider did not return an AIProviderResult")
    except Exception as error:  # a provider must never crash the workflow or leave no record
        result = AIProviderResult(getattr(provider, "name", "unknown") or "unknown", getattr(provider, "model", "unknown") or "unknown",
                                  getattr(provider, "execution_mode", "live"), "error", None,
                                  error=f"{type(error).__name__}: {error}",
                                  replay_of=getattr(getattr(provider, "recorded", None), "extraction_id", None)
                                  if getattr(provider, "execution_mode", None) == "replay" else None)
    return build_extraction_record(request, result, extraction_id=extraction_id, now=now)
