"""Extraction evaluation against hand-written labels.

The comparison is deliberately simple and inspectable:

1. Normalise both sides: lowercase, trim, collapse whitespace, strip currency symbols
   and commas. Dates and date ranges compare as dates. Numeric strings compare as
   numbers. Masked account numbers compare on their digit tail.
2. A label beginning with ``FLAG:`` is a category match against the extracted flags,
   not a string match against a field value.
3. Field names get a small alias table so "masked_account_number" and
   "account_number_masked" are the same field.

The labels are read only here. ``pipeline.extract`` never reads them.

``run_eval(provider=None)`` uses the deterministic extractor. When a provider is
passed, each document is sent through ``complete`` and parsed as JSON; any failure
falls back to the deterministic result and is noted in the report.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from app.llm import (
    MODE_DETERMINISTIC_EXTRACT,
    is_recorded_model_run,
    utc_now_iso,
)

from . import (
    REPO_ROOT,
    data_dir,
    doc_id,
    document_text,
    load_book,
    load_documents,
    load_json,
    parse_date,
)

LABEL_META_KEYS = {
    "id",
    "document_id",
    "doc_id",
    "filename",
    "file",
    "kind",
    "type",
    "title",
    "notes",
    "source_span",
    "page",
}

FIELD_ALIASES = {
    "masked_account_number": "account_number",
    "masked_number": "account_number",
    "account_number_masked": "account_number",
    "account_no": "account_number",
    "acct_number": "account_number",
    "acct_no": "account_number",
    "accountnumber": "account_number",
    "period": "statement_period",
    "statementperiod": "statement_period",
    "address_on_file": "address",
    "address_of_record": "address",
    "mailing_address": "address",
    "addressonfile": "address",
    "beneficiary_designation": "beneficiary",
    "primary_beneficiary": "beneficiary",
    "registration_type": "registration",
    "account_registration": "registration",
    "positions_held": "positions_count",
    "position_count": "positions_count",
    "account_to_be_opened": "account_to_open",

    "tax_id": "trust_ein",
    "ein": "trust_ein",
    "trustein": "trust_ein",
    "position_count": "positions_count",
    "number_of_positions": "positions_count",
}

FLAG_ALIASES = {
    "missing_beneficiary": "beneficiary_missing",
    "beneficiary_not_designated": "beneficiary_missing",
    "no_beneficiary": "beneficiary_missing",
    "stale_address_marker": "stale_address",
    "address_stale": "stale_address",
    "registration_title_mismatch": "registration_mismatch",
    "title_registration_mismatch": "registration_mismatch",
    "non_transferable": "non_transferable_share_class",
    "non_transferable_asset": "non_transferable_share_class",
    "proprietary_fund": "non_transferable_share_class",
    "surrender_charge": "annuity_surrender_charge",
    "annuity_surrender": "annuity_surrender_charge",
    "trust_ein_missing": "trust_ein_absent",
    "missing_trust_ein": "trust_ein_absent",
}

SYSTEM_PROMPT = (
    "You extract fields from synthetic financial statement text for a product demo. "
    "Return only a JSON object with two arrays: fields (name, value, confidence, "
    "source_span) and flags (kind, detail, severity). Do not add commentary."
)

MISSING = object()


def run_eval(
    provider: Any = None,
    *,
    data_dir_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Compare extraction output with ``data/eval/labeled.json`` and write a report.

    ``provider`` is optional. Without it the run is the deterministic extractor, which
    is the demo default. A provider is used per document and falls back to the
    deterministic result when the completion cannot be parsed.
    """

    base = Path(data_dir_path) if data_dir_path is not None else data_dir()
    labeled_path = base / "eval" / "labeled.json"
    labels = _load_labels(labeled_path)
    documents = _load_all_documents(base)

    per_field: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    notes: list[str] = []
    evaluated_documents = 0
    provider_used = 0
    provider_fallbacks = 0

    notes.append(
        "Labels are synthetic and describe the same documents this extractor reads. "
        "The accuracy is an in-sample check, not a generalization estimate."
    )
    if not labels:
        notes.append(f"No labels found at {labeled_path}.")

    for label_doc in labels:
        identifier = _label_id(label_doc)
        document = _match_document(documents, identifier)
        expected = _expected_pairs(label_doc)

        if document is None:
            failures.append(
                {
                    "document_id": identifier,
                    "field": "__document__",
                    "expected": "document text for the labeled file",
                    "actual": "not found",
                }
            )
            continue
        if not document_text(document).strip():
            failures.append(
                {
                    "document_id": identifier,
                    "field": "__document__",
                    "expected": "statement text",
                    "actual": "document has no text",
                }
            )
            continue

        extraction, method, used_provider, fell_back = _extract(document, provider)
        if used_provider:
            provider_used += 1
        if fell_back:
            provider_fallbacks += 1
        evaluated_documents += 1

        actual_fields = _actual_field_index(extraction)
        actual_flags = _actual_flag_index(extraction)
        actual_positions = _actual_positions_count(extraction)

        for field_name, expected_value in expected:
            if _is_flag_expectation(field_name, expected_value):
                canonical = _canonical_flag(
                    _flag_category(field_name, expected_value)
                )
                match = canonical in actual_flags
                actual_value = "present" if match else "absent"
                per_field.append(
                    {
                        "document_id": identifier,
                        "field": f"FLAG: {canonical}",
                        "expected": "present",
                        "actual": actual_value,
                        "match": match,
                    }
                )
                if not match:
                    failures.append(per_field[-1])
                continue

            canonical_name = _canonical_name(field_name)
            if canonical_name == "positions_count":
                actual_value = actual_positions
            else:
                actual_value = actual_fields.get(canonical_name, MISSING)

            match = (
                actual_value is not MISSING
                and _values_match(expected_value, actual_value, canonical_name)
            )
            row = {
                "document_id": identifier,
                "field": canonical_name,
                "expected": _display(expected_value),
                "actual": "" if actual_value is MISSING else _display(actual_value),
                "match": bool(match),
            }
            per_field.append(row)
            if not match:
                failures.append(row)

        # Explicit flags arrays in the labels become category expectations.
        for kind in _label_flags(label_doc):
            canonical = _canonical_flag(kind)
            match = canonical in actual_flags
            row = {
                "document_id": identifier,
                "field": f"FLAG: {canonical}",
                "expected": "present",
                "actual": "present" if match else "absent",
                "match": match,
            }
            per_field.append(row)
            if not match:
                failures.append(row)

    field_count = len(per_field)
    correct_count = sum(1 for row in per_field if row["match"])
    accuracy = round(correct_count / field_count, 4) if field_count else 0.0

    method = MODE_DETERMINISTIC_EXTRACT
    if provider is not None and provider_used and not provider_fallbacks:
        method = f"provider: {getattr(provider, 'name', 'unknown')}"

    try:
        labels_display = str(labeled_path.relative_to(REPO_ROOT))
    except ValueError:
        labels_display = labeled_path.name

    report = {
        "run_at": utc_now_iso(),
        "field_count": field_count,
        "correct_count": correct_count,
        "accuracy": accuracy,
        "per_field": per_field,
        "failures": failures,
        "method": method,
        "documents_evaluated": evaluated_documents,
        "labels_path": labels_display,
        "notes": notes,
    }
    if provider is not None:
        report["provider_documents"] = provider_used
        report["provider_fallbacks"] = provider_fallbacks
    if not labels:
        report["notes"].append(
            "Run scripts/generate_book.py (or the seed step) before scripts/run_eval.py."
        )

    _write_report(base, report)
    return report


# ---------------------------------------------------------------------------
# Extraction through an optional provider
# ---------------------------------------------------------------------------


def _extract(
    document: dict[str, Any], provider: Any
) -> tuple[dict[str, Any], str, bool, bool]:
    from .extract import extract_document

    deterministic = extract_document(document)
    if provider is None:
        return deterministic, deterministic["method"], False, False

    identifier = doc_id(document)
    target = provider.for_key(f"extract-{identifier}") if hasattr(provider, "for_key") else provider
    prompt = _extraction_prompt(document)
    try:
        raw = target.complete(SYSTEM_PROMPT, prompt)
        parsed = _parse_model_payload(raw)
    except Exception as exc:  # cache miss, configuration error, throttling
        deterministic["provider_error"] = str(exc)[:200]
        return deterministic, deterministic["method"], False, True
    if parsed is None:
        return deterministic, deterministic["method"], False, True
    # A cached file written by the deterministic extractor is not a model run, even
    # when it is reached through a provider object. Provenance stays honest.
    cached_mode = getattr(target, "last_mode", None) or getattr(provider, "last_mode", None)
    if cached_mode and not is_recorded_model_run(cached_mode):
        return deterministic, deterministic["method"], False, False
    return parsed, f"provider: {getattr(provider, 'name', 'unknown')}", True, False


def _extraction_prompt(document: dict[str, Any]) -> str:
    identifier = doc_id(document)
    text = document_text(document)
    return (
        f"Document id: {identifier}\n"
        "Extract registration, account_number, statement_period, address, beneficiary, "
        "and one set of fields per position (symbol, description, quantity, price, "
        "value, cost_basis). Use the field names exactly as written.\n\n"
        f"--- document text ---\n{text}\n--- end ---"
    )


def _parse_model_payload(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    fields = payload.get("fields")
    flags = payload.get("flags", [])
    if not isinstance(fields, list) or not isinstance(flags, list):
        return None
    return {
        "fields": fields,
        "flags": flags,
        "document_id": payload.get("document_id", ""),
        "method": "live model, recorded",
    }


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_labels(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path, default=None)
    if payload is None:
        return []

    if isinstance(payload, list):
        return _coalesce_rows([item for item in payload if isinstance(item, dict)])

    if not isinstance(payload, dict):
        return []

    for key in ("documents", "labels", "labeled", "data", "examples"):
        value = payload.get(key)
        if isinstance(value, list):
            return _coalesce_rows([item for item in value if isinstance(item, dict)])
        if isinstance(value, dict):
            return [_entry_from_mapping(item_id, item) for item_id, item in value.items()]

    if _looks_like_label_entry(payload):
        return [payload]

    # Last shape: a mapping of document id to fields.
    entries = []
    for item_id, value in payload.items():
        if isinstance(value, dict):
            entries.append(_entry_from_mapping(item_id, value))
    return entries


def _coalesce_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group label rows into one entry per document.

    The seed file stores one row per expected field:
    ``{"document_id": "D-1", "field_name": "Account number", "expected": "..."}``.
    Document-level entries (with a fields object or a flags array) pass through
    unchanged. Both shapes are accepted so the eval is not brittle.
    """

    if not items:
        return []
    row_style = all(
        ("field_name" in item or "field" in item)
        and ("expected" in item or "value" in item)
        and "document_id" in item
        for item in items
    )
    if not row_style:
        return items

    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in items:
        identifier = str(item.get("document_id") or "")
        if identifier not in grouped:
            grouped[identifier] = {"id": identifier, "fields": {}}
            order.append(identifier)
        name = item.get("field_name") or item.get("field") or item.get("name")
        value = item.get("expected", item.get("value"))
        if name is not None:
            grouped[identifier]["fields"][str(name)] = value
    return [grouped[identifier] for identifier in order]


def _entry_from_mapping(item_id: str, value: dict[str, Any]) -> dict[str, Any]:
    if _looks_like_label_entry(value):
        entry = dict(value)
        entry.setdefault("id", item_id)
        return entry
    return {"id": item_id, "fields": value}


def _looks_like_label_entry(payload: dict[str, Any]) -> bool:
    return any(
        key in payload
        for key in ("fields", "expected", "expected_fields", "labels", "flags")
    )


def _load_all_documents(base: Path) -> list[dict[str, Any]]:
    documents = load_documents(base / "documents")
    book = load_book(base / "book.json")
    known = {_normalise_id(doc_id(item)) for item in documents}
    for item in book.get("documents", []) if isinstance(book.get("documents"), list) else []:
        if not isinstance(item, dict):
            continue
        identifier = _normalise_id(doc_id(item))
        if identifier not in known and document_text(item).strip():
            documents.append(item)
            known.add(identifier)
    return documents


def _label_id(label_doc: dict[str, Any]) -> str:
    for key in ("id", "document_id", "doc_id", "filename", "file"):
        value = label_doc.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _normalise_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\.(json|txt|md|pdf)$", "", text)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _match_document(
    documents: list[dict[str, Any]], identifier: str
) -> Optional[dict[str, Any]]:
    wanted = _normalise_id(identifier)
    for document in documents:
        if _normalise_id(doc_id(document)) == wanted:
            return document
    for document in documents:
        filename = document.get("filename") or document.get("file")
        if filename and _normalise_id(Path(str(filename)).stem) == wanted:
            return document
    if len(documents) == 1 and not wanted:
        return documents[0]
    return None


# ---------------------------------------------------------------------------
# Expected-value flattening
# ---------------------------------------------------------------------------


def _expected_pairs(label_doc: dict[str, Any]) -> list[tuple[str, Any]]:
    fields = None
    for key in ("fields", "expected", "expected_fields", "labels"):
        if key in label_doc and label_doc[key] is not None:
            fields = label_doc[key]
            break
    if fields is None:
        fields = {
            key: value
            for key, value in label_doc.items()
            if key not in LABEL_META_KEYS and key != "flags"
        }

    pairs: list[tuple[str, Any]] = []
    if isinstance(fields, dict):
        for name, value in fields.items():
            pairs.extend(_expand_field(str(name), value))
    elif isinstance(fields, list):
        for item in fields:
            if isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("field_name")
                value = item.get("value", item.get("expected"))
                if name is not None:
                    pairs.extend(_expand_field(str(name), value))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                pairs.extend(_expand_field(str(item[0]), item[1]))
    return pairs


def _expand_field(name: str, value: Any) -> list[tuple[str, Any]]:
    # Labels sometimes wrap a value with its own confidence.
    if isinstance(value, dict) and "value" in value and "kind" not in value:
        value = value.get("value")

    # A nested flags array is handled by _label_flags, not as a field comparison.
    if name.lower() in {"flags", "flag"} and isinstance(value, (list, dict)):
        return []

    if name.lower() in {"positions", "holdings", "position_list"}:
        if isinstance(value, list):
            pairs: list[tuple[str, Any]] = []
            for index, position in enumerate(value):
                if not isinstance(position, dict):
                    continue
                symbol = (
                    position.get("symbol")
                    or position.get("ticker")
                    or str(index)
                )
                for key, field_value in position.items():
                    if key in {"symbol", "ticker"}:
                        pairs.append((f"position[{symbol}].symbol", symbol))
                    else:
                        pairs.append((f"position[{symbol}].{key}", field_value))
            return pairs
        if isinstance(value, (int, float)):
            return [("positions_count", value)]

    return [(name, value)]


def _label_flags(label_doc: dict[str, Any]) -> list[str]:
    raw = label_doc.get("flags")
    if not raw:
        return []
    kinds: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                kinds.append(item)
            elif isinstance(item, dict):
                kind = item.get("kind") or item.get("name") or item.get("value")
                if kind:
                    kinds.append(str(kind))
    elif isinstance(raw, dict):
        kinds.extend(str(key) for key in raw.keys())
    elif isinstance(raw, str):
        kinds.append(raw)
    return kinds


# ---------------------------------------------------------------------------
# Actual-value indexing
# ---------------------------------------------------------------------------


def _actual_field_index(extraction: dict[str, Any]) -> dict[str, Any]:
    index: dict[str, Any] = {}
    fields = extraction.get("fields")
    if not isinstance(fields, list):
        return index
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = field.get("name")
        if name is None:
            continue
        index[_canonical_name(str(name))] = field.get("value")
    return index


def _actual_flag_index(extraction: dict[str, Any]) -> set[str]:
    flags = extraction.get("flags")
    kinds: set[str] = set()
    if isinstance(flags, list):
        for flag in flags:
            if isinstance(flag, dict) and flag.get("kind"):
                kinds.add(_canonical_flag(str(flag["kind"])))
            elif isinstance(flag, str):
                kinds.add(_canonical_flag(flag))
    return kinds


def _actual_positions_count(extraction: dict[str, Any]) -> int:
    fields = extraction.get("fields")
    if not isinstance(fields, list):
        return 0
    symbols = set()
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "")
        match = re.match(r"position\[([^\]]+)\]", name)
        if match:
            symbols.add(match.group(1))
    return len(symbols)


def _canonical_name(name: str) -> str:
    text = str(name).strip().lower()
    text = re.sub(r"\[([^\]]+)\]", lambda match: "[" + _canonical_token(match.group(1)) + "]", text)
    text = _canonical_token(text)
    text = text.replace(".", "_")
    text = re.sub(r"_+", "_", text).strip("_")
    return FIELD_ALIASES.get(text, text)


def _canonical_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")


def _canonical_flag(kind: str) -> str:
    text = str(kind).strip().lower()
    text = re.sub(r"^flag\s*:\s*", "", text)
    text = _canonical_token(text)
    return FLAG_ALIASES.get(text, text)


def _is_flag_expectation(field_name: str, value: Any) -> bool:
    name = str(field_name).strip()
    if name.lower() in {"flags", "flag"}:
        return True
    if name.upper().startswith("FLAG:"):
        return True
    return isinstance(value, str) and value.strip().upper().startswith("FLAG:")


def _flag_category(field_name: str, value: Any) -> str:
    name = str(field_name).strip()
    if name.upper().startswith("FLAG:"):
        return name.split(":", 1)[1]
    if isinstance(value, str) and value.strip().upper().startswith("FLAG:"):
        return value.split(":", 1)[1]
    return name


# ---------------------------------------------------------------------------
# Value comparison
# ---------------------------------------------------------------------------


def _normalise(value: Any) -> str:
    """Lowercase, trim, collapse whitespace, strip currency symbols and commas."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value}"
    text = str(value).strip().lower()
    text = text.replace(",", "")
    text = re.sub(r"[\u0024\u00a3\u20ac]", "", text)
    text = re.sub(r"\busd\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" .;")


def _values_match(expected: Any, actual: Any, field_name: str = "") -> bool:
    if expected is None or actual is None:
        return _normalise(expected) == _normalise(actual)

    expected_period = _period(expected)
    actual_period = _period(actual)
    if expected_period and actual_period:
        return expected_period == actual_period

    expected_date = _as_date(expected)
    actual_date = _as_date(actual)
    if expected_date and actual_date:
        return expected_date == actual_date

    expected_number = _as_number(expected)
    actual_number = _as_number(actual)
    if expected_number is not None and actual_number is not None:
        if expected_number == actual_number:
            return True
        tolerance = max(1e-9, abs(expected_number) * 1e-6)
        return abs(expected_number - actual_number) <= tolerance

    expected_masked = _masked_tail(expected)
    actual_masked = _masked_tail(actual)
    if expected_masked and actual_masked:
        return expected_masked == actual_masked

    return _normalise(expected) == _normalise(actual)


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    text = re.sub(r"\s+", "", text)
    if re.fullmatch(r"\(?-?\d+(?:\.\d+)?\)?", text):
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")
        try:
            number = float(text)
        except ValueError:
            return None
        return -number if negative else number
    return None


def _as_date(value: Any):
    if isinstance(value, str):
        return parse_date(value)
    return None


def _period(value: Any) -> Optional[tuple[Any, Any]]:
    if not isinstance(value, str):
        return None
    parts = re.split(r"\s+(?:to|through|thru|[-\u2013\u2014])\s+", value.strip(), maxsplit=1)
    if len(parts) != 2:
        return None
    start = parse_date(parts[0].strip())
    end = parse_date(parts[1].strip())
    if start and end:
        return (start, end)
    return None


def _masked_tail(value: Any) -> str:
    """Digit tail for masked numbers, or a plain 3 to 6 digit number.

    Label files sometimes store only "4821" while the extractor returns "****4821".
    Comparing the tail is the honest middle ground: it does not credit a different
    account number, and it does not fail on the mask alone.
    """

    if not isinstance(value, str):
        return ""
    text = value.strip()
    digits = re.findall(r"\d", text)
    if len(digits) < 3:
        return ""
    if re.search(r"[*xX\u2022]", text):
        return "".join(digits[-4:])
    if text.isdigit() and 3 <= len(text) <= 6:
        return "".join(digits[-4:])
    return ""


def _display(value: Any) -> str:
    if value is MISSING:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ---------------------------------------------------------------------------
# Report write
# ---------------------------------------------------------------------------


def _write_report(base: Path, report: dict[str, Any]) -> None:
    target = base / "eval" / "report.json"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        # A read-only deployment should still return the computed report.
        report.setdefault("notes", []).append(f"Could not write {target}.")
