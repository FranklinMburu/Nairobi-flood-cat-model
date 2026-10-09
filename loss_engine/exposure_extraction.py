"""Exposure extraction schema and deterministic verifier (Checkpoint 8.2, Mode B).

An AI provider reads an unstructured exposure submission and returns a JSON
candidate in the schema below: facts stated in the text, each with a verbatim
quote. This module checks that candidate against the source text without
trusting the AI's interpretation, and reports, field by field, whether it is

- valid_candidate: every check passes; it still needs human approval;
- needs_review:    something is missing, ambiguous or a judgement a human must confirm;
- rejected:        malformed, unsupported by its quotes, inconsistent or tampered.

A value is checked against what the number in the source means, not only
whether the same digits occur in the quote. Every number in the source is
classified by its local context (money with an attached currency, money with
no currency, floor area, building count, date, percentage, plain number), and
a value must match a number of the right kind. A currency counts only if its
marker is inside the quote. Context outside the quote is used only to find a
contradiction or a reason for review, never to support a claim.

Nothing here approves a candidate, corrects it, fills a gap, geocodes, builds
an exposure file or calculates anything financial. Where meaning cannot be
established deterministically, the field goes to human review.
"""

from __future__ import annotations

import bisect
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal

from .ai_records import AIExtractionRecord, SourceDocument, sha256_text
from .validation import HOUSING_CLASSES

SCHEMA_VERSION = "exposure-extraction-1"
REPORT_FORMAT = "exposure-verification-1"

LOCATION_STATUSES = ("stated", "missing", "ambiguous")
CLASS_STATUSES = ("stated", "mapped", "missing", "ambiguous")
VALUE_STATUSES = ("stated", "missing", "ambiguous")
VALUE_BASES = ("per_building", "total", "missing", "ambiguous")
UNMODELLED_KINDS = ("deductible", "limit", "business_interruption", "contents", "other")

OUTCOMES = ("valid_candidate", "needs_review", "rejected")
CHECK_RESULTS = ("pass", "review", "reject")

# A class is "stated" only when the quote uses the class's own name.
STATED_CLASS_PHRASES = {
    "informal_iron_sheet": ("informal iron sheet", "informal iron-sheet", "informal_iron_sheet"),
    "semi_permanent": ("semi-permanent", "semi permanent", "semi_permanent"),
    "permanent_masonry": ("permanent masonry", "permanent_masonry"),
    "concrete_rcc": ("concrete_rcc", "reinforced concrete", "rcc"),
}

# Documented mapping rules [A]: provisional team assumptions, not source facts.
# A "mapped" class must cite one rule whose phrase appears in its quote, and it
# always goes to human review. A material alone ("timber", "iron sheet", "mud")
# is not enough: the phrase must describe the building, as below.
CLASS_MAPPING_RULES = {
    "map-masonry": ("permanent_masonry", ("masonry", "stone house", "stone-built", "brick house", "brick-built")),
    "map-concrete": ("concrete_rcc", ("concrete building", "concrete frame", "concrete-framed")),
    "map-iron-sheet": ("informal_iron_sheet", ("mabati house", "mabati structure", "iron-sheet house",
                                               "iron sheet house", "iron-sheet structure", "corrugated iron house")),
    "map-semi-permanent": ("semi_permanent", ("mud-walled", "mud walled", "wattle and daub", "timber-walled")),
}
# Material words, and the class each points towards. Without one of the phrases
# above a material leaves the class ambiguous; a material of another class next
# to a mapped class calls for review.
MATERIAL_CLASSES = {
    "timber": "semi_permanent", "wood": "semi_permanent", "mud": "semi_permanent", "wattle": "semi_permanent",
    "iron sheet": "informal_iron_sheet", "iron-sheet": "informal_iron_sheet", "mabati": "informal_iron_sheet",
    "corrugated iron": "informal_iron_sheet", "concrete": "concrete_rcc", "stone": "permanent_masonry",
    "brick": "permanent_masonry", "masonry": "permanent_masonry",
}
MATERIAL_WORDS = tuple(MATERIAL_CLASSES)

PER_BUILDING_CUES = ("each", "per building", "per house", "per unit", "per property", "per home", "apiece",
                     "a piece", "every")
TOTAL_CUES = ("total", "in all", "combined", "aggregate", "altogether", "for all", "overall", "together")

# Currency markers. Only a KES marker establishes a KES amount. "/=" and "/-"
# after an amount are the Kenyan notation for shillings.
CURRENCY_TOKENS = {
    "KES": ("kes", "kshs", "ksh", "sh", "shs", "shillings", "kenya shillings", "/=", "/-"),
    "USD": ("usd", "us$", "$", "dollars"),
    "EUR": ("eur", "€", "euros"),
    "GBP": ("gbp", "£", "pounds"),
}
_CURRENCY_RE = re.compile(
    r"(?i)(?<![a-z])(kenya shillings|shillings|kshs|ksh|kes|shs|sh|us\$|usd|dollars|eur|euros|gbp|pounds)(?![a-z])"
    r"|(\$|€|£)|(?<=\d)(/=|/-)"
)
_SCALES = {"billion": 9, "bn": 9, "million": 6, "mn": 6, "m": 6, "thousand": 3, "k": 3}
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_AMOUNT_RE = re.compile(
    rf"(?i)(?<![\w.,])(?P<num>{_NUM})(?:\s*(?P<scale>billion|bn|million|mn|m|thousand|k)(?![\w²]))?"
    r"(?:(?![\w²])|(?=m2|m²|sqm))"
)
_NUMBER_RE = re.compile(rf"(?<![\w.])(?P<num>{_NUM})")
_COORD_RE = re.compile(r"(?P<sign>[-−])?\s*(?P<num>\d{1,3}\.\d+)\s*°?\s*(?P<hem>[NSEWnsew])?(?![A-Za-z])")
_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}

# Context patterns for classifying a number by what follows or precedes it.
_CUR_BEFORE = re.compile(r"(?:(?<![a-z])(kenya shillings|kshs|ksh|kes|shs|sh|usd|eur|gbp)\.?|(us\$|\$|€|£))\s*$")
_CUR_AFTER = re.compile(r"^\s*(/=|/-|(?:kenya\s+)?shillings|kshs|ksh|kes|usd|dollars|euros|eur|gbp|pounds)(?![a-z])")
_AREA_AFTER = re.compile(r"^\s*(m2|m²|sqm|sq\.?\s?m(?![a-z])|square\s+met)")
_BUILDING_NOUN = (r"(houses?|homes?|buildings?|units?|structures?|blocks?|propert(?:y|ies)|flats?|apartments?|"
                  r"warehouses?|shops?|offices?|godowns?|bungalows?|maisonettes?|villas?|dwellings?|hostels?|"
                  r"schools?|hotels?|factor(?:y|ies)|stores?)(?![a-z])")
_COUNT_AFTER = re.compile(r"^\s*(?:[a-z][a-z-]*\s+){0,3}" + _BUILDING_NOUN)
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_POLICY_WORDS = ("deductible", "excess", "limit", "limited", "sublimit", "sub-limit", "premium", "retention",
                 "liability", "franchise", "indemnity")
_QUALIFIER_WORDS = ("about", "approx", "approximately", "around", "circa", "roughly", "estimated", "est",
                    "over", "under", "nearly", "almost", "maximum", "max", "minimum", "min", "least", "~")
_VALUE_WORDS = ("insured", "sum", "value", "valued", "tiv", "worth", "cover", "covered", "rebuild",
                "replacement", "cost")

ITEM_KEYS = ("item_id", "location", "housing_class", "building_count", "insured_value", "floor_area_m2",
             "cost_per_m2", "unmodelled_terms")
FIELD_KEYS = {
    "location": ("text", "lat", "lon", "status", "quote"),
    "housing_class": ("value", "status", "quote", "mapping_rule"),
    "building_count": ("value", "status", "quote"),
    "insured_value": ("amount", "currency", "basis", "quote"),
    "floor_area_m2": ("value", "status", "quote"),
    "cost_per_m2": ("amount", "currency", "status", "quote"),
}
TERM_KEYS = ("kind", "text", "quote")


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


# The output schema an AI provider is asked to follow. The verifier below does
# not rely on the provider having obeyed it.
EXPOSURE_SCHEMA = _object({
    "items": {"type": "array", "items": _object({
        "item_id": {"type": "string"},
        "location": _object({
            "text": _nullable({"type": "string"}), "lat": _nullable({"type": "number"}),
            "lon": _nullable({"type": "number"}), "status": {"enum": list(LOCATION_STATUSES)},
            "quote": _nullable({"type": "string"})}),
        "housing_class": _object({
            "value": _nullable({"enum": list(HOUSING_CLASSES)}), "status": {"enum": list(CLASS_STATUSES)},
            "quote": _nullable({"type": "string"}), "mapping_rule": _nullable({"enum": list(CLASS_MAPPING_RULES)})}),
        "building_count": _object({
            "value": _nullable({"type": "integer"}), "status": {"enum": list(VALUE_STATUSES)},
            "quote": _nullable({"type": "string"})}),
        "insured_value": _object({
            "amount": _nullable({"type": "number"}), "currency": _nullable({"type": "string"}),
            "basis": {"enum": list(VALUE_BASES)}, "quote": _nullable({"type": "string"})}),
        "floor_area_m2": _object({
            "value": _nullable({"type": "number"}), "status": {"enum": list(VALUE_STATUSES)},
            "quote": _nullable({"type": "string"})}),
        "cost_per_m2": _object({
            "amount": _nullable({"type": "number"}), "currency": _nullable({"type": "string"}),
            "status": {"enum": list(VALUE_STATUSES)}, "quote": _nullable({"type": "string"})}),
        "unmodelled_terms": {"type": "array", "items": _object({
            "kind": {"enum": list(UNMODELLED_KINDS)}, "text": {"type": "string"}, "quote": {"type": "string"}})},
    })},
})


# --- Deterministic parsing ---------------------------------------------------------------


def parse_amounts(quote: str) -> tuple[Decimal, ...]:
    """Every amount written in the quote, with its scale applied: "3.2M" -> 3200000, "500k" -> 500000."""
    return tuple(t.value for t in classify_numbers(quote))


def parse_numbers(quote: str) -> tuple[Decimal, ...]:
    """Every plain number written in the quote, with no scale words applied."""
    return tuple(Decimal(m.group("num").replace(",", "")) for m in _NUMBER_RE.finditer(quote))


def _currency_code(token: str) -> str | None:
    token = token.lower().rstrip(".")
    token = "shillings" if "shilling" in token else token
    return next((code for code, tokens in CURRENCY_TOKENS.items() if token in tokens), None)


def parse_currencies(quote: str) -> frozenset[str]:
    """The currencies the quote names, by marker."""
    return frozenset(c for m in _CURRENCY_RE.finditer(quote) if (c := _currency_code(m.group(0))) is not None)


def parse_coordinates(quote: str) -> tuple[Decimal, ...]:
    """Every decimal coordinate in the quote, signed by a minus sign or an S/W hemisphere letter."""
    values = []
    for m in _COORD_RE.finditer(quote):
        value = Decimal(m.group("num"))
        negative = m.group("sign") is not None or (m.group("hem") or "").upper() in ("S", "W")
        values.append(-value if negative else value)
    return tuple(values)


def parse_count_words(quote: str) -> frozenset[int]:
    """Whole numbers written as digits or as number words."""
    counts = {int(n) for n in parse_numbers(quote) if n == n.to_integral_value()}
    counts.update(_NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", quote.lower()) if w in _NUMBER_WORDS)
    return frozenset(counts)


@dataclass(frozen=True)
class NumberToken:
    """A number in the source and what its context says it is.

    kind is money (a currency marker is attached), money_uncertain (a scale
    word or value wording but no currency), area, count, date, percent or
    plain. Positions are offsets in the text that was classified.
    """

    value: Decimal
    raw: Decimal
    start: int
    end: int
    kind: str
    currency: str | None
    currency_start: int | None
    currency_end: int | None
    policy_term: bool
    qualified: bool
    in_range: bool


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z~][a-z-]*", text.lower())


def classify_numbers(text: str) -> tuple[NumberToken, ...]:
    """Every number in text, classified by its immediate context. Deterministic; no meaning is guessed."""
    tokens = []
    for m in _AMOUNT_RE.finditer(text):
        num = Decimal(m.group("num").replace(",", ""))
        scale = (m.group("scale") or "").lower()
        value = num.scaleb(_SCALES[scale]) if scale else num
        start, end = m.start(), m.end()
        before, after = text[max(0, start - 40):start], text[end:end + 60]
        lower_before, lower_after = before.lower(), after.lower()

        currency, cur_span = None, (None, None)
        if (cb := _CUR_BEFORE.search(lower_before)) is not None:
            marker = cb.group(1) or cb.group(2)
            currency, cur_span = _currency_code(marker), (start - len(before) + cb.start(), start - len(before) + cb.end())
        elif (ca := _CUR_AFTER.match(lower_after)) is not None:
            currency, cur_span = _currency_code(ca.group(1)), (end + ca.start(1), end + ca.end(1))

        integral = value == value.to_integral_value()
        four_digit_year = not scale and integral and len(m.group("num")) == 4 and 1900 <= value <= 2100
        dated = (re.search(r"\d\s*[/-]\s*$", lower_before) is not None
                 or re.match(r"^\s*[/-]\s*\d", lower_after) is not None
                 or re.match(r"^\s*" + _MONTH + r"(?![a-z])", lower_after) is not None
                 or re.search(r"(?<![a-z])" + _MONTH + r"\s*$", lower_before) is not None)
        recent = _words(before)[-4:]

        if _AREA_AFTER.match(lower_after):
            kind = "area"
        elif lower_after.lstrip().startswith("%"):
            kind = "percent"
        elif currency is None and (four_digit_year or (dated and not scale)):
            kind = "date"
        elif currency is not None:
            kind = "money"
        elif not scale and integral and _COUNT_AFTER.match(lower_after):
            kind = "count"
        elif scale or any(w in _VALUE_WORDS for w in recent):
            kind = "money_uncertain"
        else:
            kind = "plain"

        in_range = (re.match(r"^\s*(?:-|–|to)\s*(?:kes|kshs?|sh|usd|us\$|\$)?\.?\s*\d", lower_after) is not None
                    or re.search(r"(?:between|from)\s*(?:kes|kshs?|sh|usd|us\$|\$)?\.?\s*$", lower_before) is not None
                    or re.search(r"\d\s*[a-z]{0,3}\s*(?:-|–|to)\s*(?:kes|kshs?|sh|usd|us\$|\$)?\.?\s*$", lower_before) is not None)
        joined = " ".join(recent)
        tokens.append(NumberToken(
            value=value, raw=num, start=start, end=end, kind=kind, currency=currency,
            currency_start=cur_span[0], currency_end=cur_span[1],
            policy_term=any(w in _POLICY_WORDS for w in recent),
            qualified=(any(w in _QUALIFIER_WORDS for w in recent) or "up to" in joined
                       or re.search(r"~\s*(?:[a-z$€£]+\.?\s*)?$", lower_before) is not None),
            in_range=in_range,
        ))
    return tuple(tokens)


def _word_counts(text: str) -> tuple[tuple[int, int, bool], ...]:
    """Number words with their position and whether a building noun follows within three words."""
    found = []
    for m in re.finditer(r"(?i)(?<![a-z])(" + "|".join(_NUMBER_WORDS) + r")(?![a-z])", text):
        found.append((_NUMBER_WORDS[m.group(1).lower()], m.start(),
                      _COUNT_AFTER.match(text[m.end():m.end() + 60].lower()) is not None))
    return tuple(found)


def _has_phrase(text: str, phrase: str) -> bool:
    """The phrase as whole words, allowing a plural ending: "house" matches "houses"."""
    return re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?:e?s)?(?![a-z])", text.lower()) is not None


# --- Result records ------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldCheck:
    """One check on one field of one item. result is pass, review or reject."""

    item_id: str | None
    field: str
    check: str
    result: str
    detail: str

    def __post_init__(self):
        if self.result not in CHECK_RESULTS:
            raise ValueError(f"result must be one of {list(CHECK_RESULTS)}")


@dataclass(frozen=True)
class VerificationReport:
    """The verifier's finding for one candidate. Never an approval.

    `outcome` is valid_candidate, needs_review or rejected. Every candidate
    still requires human approval, whatever its outcome; `requires_human_approval`
    is always True and there is no approved state here.
    """

    report_format: str
    schema_version: str
    document_id: str
    document_sha256: str
    extraction_id: str | None
    response_sha256: str
    outcome: str
    checks: tuple[FieldCheck, ...]
    requires_human_approval: bool = True

    def __post_init__(self):
        object.__setattr__(self, "checks", tuple(self.checks))
        if self.outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {list(OUTCOMES)}")
        if self.requires_human_approval is not True:
            raise ValueError("verification never replaces human approval")
        expected = _outcome(self.checks)
        if self.outcome != expected:
            raise ValueError(f"outcome {self.outcome!r} does not follow from the checks ({expected!r})")

    def failures(self, result: str) -> tuple[FieldCheck, ...]:
        return tuple(c for c in self.checks if c.result == result)

    def to_dict(self) -> dict:
        data = {k: getattr(self, k) for k in self.__dataclass_fields__}
        data["checks"] = [{"item_id": c.item_id, "field": c.field, "check": c.check, "result": c.result,
                           "detail": c.detail} for c in self.checks]
        return data

    def to_json(self) -> str:
        """Sorted keys, two-space indent, UTF-8, no NaN: the run record's file form."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"

    @classmethod
    def from_dict(cls, data: dict) -> "VerificationReport":
        values = dict(data)
        values["checks"] = tuple(FieldCheck(**c) for c in data["checks"])
        return cls(**values)


def _outcome(checks) -> str:
    results = {c.result for c in checks}
    if "reject" in results:
        return "rejected"
    if "review" in results:
        return "needs_review"
    return "valid_candidate"


# --- The verifier --------------------------------------------------------------------------


class _Checks:
    """Collects FieldChecks for one candidate, and where each item's evidence sits in the source."""

    def __init__(self):
        self.items: list[FieldCheck] = []
        self.segments: dict[str, list[frozenset[int]]] = {}
        self.value_tokens: dict[int, list[str]] = {}

    def add(self, item_id, field, check, result, detail=""):
        self.items.append(FieldCheck(item_id, field, check, result, detail))

    def ok(self, item_id, field, check, condition, failure, detail):
        self.add(item_id, field, check, "pass" if condition else failure, "" if condition else detail)
        return condition


class _Source:
    """The source text, its numbers and its sentences, computed once."""

    def __init__(self, text: str):
        self.text = text
        self.tokens = classify_numbers(text)
        self.words = _word_counts(text)
        bounds = [0] + [m.end() for m in re.finditer(r"(?:\r?\n)+|(?<=[;!?])\s+|(?<=\.)\s+(?=[A-Z])", text)]
        self.segment_starts = sorted(set(bounds))

    def occurrences(self, quote: str) -> list[int]:
        starts, i = [], self.text.find(quote)
        while i != -1:
            starts.append(i)
            i = self.text.find(quote, i + 1)
        return starts

    def segment(self, position: int) -> int:
        return bisect.bisect_right(self.segment_starts, position) - 1

    def segment_bounds(self, position: int) -> tuple[int, int]:
        index = self.segment(position)
        end = self.segment_starts[index + 1] if index + 1 < len(self.segment_starts) else len(self.text)
        return self.segment_starts[index], end

    def tokens_in(self, start: int, end: int) -> list[NumberToken]:
        return [t for t in self.tokens if start <= t.start and t.end <= end]


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _decimal(x) -> Decimal:
    return Decimal(str(x))


def _check_shape(c: _Checks, item_id, field, obj, keys) -> bool:
    if not isinstance(obj, dict):
        c.add(item_id, field, "schema", "reject", f"{field} must be an object")
        return False
    if set(obj) != set(keys):
        c.add(item_id, field, "schema", "reject", f"{field} must have exactly the keys {list(keys)}, got {sorted(obj)}")
        return False
    return True


def _locate(c: _Checks, src: _Source, item_id, field, quote) -> int | None:
    """The quote's position in the source, recording its sentence for the item. None if it repeats."""
    starts = src.occurrences(quote)
    c.segments.setdefault(item_id, []).append(frozenset(src.segment(s) for s in starts))
    if len(starts) > 1:
        c.add(item_id, field, "quote_repeated", "review",
              f"the quote occurs {len(starts)} times in the source; which occurrence is meant cannot be pinned down")
        return None
    return starts[0] if starts else None


def _check_quote(c: _Checks, item_id, field, quote, text, required: bool) -> bool:
    """A quote, when present, must occur in the source exactly as written."""
    if quote is None:
        return c.ok(item_id, field, "quote_present", not required, "reject", "a stated value needs a supporting quote")
    if not isinstance(quote, str) or not quote.strip():
        c.add(item_id, field, "quote_type", "reject", "quote must be a non-empty string or null")
        return False
    return c.ok(item_id, field, "quote_in_source", quote in text, "reject",
                f"quote is not an exact substring of the source: {quote!r}")


def _check_status(c: _Checks, item_id, field, status, allowed) -> bool:
    return c.ok(item_id, field, "status", status in allowed, "reject", f"status must be one of {list(allowed)}, got {status!r}")


def _check_missing(c: _Checks, item_id, field, values: dict, quote) -> None:
    """A missing field must carry no value; a human must supply it later."""
    c.ok(item_id, field, "missing_is_empty", all(v is None for v in values.values()) and quote is None, "reject",
         f"status is missing but values were given: {values}")


def _verify_location(c: _Checks, src: _Source, item_id, loc) -> None:
    if not _check_shape(c, item_id, "location", loc, FIELD_KEYS["location"]):
        return
    if not _check_status(c, item_id, "location", loc["status"], LOCATION_STATUSES):
        return
    status, quote = loc["status"], loc["quote"]
    if status == "missing":
        _check_missing(c, item_id, "location", {"text": loc["text"], "lat": loc["lat"], "lon": loc["lon"]}, quote)
        c.add(item_id, "location", "location_missing", "review", "no location stated; a human must supply one")
        return
    if not _check_quote(c, item_id, "location", quote, src.text, required=True):
        return
    c.segments.setdefault(item_id, []).append(frozenset(src.segment(s) for s in src.occurrences(quote)))
    if status == "ambiguous":
        c.add(item_id, "location", "location_ambiguous", "review", "location is ambiguous in the source")
    if loc["text"] is not None:
        c.ok(item_id, "location", "text_in_quote", isinstance(loc["text"], str) and loc["text"] in quote, "reject",
             "location text must appear in its quote")
    stated = [(k, loc[k]) for k in ("lat", "lon") if loc[k] is not None]
    if stated and len(stated) != 2:
        c.add(item_id, "location", "coordinates_pair", "reject", "latitude and longitude must be given together")
        return
    if stated:
        lat, lon = loc["lat"], loc["lon"]
        if not c.ok(item_id, "location", "coordinate_type", _is_number(lat) and _is_number(lon), "reject",
                    "coordinates must be finite numbers"):
            return
        if not c.ok(item_id, "location", "coordinate_range", -90 <= lat <= 90 and -180 <= lon <= 180, "reject",
                    f"coordinates out of range: {lat}, {lon}"):
            return
        written = parse_coordinates(quote)
        c.ok(item_id, "location", "coordinates_in_quote", _decimal(lat) in written and _decimal(lon) in written,
             "reject", f"coordinates {lat}, {lon} are not written in the quote")
    elif loc["text"] is None:
        c.add(item_id, "location", "location_empty", "reject", "a stated location needs text or coordinates")


def _verify_class(c: _Checks, src: _Source, item_id, cls) -> None:
    if not _check_shape(c, item_id, "housing_class", cls, FIELD_KEYS["housing_class"]):
        return
    if not _check_status(c, item_id, "housing_class", cls["status"], CLASS_STATUSES):
        return
    status, value, quote, rule = cls["status"], cls["value"], cls["quote"], cls["mapping_rule"]
    if status == "missing":
        _check_missing(c, item_id, "housing_class", {"value": value, "mapping_rule": rule}, quote)
        c.add(item_id, "housing_class", "class_missing", "review", "no construction type stated; a human must supply one")
        return
    if not _check_quote(c, item_id, "housing_class", quote, src.text, required=True):
        return
    c.segments.setdefault(item_id, []).append(frozenset(src.segment(s) for s in src.occurrences(quote)))
    if status == "ambiguous":
        c.ok(item_id, "housing_class", "ambiguous_has_no_value", value is None and rule is None, "reject",
             "an ambiguous class must not carry a value")
        c.add(item_id, "housing_class", "class_ambiguous", "review", "construction type is ambiguous in the source")
        return
    if not c.ok(item_id, "housing_class", "class_value", value in HOUSING_CLASSES, "reject",
                f"housing class must be one of {list(HOUSING_CLASSES)}, got {value!r}"):
        return
    named = {k for k, phrases in STATED_CLASS_PHRASES.items() if any(_has_phrase(quote, p) for p in phrases)}
    if status == "stated":
        c.ok(item_id, "housing_class", "rule_absent", rule is None, "reject", "a stated class must not cite a mapping rule")
        c.ok(item_id, "housing_class", "class_named_in_quote", value in named, "reject",
             f"the quote does not name {value!r}; a reading of a description is a mapping, not a statement")
        return
    # mapped
    if not c.ok(item_id, "housing_class", "mapping_rule", rule in CLASS_MAPPING_RULES, "reject",
                f"a mapped class must cite one of {list(CLASS_MAPPING_RULES)}, got {rule!r}"):
        return
    target, phrases = CLASS_MAPPING_RULES[rule]
    c.ok(item_id, "housing_class", "rule_target", target == value, "reject", f"rule {rule} maps to {target!r}, not {value!r}")
    if not any(_has_phrase(quote, p) for p in phrases):
        material = [w for w in MATERIAL_WORDS if _has_phrase(quote, w)]
        c.add(item_id, "housing_class", "rule_phrase_in_quote", "reject",
              f"the quote contains none of the phrases of rule {rule}"
              + (f"; a material alone ({', '.join(material)}) leaves the class ambiguous" if material else ""))
        return
    rivals = ({t for _, (t, ps) in CLASS_MAPPING_RULES.items() if any(_has_phrase(quote, p) for p in ps)} | named
              | {cls for word, cls in MATERIAL_CLASSES.items() if _has_phrase(quote, word)})
    if rivals - {value}:
        c.add(item_id, "housing_class", "class_conflict", "review", f"the quote also suggests {sorted(rivals - {value})}")
    c.add(item_id, "housing_class", "mapping_needs_confirmation", "review",
          f"class mapped by rule {rule} [A] from {quote!r}; a human must confirm it")


def _verify_count(c: _Checks, src: _Source, item_id, count):
    """Returns the verified count, or None."""
    if not _check_shape(c, item_id, "building_count", count, FIELD_KEYS["building_count"]):
        return None
    if not _check_status(c, item_id, "building_count", count["status"], VALUE_STATUSES):
        return None
    status, value, quote = count["status"], count["value"], count["quote"]
    if status == "missing":
        _check_missing(c, item_id, "building_count", {"value": value}, quote)
        c.add(item_id, "building_count", "count_missing", "review", "no building count stated; a human must supply one")
        return None
    if not _check_quote(c, item_id, "building_count", quote, src.text, required=True):
        return None
    start = _locate(c, src, item_id, "building_count", quote)
    if status == "ambiguous":
        c.ok(item_id, "building_count", "ambiguous_has_no_value", value is None, "reject", "an ambiguous count must not carry a value")
        c.add(item_id, "building_count", "count_ambiguous", "review", "building count is ambiguous in the source")
        return None
    if not c.ok(item_id, "building_count", "count_type", isinstance(value, int) and not isinstance(value, bool), "reject",
                f"building count must be a whole number, got {value!r}"):
        return None
    if not c.ok(item_id, "building_count", "count_positive", value > 0, "reject", f"building count must be above 0, got {value}"):
        return None
    if not c.ok(item_id, "building_count", "count_in_quote", value in parse_count_words(quote), "reject",
                f"the count {value} is not written in the quote"):
        return None
    end = None if start is None else start + len(quote)
    digits = [t for t in (src.tokens_in(start, end) if start is not None else classify_numbers(quote))
              if t.raw == value]
    words = [w for w in src.words if start is not None and start <= w[1] < end and w[0] == value] \
        if start is not None else [(v, p, n) for v, p, n in _word_counts(quote) if v == value]
    if any(t.kind == "count" for t in digits) or any(named for _, _, named in words):
        c.add(item_id, "building_count", "count_is_a_count", "pass")
    elif digits and all(t.kind in ("money", "money_uncertain", "area", "date", "percent") for t in digits) and not words:
        c.add(item_id, "building_count", "count_is_a_count", "reject",
              f"the number {value} in the quote is a {digits[0].kind}, not a building count")
    else:
        c.add(item_id, "building_count", "count_is_a_count", "review",
              f"the number {value} is not followed by a building noun; a human must confirm it is the count")
    return value


def _verify_money(c: _Checks, src: _Source, item_id, field, amount, currency, quote, start):
    """Checks a monetary value against a money number in its quote. Returns that number, or None."""
    if not c.ok(item_id, field, "amount_type", _is_number(amount), "reject", f"amount must be a finite number, got {amount!r}"):
        return None
    if not c.ok(item_id, field, "amount_positive", amount > 0, "reject", f"amount must be above 0, got {amount}"):
        return None
    end = None if start is None else start + len(quote)
    tokens = src.tokens_in(start, end) if start is not None else list(classify_numbers(quote))
    offset = start if start is not None else 0
    matches = [t for t in tokens if t.value == _decimal(amount)]
    money = [t for t in matches if t.kind in ("money", "money_uncertain")]
    if not matches:
        c.add(item_id, field, "amount_in_quote", "reject", f"the amount {amount} is not written in the quote")
        return None
    if not money:
        kinds = sorted({t.kind for t in matches})
        if any(k in ("area", "count", "date", "percent") for k in kinds):
            c.add(item_id, field, "amount_is_money", "reject",
                  f"the number {amount} in the quote is a {', '.join(kinds)}, not a monetary amount")
        else:
            c.add(item_id, field, "amount_is_money", "review",
                  f"the number {amount} carries no currency or scale; a human must confirm it is the insured value")
        return None
    token = money[0]
    c.add(item_id, field, "amount_in_quote", "pass")
    others = [t for t in tokens if t.kind in ("money", "money_uncertain") and t.start != token.start]
    if others:
        c.add(item_id, field, "several_amounts", "review",
              f"the quote contains {len(others) + 1} monetary amounts; a human must confirm which is meant")
    if token.policy_term:
        c.add(item_id, field, "amount_is_policy_term", "review", "the amount follows policy wording (deductible, limit...)")
    if token.qualified:
        c.add(item_id, field, "amount_approximate", "review", "the amount is qualified (about, up to, over...)")
    if token.in_range:
        c.add(item_id, field, "amount_range", "review", "the amount is part of a range")

    # Currency: only a marker inside the quote counts as evidence.
    attached = token.currency if (token.currency_start is not None and start is not None
                                  and start <= token.currency_start and token.currency_end <= end) else (
        token.currency if start is None else None)
    named = parse_currencies(quote)
    if currency is None:
        c.add(item_id, field, "currency_missing", "review",
              ("no currency recorded" + (f" (the quote names {', '.join(sorted(named))})" if named else ""))
              + "; KES is not established")
    elif not isinstance(currency, str) or currency not in CURRENCY_TOKENS:
        c.add(item_id, field, "currency_value", "reject", f"currency must be one of {list(CURRENCY_TOKENS)} or null")
    elif attached is not None and attached != currency:
        c.add(item_id, field, "currency_matches_amount", "reject",
              f"the amount is marked {attached} in the quote, not {currency}")
    elif attached is None and currency not in named:
        c.add(item_id, field, "currency_in_quote", "reject", f"the quote does not name the currency {currency}")
    elif attached is None:
        c.add(item_id, field, "currency_not_attached", "review",
              f"{currency} appears in the quote but not next to this amount; a human must confirm it")
    elif len(named) > 1:
        c.add(item_id, field, "currency_conflict", "review", f"the quote names several currencies: {sorted(named)}")
    elif currency != "KES":
        c.add(item_id, field, "currency_not_kes", "review",
              f"amount is in {currency}; the engine is KES-only and no conversion is made")
    else:
        c.add(item_id, field, "currency_kes", "pass")
    return token if start is not None else None


def _basis_cues(text: str) -> tuple[bool, bool]:
    return (any(_has_phrase(text, cue) for cue in PER_BUILDING_CUES), any(_has_phrase(text, cue) for cue in TOTAL_CUES))


def _verify_insured_value(c: _Checks, src: _Source, item_id, value, count) -> None:
    if not _check_shape(c, item_id, "insured_value", value, FIELD_KEYS["insured_value"]):
        return
    basis, amount, currency, quote = value["basis"], value["amount"], value["currency"], value["quote"]
    if not c.ok(item_id, "insured_value", "basis", basis in VALUE_BASES, "reject",
                f"basis must be one of {list(VALUE_BASES)}, got {basis!r}"):
        return
    if basis == "missing":
        _check_missing(c, item_id, "insured_value", {"amount": amount, "currency": currency}, quote)
        c.add(item_id, "insured_value", "value_missing", "review", "no insured value stated; a human must supply one")
        return
    if not _check_quote(c, item_id, "insured_value", quote, src.text, required=True):
        return
    start = _locate(c, src, item_id, "insured_value", quote)
    if basis == "ambiguous":
        c.add(item_id, "insured_value", "basis_ambiguous", "review", "whether the amount is per building or a total is unclear")
        if amount is not None:
            _verify_money(c, src, item_id, "insured_value", amount, currency, quote, start)
        return
    token = _verify_money(c, src, item_id, "insured_value", amount, currency, quote, start)
    if token is not None:
        c.value_tokens.setdefault(token.start, []).append(item_id)

    # Basis wording: inside the quote it is evidence; around the amount, in the
    # same sentence, it can only contradict or call for review.
    q_per, q_tot = _basis_cues(quote)
    if token is not None:
        seg_start, seg_end = src.segment_bounds(token.start)
        around = src.text[max(seg_start, token.start - 35):min(seg_end, token.end + 30)]
        a_per, a_tot = _basis_cues(around)
    else:
        a_per, a_tot = q_per, q_tot
    if (q_per and q_tot) or (a_per and a_tot):
        c.add(item_id, "insured_value", "basis_conflict", "review", "per-building and total wording both appear by the amount")
    elif basis == "per_building" and (q_tot or a_tot):
        c.add(item_id, "insured_value", "basis_matches_source", "reject", "the source describes a total, not a per-building value")
    elif basis == "total" and (q_per or a_per):
        c.add(item_id, "insured_value", "basis_matches_source", "reject", "the source describes a per-building value, not a total")
    elif (basis == "per_building" and q_per) or (basis == "total" and q_tot):
        c.add(item_id, "insured_value", "basis_matches_source", "pass")
    elif (a_per or a_tot):
        c.add(item_id, "insured_value", "basis_cue_outside_quote", "review",
              "the per-building or total wording is next to the amount but outside the quote")
    elif count == 1 and basis == "per_building":
        c.add(item_id, "insured_value", "basis_matches_source", "pass")
    else:
        c.add(item_id, "insured_value", "basis_unsupported", "review",
              f"the source does not say whether the amount is per building or a total ({count or 'unknown'} buildings)")
    if basis == "total" and count != 1:
        c.add(item_id, "insured_value", "total_needs_allocation", "review",
              "a total value is never split automatically; a human must decide the value per building")


def _verify_optional(c: _Checks, src: _Source, item_id, field, obj) -> None:
    """Floor area and cost per m²: carried through only, never used for TIV (D-001)."""
    if not _check_shape(c, item_id, field, obj, FIELD_KEYS[field]):
        return
    if not _check_status(c, item_id, field, obj["status"], VALUE_STATUSES):
        return
    numeric = "value" if field == "floor_area_m2" else "amount"
    extra = {} if field == "floor_area_m2" else {"currency": obj["currency"]}
    if obj["status"] == "missing":
        _check_missing(c, item_id, field, {numeric: obj[numeric], **extra}, obj["quote"])
        return
    if not _check_quote(c, item_id, field, obj["quote"], src.text, required=True):
        return
    start = _locate(c, src, item_id, field, obj["quote"])
    if obj["status"] == "ambiguous":
        c.add(item_id, field, "value_ambiguous", "review", f"{field} is ambiguous in the source")
        return
    if field == "cost_per_m2":
        _verify_money(c, src, item_id, field, obj["amount"], obj["currency"], obj["quote"], start)
        return
    value = obj["value"]
    if not c.ok(item_id, field, "value_type", _is_number(value) and value > 0, "reject",
                f"{field} must be a finite number above 0, got {value!r}"):
        return
    tokens = (src.tokens_in(start, start + len(obj["quote"])) if start is not None
              else list(classify_numbers(obj["quote"])))
    matches = [t for t in tokens if t.value == _decimal(value)]
    if not matches:
        c.add(item_id, field, "value_in_quote", "reject", f"the value {value} is not written in the quote")
    elif any(t.kind == "area" for t in matches):
        c.add(item_id, field, "value_in_quote", "pass")
    elif any(t.kind == "plain" for t in matches):
        c.add(item_id, field, "value_is_area", "review", f"the number {value} has no area unit; a human must confirm it")
    else:
        c.add(item_id, field, "value_is_area", "reject", f"the number {value} in the quote is a {matches[0].kind}, not an area")


def _verify_terms(c: _Checks, src: _Source, item_id, terms) -> None:
    """Policy terms are kept as information only. The engine models structural ground-up loss."""
    if not isinstance(terms, list):
        c.add(item_id, "unmodelled_terms", "schema", "reject", "unmodelled_terms must be a list")
        return
    for i, term in enumerate(terms):
        field = f"unmodelled_terms[{i}]"
        if not _check_shape(c, item_id, field, term, TERM_KEYS):
            continue
        c.ok(item_id, field, "kind", term["kind"] in UNMODELLED_KINDS, "reject", f"kind must be one of {list(UNMODELLED_KINDS)}")
        c.ok(item_id, field, "text", isinstance(term["text"], str) and term["text"].strip() != "", "reject", "text must be non-empty")
        _check_quote(c, item_id, field, term["quote"], src.text, required=True)
        c.add(item_id, field, "not_modelled", "pass", "recorded as information; not applied to the loss engine")


def _verify_associations(c: _Checks) -> None:
    """Across fields and items: evidence for one item should come from one sentence, and one amount
    should not be cited as the value of several items. Neither can be proved, so both go to review."""
    for item_id, segment_sets in c.segments.items():
        located = [s for s in segment_sets if s]
        if located and not frozenset.intersection(*located):
            c.add(item_id, "item", "evidence_in_one_sentence", "review",
                  "this item's quotes come from different sentences; a human must confirm they describe one building group")
    for position, item_ids in c.value_tokens.items():
        if len(item_ids) > 1:
            for item_id in item_ids:
                c.add(item_id, "insured_value", "value_shared", "review",
                      f"the same amount in the source is cited as the value of items {sorted(item_ids)}")


def verify_candidate(document: SourceDocument, response_text: str, *, extraction_id: str | None = None) -> VerificationReport:
    """Check one AI candidate against its source document. Neither input is changed."""
    c = _Checks()
    src = _Source(document.text)
    if document.kind != "exposure_text":
        c.add(None, "document", "document_kind", "reject", f"expected an exposure_text document, got {document.kind!r}")
    try:
        candidate = json.loads(response_text)
    except (TypeError, ValueError) as error:
        candidate = None
        c.add(None, "response", "json", "reject", f"the response is not valid JSON: {error}")
    if candidate is not None:
        if not isinstance(candidate, dict) or set(candidate) != {"items"} or not isinstance(candidate["items"], list):
            c.add(None, "response", "schema", "reject", 'the response must be an object with exactly one key, "items" (a list)')
        elif not candidate["items"]:
            c.add(None, "items", "items_present", "review", "no exposure was found in the submission")
        else:
            seen = set()
            for index, item in enumerate(candidate["items"]):
                if not isinstance(item, dict) or set(item) != set(ITEM_KEYS):
                    c.add(None, f"items[{index}]", "schema", "reject", f"each item must have exactly the keys {list(ITEM_KEYS)}")
                    continue
                item_id = item["item_id"]
                if not c.ok(item_id if isinstance(item_id, str) else None, "item_id", "item_id",
                            isinstance(item_id, str) and item_id.strip() != "" and item_id not in seen, "reject",
                            f"item_id must be a unique non-empty string, got {item_id!r}"):
                    continue
                seen.add(item_id)
                _verify_location(c, src, item_id, item["location"])
                _verify_class(c, src, item_id, item["housing_class"])
                count = _verify_count(c, src, item_id, item["building_count"])
                _verify_insured_value(c, src, item_id, item["insured_value"], count)
                _verify_optional(c, src, item_id, "floor_area_m2", item["floor_area_m2"])
                _verify_optional(c, src, item_id, "cost_per_m2", item["cost_per_m2"])
                _verify_terms(c, src, item_id, item["unmodelled_terms"])
            _verify_associations(c)
    response_sha256 = sha256_text(response_text) if isinstance(response_text, str) else ""
    return VerificationReport(REPORT_FORMAT, SCHEMA_VERSION, document.document_id, document.sha256, extraction_id,
                              response_sha256, _outcome(c.items), tuple(c.items))


def verify_extraction(record: AIExtractionRecord, document: SourceDocument) -> VerificationReport:
    """Check a recorded extraction: its provenance first, then its candidate."""
    c = _Checks()
    c.ok(None, "extraction", "extraction_mode", record.mode == "exposure", "reject", f"expected an exposure extraction, got {record.mode!r}")
    c.ok(None, "extraction", "extraction_status", record.status == "ok", "reject",
         f"the provider did not return a usable response (status {record.status!r})")
    c.ok(None, "extraction", "schema_version", record.schema_version == SCHEMA_VERSION, "reject",
         f"expected schema {SCHEMA_VERSION!r}, got {record.schema_version!r}")
    c.ok(None, "document", "single_document", record.document_ids == (document.document_id,), "reject",
         "an exposure extraction must be of exactly this one document")
    c.ok(None, "document", "document_hash", record.document_hashes == (document.sha256,), "reject",
         "the document's hash differs from the one recorded at extraction: the text has changed")
    if record.response_text is None:
        checks = tuple(c.items) + (FieldCheck(None, "response", "response_present", "reject", "no response was recorded"),)
        return VerificationReport(REPORT_FORMAT, SCHEMA_VERSION, document.document_id, document.sha256,
                                  record.extraction_id, "", _outcome(checks), checks)
    candidate = verify_candidate(document, record.response_text, extraction_id=record.extraction_id)
    checks = tuple(c.items) + candidate.checks
    return VerificationReport(REPORT_FORMAT, SCHEMA_VERSION, document.document_id, document.sha256,
                              record.extraction_id, candidate.response_sha256, _outcome(checks), checks)
