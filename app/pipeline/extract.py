"""Deterministic statement extraction and the first rules pass.

The demo is honest about where the data came from. This module parses the synthetic
statement text with regular expressions and fixed layout assumptions. It does not
call a model, it does not read the evaluation labels, and it does not infer facts the
text does not show. A recorded model run in ``data/cached/extract-<document-id>.json``
is used only when that file's ``mode`` says a live model produced it; a cache file
written by this module is never played back as a model result.

Output shape (stable, used by the UI and by ``pipeline.eval``)::

    {
      "fields": [{"name", "value", "confidence", "source_span"}, ...],
      "flags":  [{"kind", "detail", "severity"}, ...],
      "document_id": "...",
      "method": "deterministic extractor"
    }

Confidence is a display value, not a calibrated probability. It reflects how explicit
the evidence was: a labelled line scores higher than a value inferred from a title.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import date
from typing import Any, Iterable, Optional

from app.llm import MODE_DETERMINISTIC_EXTRACT, is_recorded_model_run, load_cached

from . import (
    REPO_ROOT,
    data_dir,
    doc_id,
    document_text,
    load_book,
    load_json,
    parse_date,
    to_number,
    unique,
)

# ---------------------------------------------------------------------------
# Tunables: regexes and vocabulary
# ---------------------------------------------------------------------------

#: A money or quantity token: optional currency sign, commas, parentheses for
#: negatives, and an optional trailing percent. Deliberately permissive because the
#: seed statements are hand-written and do not share one layout.
NUM = r"\(?-?\$?\s?-?[\d,]+(?:\.\d+)?\)?%?"
NUM_TOKEN = r"\(?-?\$?\s?-?[\d,]+(?:\.\d+)?\)?%?"

# CASH stays in: a cash line is a real position on these statements and the account
# summary counts it. Only table scaffolding and totals are skipped.
SKIP_POSITION_SYMBOLS = {
    "TOTAL",
    "SUBTOTAL",
    "POSITIONS",
    "SYMBOL",
    "ACCOUNT",
    "STATEMENT",
    "PAGE",
    "TOTALS",
}

POSITION_HEADER_WORDS = ("symbol", "security", "description")
POSITION_QUANTITY_WORDS = ("quantity", "qty", "shares", "units")
POSITION_VALUE_WORDS = ("value", "market value", "amount")

#: Labels are matched case-insensitively at the start of a line. The first match
#: wins, so put the most specific spelling first.
LABELS = {
    "registration": (
        "account registration",
        "registration type",
        "registration",
        "registered to",
    ),
    "title": (
        "account title",
        "account name",
        "account holder",
        "titled",
        "title",
    ),
    "account_number": (
        "account number",
        "account no",
        "account #",
        "acct number",
        "acct no",
        "acct #",
        "account",
    ),
    "period": (
        "statement period",
        "statement cycle",
        "for the period",
        "period covered",
        "period",
    ),
    "address": (
        "address on file",
        "address of record",
        "mailing address",
        "address",
    ),
    "beneficiary": (
        "primary beneficiary",
        "beneficiary designation",
        "beneficiaries",
        "beneficiary",
        "tod beneficiary",
        "transfer on death",
    ),
    "ein": (
        "trust ein",
        "taxpayer id",
        "tax id",
        "ein",
    ),
}

TRUST_WORDS = ("trust", "trustee", "irrevocable", "revocable")
IRA_WORDS = ("ira", "roth", "sep ira", "simple ira", "inherited ira", "rollover ira")
UTMA_WORDS = ("utma", "ugma", "custodial")
ENTITY_WORDS = ("llc", "corporation", "inc.", "partnership", "llp", "l.p.")

#: Registration mismatch compares broad categories, not names. Two lines match when
#: they share at least one category. Lines with no category at all are not compared.
REGISTRATION_CATEGORIES = {
    "individual": ("individual", "single owner", "sole owner"),
    "joint": (
        "joint",
        "jtwros",
        "tenants by the entirety",
        "tenants in common",
        "tenancy in common",
        "community property",
    ),
    "trust": TRUST_WORDS,
    "retirement": IRA_WORDS,
    "utma": UTMA_WORDS,
    "entity": ENTITY_WORDS,
}

STALE_ADDRESS_MARKERS = (
    "stale address",
    "address verification required",
    "verify address",
    "address not verified",
    "returned mail",
    "address returned",
    "bad address",
    "undeliverable",
)

NON_TRANSFERABLE_MARKERS = (
    "non-transferable",
    "not transferable",
    "not available at",
    "not held at",
    "proprietary fund",
    "proprietary share class",
    "restricted security",
    "private placement",
)

#: "Class C" alone is too weak. It only counts next to a fund or share-class word.
CLASS_C_CONTEXT = ("share", "fund", "class c shares", "share class c")

ANNUITY_SURRENDER_MARKERS = (
    "surrender charge",
    "surrender period",
    "surrender value",
    "contingent deferred sales charge",
    "cdsc",
)

BENEFICIARY_MISSING_MARKERS = (
    "none designated",
    "not designated",
    "no beneficiary",
    "beneficiary not on file",
    "beneficiary pending",
)

#: An EIN field with one of these values is present on the page but not usable, so
#: the trust EIN finding still fires.
EIN_PLACEHOLDERS = {
    "",
    "pending",
    "not issued",
    "not provided",
    "not on file",
    "none",
    "tbd",
    "n/a",
    "missing",
}

#: These documents are structured summaries, not holdings statements. Their labelled
#: lines are extracted generically; the statements use the explicit rules below.
SUMMARY_DOCUMENT_KINDS = {"trust summary", "annuity summary", "crm export"}

#: Labels on a summary line that are page furniture, not data.
SUMMARY_SKIP_LABELS = {
    "notes",
    "questions",
    "prepared",
    "end of statement",
    "specimen",
}

#: The account-level summary block on a statement.
SUMMARY_FIELD_SPECS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("account_value", ("total account value", "account value", "total value"), "number"),
    ("cost_basis", ("total cost basis", "cost basis", "total basis"), "number"),
    ("positions_count", ("positions held", "number of positions"), "integer"),
    ("custodian", ("custodian", "custodian name"), "text"),
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_document(document: dict[str, Any]) -> dict[str, Any]:
    """Extract fields and raise first-pass flags from one synthetic document.

    ``document`` needs an id (or a filename) and text under one of the keys
    ``text``, ``content``, ``body``, ``statement_text``, or ``lines``. Missing text
    returns an empty field list and a high-severity ``document_unreadable`` flag
    rather than invented values.
    """

    if not isinstance(document, dict):
        raise TypeError("extract_document expects a document dict")

    identifier = doc_id(document)
    cached = _recorded_model_extraction(identifier)
    if cached is not None:
        cached["document_id"] = identifier
        cached["method"] = "live model, recorded (cached)"
        return cached

    text = document_text(document)
    if not text.strip() and isinstance(document.get("path"), str):
        text = _read_relative_text(document["path"])
    if not text.strip():
        # The book stores document metadata rows (id, kind, filename). Resolve the
        # full text from data/documents/<id>.json so both call styles work.
        text = _text_for_document_id(identifier)

    if not text.strip():
        return {
            "fields": [],
            "flags": [
                {
                    "kind": "document_unreadable",
                    "detail": "No statement text was found for this document.",
                    "severity": "high",
                }
            ],
            "document_id": identifier,
            "method": MODE_DETERMINISTIC_EXTRACT,
        }

    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    fields: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []

    heading = _document_heading(document, text)

    # --- Scalar fields -----------------------------------------------------
    registration, registration_line, registration_explicit = _extract_registration(lines)
    if registration:
        fields.append(
            _field(
                "registration",
                registration,
                _confidence(0.97 if registration_explicit else 0.68, heading),
                registration_line,
            )
        )

    title, title_line = _extract_labeled(lines, LABELS["title"])
    if title:
        fields.append(_field("account_title", title, 0.9, title_line))

    account_number, account_line = _extract_account_number(lines)
    if account_number:
        confidence = 0.99 if re.search(r"[*xX•]", account_number) else 0.93
        fields.append(_field("account_number", account_number, confidence, account_line))

    period, period_line = _extract_period(lines)
    if period:
        parsed = _split_period(period)
        confidence = 0.96 if parsed else 0.74
        fields.append(_field("statement_period", period, confidence, period_line))

    address, address_line = _extract_address(lines)
    if address:
        confidence = 0.92 if len(address.split(",")) >= 3 else 0.81
        fields.append(_field("address", address, confidence, address_line))

    beneficiary, beneficiary_line = _extract_beneficiary(lines)
    if beneficiary:
        fields.append(_field("beneficiary", beneficiary, 0.93, beneficiary_line))

    ein, ein_line = _extract_ein(lines)
    if ein:
        fields.append(_field("trust_ein", ein, 0.98, ein_line))

    # --- Account summary block and structured summary documents -----------
    known_names = {field["name"] for field in fields}
    for extra in _extract_summary_fields(lines, heading):
        if extra["name"] not in known_names:
            fields.append(extra)
            known_names.add(extra["name"])

    # The account record, when the masked number matches an account in the book,
    # lets the rules layer catch a registration that changed between the statement
    # and the CRM. No match means no comparison, never an invented one.
    book_registration = _book_registration_for(document, account_number)

    # --- Position lines ----------------------------------------------------
    positions = _extract_positions(lines)
    for index, position in enumerate(positions):
        base_confidence = max(0.7, 0.95 - (index * 0.03))
        prefix = f"position[{position['symbol']}]"
        fields.append(
            _field(f"{prefix}.symbol", position["symbol"], base_confidence, position["source_span"])
        )
        if position.get("description"):
            fields.append(
                _field(
                    f"{prefix}.description",
                    position["description"],
                    max(0.7, base_confidence - 0.04),
                    position["source_span"],
                )
            )
        for key, offset in (
            ("quantity", 0.0),
            ("price", 0.02),
            ("value", -0.01),
            ("cost_basis", 0.06),
        ):
            value = position.get(key)
            if value is None:
                continue
            fields.append(
                _field(
                    f"{prefix}.{key}",
                    value,
                    max(0.7, base_confidence - offset),
                    position["source_span"],
                )
            )

    # A fund position named with a share class is worth surfacing even when the
    # transferable flag is cut off in the detail view.
    for extra in _fund_fields(positions):
        if extra["name"] not in known_names:
            fields.append(extra)
            known_names.add(extra["name"])

    # --- Rules layer: flags the text does or does not show -----------------
    flags.extend(_flag_beneficiary(beneficiary, text, lines, heading))
    flags.extend(_flag_stale_address(address, text, lines))
    flags.extend(
        _flag_registration_mismatch(
            title,
            registration,
            title_line,
            registration_line,
            book_registration,
        )
    )
    flags.extend(_flag_non_transferable(positions, text))
    flags.extend(_flag_annuity_surrender(text))
    flags.extend(_flag_trust_ein(heading, registration, title, text, ein))

    return {
        "fields": fields,
        "flags": _dedupe_flags(flags),
        "document_id": identifier,
        "method": MODE_DETERMINISTIC_EXTRACT,
    }


# ---------------------------------------------------------------------------
# Scalar extraction
# ---------------------------------------------------------------------------


def _field(name: str, value: Any, confidence: float, source_span: str) -> dict[str, Any]:
    return {
        "name": name,
        "value": _clean_value(value),
        "confidence": round(float(confidence), 2),
        "source_span": (source_span or "").strip(),
    }


def _confidence(base: float, heading: str) -> float:
    """Slightly lower confidence for summary documents with no account heading."""

    if heading in {"trust summary", "annuity summary", "crm export", "unknown"}:
        return max(0.55, base - 0.08)
    return base


def _clean_value(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip().strip(",")
    return value


def _label_pattern(label: str) -> re.Pattern[str]:
    # A label may be followed by a colon, a dash, or two spaces. Avoid \b on both
    # ends because some labels end in "#" or ".".
    escaped = re.escape(label)
    return re.compile(rf"^\s*{escaped}\s*(?::|-|\u2013)?\s*(.*)$", re.IGNORECASE)


def _find_labeled(
    lines: Iterable[str], labels: Iterable[str]
) -> Optional[tuple[str, int, str]]:
    """Return (value, line_index, whole_line) for the first matching label.

    An empty inline value returns an empty string; callers decide whether to read the
    following block (address, beneficiary).
    """

    patterns = [(label, _label_pattern(label)) for label in labels]
    for index, line in enumerate(lines):
        for label, pattern in patterns:
            match = pattern.match(line)
            if not match:
                continue
            # Guard against "account" matching "Account Number: ..." a second time.
            remainder = match.group(1).strip()
            if label.lower() in {"account", "period", "title", "address"} and remainder:
                next_word = remainder.split()[0].strip(":").lower()
                if next_word in {
                    "number",
                    "no",
                    "registration",
                    "title",
                    "period",
                    "holder",
                    "of",
                    "on",
                }:
                    continue
            return remainder, index, line.strip()
    return None


def _extract_labeled(
    lines: list[str], labels: Iterable[str]
) -> tuple[str, str]:
    found = _find_labeled(lines, labels)
    if not found:
        return "", ""
    value, _, whole = found
    return value.strip(), whole


def _extract_registration(lines: list[str]) -> tuple[str, str, bool]:
    found = _find_labeled(lines, LABELS["registration"])
    if found and found[0]:
        value, _, whole = found
        return _clean_registration(value), whole, True
    # Fall back to the account title, but mark it as inferred for confidence.
    title, title_line = _extract_labeled(lines, LABELS["title"])
    if title:
        return _clean_registration(title), title_line, False
    return "", "", False


def _clean_registration(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" :-")
    # Drop a trailing account number that sometimes shares the line.
    value = re.sub(r"(?i)\s+(?:account|acct)\s*(?:#|no\.?|number)?\s*[:#]?\s*[*xX\d-]+$", "", value)
    return value.strip()


def _extract_account_number(lines: list[str]) -> tuple[str, str]:
    for label in LABELS["account_number"]:
        pattern = re.compile(
            rf"^\s*{re.escape(label)}\s*(?::|-|\u2013)?\s*([*xX•#\d][*xX•#\d\s*-]{{2,}})",
            re.IGNORECASE,
        )
        for line in lines:
            match = pattern.match(line)
            if not match:
                continue
            raw = match.group(1).strip()
            raw = re.sub(r"\s+", "", raw)
            if re.search(r"\d", raw):
                return raw, line.strip()
    return "", ""


def _extract_period(lines: list[str]) -> tuple[str, str]:
    found = _find_labeled(lines, LABELS["period"])
    if found and found[0]:
        value, _, whole = found
        value = re.sub(r"\s+", " ", value).strip()
        return _normalise_period(value), whole
    match = re.search(
        r"(?i)(?:for\s+the\s+period|period)\s+(?:of\s+)?(.+?)\s+(?:through|thru|to)\s+(.+?)(?:\.|$)",
        "\n".join(lines),
    )
    if match:
        return _normalise_period(f"{match.group(1)} to {match.group(2)}"), match.group(0).strip()
    return "", ""


def _normalise_period(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip().strip(".")
    value = re.sub(r"\s+(?:-|\u2013|\u2014|through|thru)\s+", " to ", value)
    return re.sub(r"\s+", " ", value).strip()


def _split_period(value: str) -> Optional[tuple[str, str]]:
    if " to " not in value:
        return None
    start, end = value.split(" to ", 1)
    if parse_date(start.strip()) and parse_date(end.strip()):
        return start.strip(), end.strip()
    return None


def _extract_address(lines: list[str]) -> tuple[str, str]:
    found = _find_labeled(lines, LABELS["address"])
    if not found:
        return "", ""
    inline, index, _ = found
    if inline:
        value = re.sub(r"\s+", " ", inline).strip()
        # "(last updated 2019-04-12)" is metadata, not part of the mailing address.
        value = re.sub(
            r"\s*\((?:last\s+updated|updated|last\s+verified|verified)[^)]*\)\s*$",
            "",
            value,
            flags=re.IGNORECASE,
        ).strip()
        return value, lines[index].strip()

    block: list[str] = []
    for next_line in lines[index + 1 : index + 6]:
        candidate = next_line.strip()
        if not candidate:
            if block:
                break
            continue
        if _looks_like_label(candidate):
            break
        if _looks_like_address(candidate):
            block.append(candidate)
        elif not block and _looks_like_person(candidate):
            continue  # the client name usually sits above the street line
        elif block:
            break
    if not block:
        return "", ""
    joined = ", ".join(part.strip(",") for part in block[:3])
    return joined, "\n".join(lines[index : index + 1 + len(block)]).strip()


def _looks_like_address(line: str) -> bool:
    if re.match(r"(?i)^\s*(p\.?\s?o\.?\s?box)\b", line):
        return True
    if re.search(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b", line):
        return True
    if re.match(r"^\s*\d+[A-Za-z]?\s+\S+", line) and not _looks_like_label(line):
        return True
    if "," in line and re.search(r"(?i)\b(street|st\.?|avenue|ave\.?|road|rd\.?|lane|ln\.?|drive|dr\.?|court|ct\.?|way|boulevard|blvd)\b", line):
        return True
    return False


def _looks_like_person(line: str) -> bool:
    if re.search(r"\d", line) or "," in line:
        return False
    words = line.split()
    return 2 <= len(words) <= 5 and all(word[:1].isupper() or word in {"and", "&"} for word in words)


def _looks_like_label(line: str) -> bool:
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9 /#.&-]{0,40}:", line))


def _extract_beneficiary(lines: list[str]) -> tuple[str, str]:
    found = _find_labeled(lines, LABELS["beneficiary"])
    if not found:
        return "", ""
    inline, index, _ = found
    if inline:
        return re.sub(r"\s+", " ", inline).strip(), lines[index].strip()

    block: list[str] = []
    for next_line in lines[index + 1 : index + 4]:
        candidate = next_line.strip()
        if not candidate:
            break
        if _looks_like_label(candidate):
            break
        block.append(candidate)
    if not block:
        return "", ""
    return ", ".join(block), "\n".join([lines[index], *block]).strip()


def _extract_ein(lines: list[str]) -> tuple[str, str]:
    found = _find_labeled(lines, LABELS["ein"])
    if found and found[0]:
        return found[0].strip(), found[2]
    for line in lines:
        match = re.search(r"\b\d{2}-\d{7}\b", line)
        if match:
            return match.group(0), line.strip()
    return "", ""



def _extract_summary_fields(
    lines: list[str], heading: str
) -> list[dict[str, Any]]:
    """Pull the account summary block, or a structured summary document's labels.

    Statements have a small fixed summary block. Trust, annuity, and CRM summaries
    are written as one label per line, so they are read generically. Both paths use
    only what the line says.
    """

    fields: list[dict[str, Any]] = []
    if heading in SUMMARY_DOCUMENT_KINDS:
        for index, line in enumerate(lines):
            match = re.match(
                r"^([A-Za-z0-9][A-Za-z0-9 /&'().-]{1,45}):\s*(.+)$", line
            )
            if not match:
                continue
            label = match.group(1).strip()
            value = match.group(2).strip()
            if label.lower() in SUMMARY_SKIP_LABELS or not value:
                continue
            name = _field_name(label)
            if not name:
                continue
            confidence = max(0.72, 0.94 - (index * 0.015))
            fields.append(
                _field(
                    name,
                    _normalise_summary_value(name, value),
                    round(confidence, 2),
                    line.strip(),
                )
            )
        return fields

    for name, labels, value_type in SUMMARY_FIELD_SPECS:
        found = _find_labeled(lines, labels)
        if not found or not found[0]:
            continue
        raw, _, whole = found
        value: Any = raw.strip()
        if value_type in {"number", "integer"}:
            number = to_number(value)
            if number is None:
                continue
            value = int(number) if value_type == "integer" else number
        fields.append(_field(name, value, 0.98 if value_type != "text" else 0.96, whole))
    return fields


def _field_name(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(label).strip().lower()).strip("_")


def _normalise_summary_value(name: str, value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if name == "surrender_charge":
        # The charge is a rate plus an end date; "of contract value" is the base,
        # not part of the value a reviewer needs in the field.
        value = re.sub(r"\s+of\s+contract\s+value", "", value, flags=re.IGNORECASE)
    return value


def _fund_fields(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for position in positions:
        description = str(position.get("description") or "")
        if "fund" not in description.lower():
            continue
        match = re.search(r"\bClass\s+([A-Z])\b", description)
        if not match:
            continue
        return [
            _field("fund_holding", description, 0.9, position["source_span"]),
            _field("share_class", f"Class {match.group(1)}", 0.88, position["source_span"]),
        ]
    return []


def _book_registration_for(document: dict[str, Any], account_number: str) -> str:
    """Registration from the book account with the same masked number, if any."""

    explicit = document.get("expected_registration") or document.get("book_registration")
    if explicit:
        return str(explicit)
    target = _digits_tail(account_number)
    if not target:
        return ""
    try:
        book = load_book()
    except Exception:
        return ""
    rows = book.get("accounts") if isinstance(book, dict) else None
    if not isinstance(rows, list):
        return ""
    for account in rows:
        if not isinstance(account, dict):
            continue
        if _digits_tail(str(account.get("masked_number") or "")) == target:
            return str(account.get("registration") or "")
    return ""


def _digits_tail(value: str) -> str:
    digits = re.findall(r"\d", str(value or ""))
    return "".join(digits[-4:]) if len(digits) >= 3 else ""


# ---------------------------------------------------------------------------
# Position extraction
# ---------------------------------------------------------------------------


def _extract_positions(lines: list[str]) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float]] = set()
    in_table = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if _is_position_header(lower):
            in_table = True
            continue
        if lower.startswith(("total", "subtotal", "totals")):
            in_table = False
            continue
        parsed = _parse_position_line(stripped)
        if not parsed:
            continue
        # A parsed line already requires a ticker-shaped symbol and a numeric tail;
        # the table flag only affects how the description was reconstructed.
        key = (parsed["symbol"], float(parsed.get("value") or 0), float(parsed.get("quantity") or 0))
        if key in seen:
            continue
        seen.add(key)
        positions.append(parsed)
    return positions


def _is_position_header(lower: str) -> bool:
    if not any(word in lower for word in POSITION_HEADER_WORDS):
        return False
    if not any(word in lower for word in POSITION_QUANTITY_WORDS):
        return False
    return any(word in lower for word in POSITION_VALUE_WORDS) or "price" in lower


def _parse_position_line(line: str) -> Optional[dict[str, Any]]:
    symbol = line.split()[0].strip()
    if symbol.upper().strip(".,") in SKIP_POSITION_SYMBOLS:
        return None
    if not re.fullmatch(r"[A-Z][A-Z0-9]{0,6}(?:[./][A-Z0-9]{1,3})?", symbol):
        return None

    # Preferred split: columns separated by two or more spaces or a tab.
    parts = [part.strip() for part in re.split(r"\s{2,}|\t+", line) if part.strip()]
    if len(parts) >= 5:
        tail = parts[-4:] if len(parts) >= 6 else parts[-3:]
        numbers = [_parse_number(item) for item in tail]
        if len(numbers) == 4 and all(number is not None for number in numbers):
            quantity, price, value, cost = numbers
            description = " ".join(parts[1:-4]).strip()
            if description:
                return {
                    "symbol": symbol,
                    "description": description,
                    "quantity": quantity,
                    "price": price,
                    "value": value,
                    "cost_basis": cost,
                    "source_span": line,
                    "looks_tabular": True,
                }
        if len(numbers) == 3 and all(number is not None for number in numbers):
            quantity, price, value = numbers
            description = " ".join(parts[1:-3]).strip()
            if description:
                return {
                    "symbol": symbol,
                    "description": description,
                    "quantity": quantity,
                    "price": price,
                    "value": value,
                    "cost_basis": None,
                    "source_span": line,
                    "looks_tabular": True,
                }

    # Fallback: one-space columns with a numeric tail.
    match = re.match(
        rf"^(?P<symbol>[A-Z][A-Z0-9]{{0,6}}(?:[./][A-Z0-9]{{1,3}})?)\s+"
        rf"(?P<desc>.+?)\s+"
        rf"(?P<qty>{NUM_TOKEN})\s+"
        rf"(?P<price>{NUM_TOKEN})\s+"
        rf"(?P<value>{NUM_TOKEN})"
        rf"(?:\s+(?P<cost>{NUM_TOKEN}|N/?A|--))?$",
        line,
    )
    if not match:
        return None
    description = (match.group("desc") or "").strip()
    if not description or not any(char.isalpha() for char in description):
        return None
    return {
        "symbol": match.group("symbol"),
        "description": description,
        "quantity": _parse_number(match.group("qty")),
        "price": _parse_number(match.group("price")),
        "value": _parse_number(match.group("value")),
        "cost_basis": _parse_number(match.group("cost")) if match.group("cost") else None,
        "source_span": line,
        "looks_tabular": False,
    }


def _parse_number(token: Any) -> Optional[float]:
    if token is None:
        return None
    text = str(token).strip()
    if text.upper() in {"N/A", "NA", "--", "-", ""}:
        return None
    return to_number(text)


# ---------------------------------------------------------------------------
# Rules layer
# ---------------------------------------------------------------------------


def _flag_beneficiary(
    beneficiary: str, text: str, lines: list[str], heading: str = "statement"
) -> list[dict[str, Any]]:
    lowered = text.lower()
    if heading not in {"statement", "unknown"} and "account registration" not in lowered:
        # A trust or annuity summary is not an account statement. Its beneficiary
        # language is about the documents, not a missing account designation.
        return []
    if beneficiary:
        if any(marker in beneficiary.lower() for marker in BENEFICIARY_MISSING_MARKERS):
            return [
                {
                    "kind": "beneficiary_missing",
                    "detail": f"The beneficiary line reads \"{beneficiary}\", so no designation is on file.",
                    "severity": "high",
                }
            ]
        return []
    has_any_label = any(
        _find_labeled(lines, (label,)) for label in LABELS["beneficiary"]
    )
    if has_any_label:
        detail = "A beneficiary label appears but no designation follows it."
    elif "beneficiary" in lowered:
        detail = "The text mentions beneficiaries but shows no designation for this account."
    else:
        detail = "No beneficiary designation appears anywhere in the statement text."
    return [{"kind": "beneficiary_missing", "detail": detail, "severity": "high"}]


def _flag_stale_address(
    address: str, text: str, lines: list[str]
) -> list[dict[str, Any]]:
    lowered = text.lower()
    for marker in STALE_ADDRESS_MARKERS:
        if marker in lowered:
            evidence = next(
                (line.strip() for line in lines if marker in line.lower()), ""
            )
            detail = f'The document carries the marker "{marker}".'
            if evidence and evidence.lower() != marker:
                detail = f'The document carries the marker "{marker}" on the line: {evidence}.'
            return [{"kind": "stale_address", "detail": detail, "severity": "medium"}]

    # A date on the address itself is evidence too. Twenty-four months is the
    # re-verification window in this synthetic policy (CP-07).
    for match in re.finditer(
        r"last\s+(?:updated|confirmed)(?:\s+on)?\s+(\d{4}-\d{2}-\d{2})",
        text,
        flags=re.IGNORECASE,
    ):
        parsed = parse_date(match.group(1))
        if parsed and (date.today() - parsed).days > 730:
            return [
                {
                    "kind": "stale_address",
                    "detail": (
                        f"The address was last updated {match.group(1)}, more than "
                        "24 months ago, so it needs re-verification before documents are sent."
                    ),
                    "severity": "medium",
                }
            ]
    return []


def _flag_registration_mismatch(
    title: str,
    registration: str,
    title_line: str,
    registration_line: str,
    book_registration: str = "",
) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []

    if title and registration and title.strip().lower() != registration.strip().lower():
        title_categories = _registration_categories(title)
        registration_categories = _registration_categories(registration)
        if title_categories and registration_categories and title_categories.isdisjoint(
            registration_categories
        ):
            flags.append(
                {
                    "kind": "registration_mismatch",
                    "detail": (
                        "The account title and the registration line disagree: "
                        f'title "{title}" versus registration "{registration}".' 
                    ),
                    "severity": "high",
                }
            )
        elif (
            not title_categories
            and registration_categories & {"trust", "entity", "utma"}
            and _looks_like_person(title)
        ):
            # A bare personal name under a trust or entity registration is a mismatch
            # even though neither line carries a category word we can compare.
            flags.append(
                {
                    "kind": "registration_mismatch",
                    "detail": (
                        f'The title "{title}" is a personal name while the registration '
                        f'reads "{registration}".' 
                    ),
                    "severity": "high",
                }
            )

    if book_registration and registration:
        if book_registration.strip().lower() != registration.strip().lower():
            statement_categories = _registration_categories(registration)
            record_categories = _registration_categories(book_registration)
            disagree = bool(statement_categories and record_categories) and (
                statement_categories.isdisjoint(record_categories)
            )
            if not statement_categories and record_categories & {"trust", "entity", "utma"}:
                disagree = True
            if disagree:
                flags.append(
                    {
                        "kind": "registration_mismatch",
                        "detail": (
                            f'The statement registration reads "{registration}" while the '
                            f'account record reads "{book_registration}".' 
                        ),
                        "severity": "high",
                    }
                )
    return flags


def _registration_categories(text: str) -> set[str]:
    lowered = text.lower()
    found: set[str] = set()
    for category, words in REGISTRATION_CATEGORIES.items():
        for word in words:
            if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
                found.add(category)
                break
    return found


def _flag_non_transferable(
    positions: list[dict[str, Any]], text: str
) -> list[dict[str, Any]]:
    lowered = text.lower()
    evidence: list[str] = []
    by_symbol = {str(p.get("symbol")): str(p.get("description") or "") for p in positions}
    for position in positions:
        description = str(position.get("description") or "")
        symbol = position.get("symbol")
        description_lower = description.lower()
        if any(marker in description_lower for marker in NON_TRANSFERABLE_MARKERS):
            evidence.append(f"{symbol} ({description})")
        elif "class c" in description_lower and any(
            word in description_lower for word in CLASS_C_CONTEXT
        ):
            evidence.append(f"{symbol} ({description})")
    for line in text.splitlines():
        line_lower = line.lower()
        cleaned = line.strip()
        if not cleaned:
            continue
        # A holding line collapses to "SYMBOL (description)" so the flag detail is
        # readable instead of carrying the statement's column padding.
        first_token = cleaned.split()[0].strip(".,") if cleaned.split() else ""
        if first_token in by_symbol:
            if any(marker in line_lower for marker in NON_TRANSFERABLE_MARKERS) or (
                "class c" in line_lower and any(word in line_lower for word in CLASS_C_CONTEXT)
            ):
                compact = f"{first_token} ({by_symbol[first_token]})"
                if compact not in evidence:
                    evidence.append(compact)
            continue
        if any(marker in line_lower for marker in NON_TRANSFERABLE_MARKERS):
            if cleaned not in evidence:
                evidence.append(cleaned)
        elif "class c" in line_lower and any(word in line_lower for word in CLASS_C_CONTEXT):
            if cleaned not in evidence:
                evidence.append(cleaned)
    if not evidence:
        return []
    unique_evidence = unique(evidence)[:3]
    return [
        {
            "kind": "non_transferable_share_class",
            "detail": (
                "The statement shows a share class or asset the receiving custodian may "
                "not accept in kind: "
                + "; ".join(unique_evidence)
                + "."
            ),
            "severity": "high",
        }
    ]


def _flag_annuity_surrender(text: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    hits = [marker for marker in ANNUITY_SURRENDER_MARKERS if marker in lowered]
    if not hits:
        return []
    line = next(
        (item.strip() for item in text.splitlines() if any(hit in item.lower() for hit in hits)),
        "",
    )
    detail = f"The document mentions {', '.join(unique(hits))}."
    if line:
        detail = f'The document states: "{line}".'
    return [
        {
            "kind": "annuity_surrender_charge",
            "detail": detail,
            "severity": "high",
        }
    ]


def _flag_trust_ein(
    heading: str,
    registration: str,
    title: str,
    text: str,
    ein: str,
) -> list[dict[str, Any]]:
    combined = " ".join([heading, registration, title]).lower()
    is_trust = (
        any(word in combined for word in TRUST_WORDS)
        or "trust" in _registration_categories(registration)
        or "trust" in _registration_categories(title)
    )
    if not is_trust:
        return []
    if ein and ein.strip().lower() not in EIN_PLACEHOLDERS:
        return []
    if ein:
        detail = (
            f'The trust EIN field reads "{ein}", so the account cannot be titled yet. '
            "The receiving custodian needs an issued EIN."
        )
    else:
        detail = (
            "The account is titled as a trust but no EIN or tax ID appears in the "
            "document. The receiving custodian needs one before opening the account."
        )
    return [{"kind": "trust_ein_absent", "detail": detail, "severity": "high"}]


def _dedupe_flags(flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for flag in flags:
        kind = str(flag.get("kind") or "").strip()
        if not kind:
            continue
        if kind not in merged:
            merged[kind] = {
                "kind": kind,
                "detail": str(flag.get("detail") or "").strip(),
                "severity": str(flag.get("severity") or "medium"),
            }
            order.append(kind)
        else:
            existing = merged[kind]
            incoming = str(flag.get("detail") or "").strip()
            if incoming and incoming not in existing["detail"]:
                existing["detail"] = f"{existing['detail']} {incoming}".strip()
            if _severity_rank(flag.get("severity")) > _severity_rank(existing["severity"]):
                existing["severity"] = str(flag.get("severity"))
    return [merged[kind] for kind in order]


def _severity_rank(value: Any) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(str(value).lower(), 0)


# ---------------------------------------------------------------------------
# Helpers: heading, cached model run, file fallback
# ---------------------------------------------------------------------------


def _document_heading(document: dict[str, Any], text: str) -> str:
    kind = str(document.get("kind") or document.get("type") or "").lower()
    if kind:
        return kind.replace("_", " ")
    first = text.strip().splitlines()[0].lower() if text.strip() else ""
    if "annuity" in first:
        return "annuity summary"
    if "trust" in first:
        return "trust summary"
    if "statement" in first:
        return "statement"
    return "unknown"


def _recorded_model_extraction(identifier: str) -> Optional[dict[str, Any]]:
    """Return a cached model extraction, but only when it really was a model run."""

    entry = load_cached(f"extract-{identifier}")
    if not entry or not is_recorded_model_run(entry.get("mode")):
        return None
    raw = entry.get("text")
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        payload = json.loads(cleaned)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    fields = payload.get("fields")
    flags = payload.get("flags")
    if not isinstance(fields, list) or not isinstance(flags, list):
        return None
    return {"fields": fields, "flags": flags}


def _text_for_document_id(identifier: str) -> str:
    candidate = data_dir() / "documents" / f"{identifier}.json"
    payload = load_json(candidate, default=None)
    if isinstance(payload, dict):
        return document_text(payload)
    return ""


def _read_relative_text(path: str) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError:
        return ""
