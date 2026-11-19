"""Prompting, parsing, and deterministic logic for LP Ops.

Three model calls per packet at most: extraction, checklist, status note. Every
call returns strict JSON. The parsers never raise on bad model output; they keep
the raw text and return a clear error so the page can degrade.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from ...platform import llm

MODULE = "lpo"
MISSING = "__missing__"

FIELD_KEYS = [
    "lp_name",
    "entity_type",
    "commitment_amount",
    "qualified_status",
    "custodian",
    "bank_details_present",
    "signature_present",
    "tax_id_present",
    "side_letter",
    "subscription_date",
    "closing_date",
    "wire_instructions",
]

FIELD_LABELS = {
    "lp_name": "LP name",
    "entity_type": "Entity type",
    "commitment_amount": "Commitment",
    "qualified_status": "QP or accredited status",
    "custodian": "Custodian",
    "bank_details_present": "Bank details present",
    "signature_present": "Signature present",
    "tax_id_present": "Tax ID present",
    "side_letter": "Side letter",
    "subscription_date": "Subscription date",
    "closing_date": "Closing date",
    "wire_instructions": "Wire instructions",
}

MONEY_FIELDS = {"commitment_amount"}
DATE_FIELDS = {"subscription_date", "closing_date"}
YESNO_FIELDS = {"bank_details_present", "signature_present", "tax_id_present", "side_letter"}
TEXT_FIELDS = {"lp_name", "entity_type", "custodian"}

KIND_LABELS = {
    "missing_signature": "Missing signature",
    "missing_kyc": "Missing KYC",
    "bank_name_mismatch": "Bank name mismatch",
    "commitment_mismatch": "Commitment mismatch",
    "date_anomaly": "Date anomaly",
}
KIND_SEVERITY = {
    "missing_signature": "high",
    "missing_kyc": "high",
    "bank_name_mismatch": "medium",
    "commitment_mismatch": "high",
    "date_anomaly": "medium",
}
SEVERITY_LABELS = {"high": "High", "medium": "Medium", "low": "Low"}
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
CHECKLIST_STATUS_LABELS = {
    "blocked": "Blocked",
    "ready_for_review": "Ready for review",
    "ready_to_fund": "Ready to fund",
    "review": "Needs review",
}
CHECKLIST_OWNERS = ["Ops", "Compliance", "Counsel", "Fund administrator"]

KIND_ALIASES = {
    "missing_signature_page": "missing_signature",
    "unsigned": "missing_signature",
    "signature_missing": "missing_signature",
    "missing_signatures": "missing_signature",
    "kyc_missing": "missing_kyc",
    "missing_kyc_file": "missing_kyc",
    "kyc_pending": "missing_kyc",
    "bank_mismatch": "bank_name_mismatch",
    "wire_bank_mismatch": "bank_name_mismatch",
    "custodian_mismatch": "bank_name_mismatch",
    "commitment_discrepancy": "commitment_mismatch",
    "commitment_mismatch_with_schedule": "commitment_mismatch",
    "date_inconsistency": "date_anomaly",
    "closing_before_subscription": "date_anomaly",
    "date_error": "date_anomaly",
}

EXTRACT_SYSTEM = """You are the document intake assistant for the private markets desk of a US registered investment adviser. Read one synthetic subscription packet and return funding fields plus exceptions for a human operator.

Return one JSON object only. No markdown fences, no commentary. Extract only what the packet states. Never infer a missing value and never copy a value from the administrator record into a field. Use "Not stated" for an absent text value, "No" for a Yes/No check with no evidence, and null for an absent date. The administrator record is only a comparison source for exceptions.

Allowed exception kinds, exact strings: missing_signature, missing_kyc, bank_name_mismatch, commitment_mismatch, date_anomaly. Use no other kind. Severity is "high", "medium", or "low".

Definitions:
- missing_signature: there is no executed signature and no named signer in the packet.
- missing_kyc: the administrator record shows KYC status pending, or the packet says KYC review has not started. KYC in review is not missing.
- bank_name_mismatch: the bank name in the wire block is not the custodian of record.
- commitment_mismatch: the packet commitment differs from the administrator record.
- date_anomaly: the closing date is before the subscription date, or a date is impossible.
Return an empty exceptions list when none apply.

confidence is one number from 0 to 1 for the whole extraction. Do not default to 0.99.

Schema:
{"fields":{"lp_name":"","entity_type":"","commitment_amount":0,"qualified_status":"","custodian":"","bank_details_present":"Yes","signature_present":"Yes","tax_id_present":"Yes","side_letter":"No","subscription_date":"YYYY-MM-DD","closing_date":"YYYY-MM-DD","wire_instructions":""},"exceptions":[{"kind":"","detail":"","severity":"high"}],"confidence":0.0}

Field rules:
- Copy lp_name, entity_type, and custodian exactly as the packet states them. Do not translate or abbreviate.
- commitment_amount is an integer in USD. Do not include a currency symbol or commas.
- qualified_status is "Qualified purchaser", "Accredited investor", or "Not stated".
- bank_details_present is "Yes" only when the packet states a bank name, an ABA number, and an account number. Otherwise "No".
- signature_present is "Yes" only when the packet shows an executed signature or a named signer. A blank line is "No".
- tax_id_present is "Yes" only when a tax ID number is stated. "Pending" or "blank" is "No".
- side_letter is "Yes" only when a side letter is stated as included. Otherwise "No".
- subscription_date and closing_date are ISO dates. Use null if absent.
- wire_instructions is "Domestic wire", "International wire", or "Not stated"."""

CHECKLIST_SYSTEM = """You are the operations checklist writer for a private markets desk. Turn one subscription packet extraction into a short internal checklist for a human operator.

Return one JSON object only. No markdown. Schema:
{"status":"blocked","summary":"one or two sentences","checklist":[{"item":"","owner":"Ops","due":"YYYY-MM-DD","blocking":true}]}

status is "blocked", "ready_for_review", or "ready_to_fund".
- blocked: a high severity exception or a missing funding field stops the packet.
- ready_for_review: no high severity exception, but a medium item or a manual confirmation remains.
- ready_to_fund: no open exception and the wire and tax details are present.

Write 6 to 8 checklist items. Order blocking items first. owner is one of Ops, Compliance, Counsel, Fund administrator. due is an ISO date on or before the fund final close. blocking is true only for an item that must clear before funding.

Do not give investment, tax, or legal advice. Do not invent a fact. Use only the packet, the extraction, the administrator record, and the exception log. The administrator issue log is authoritative for what the desk already knows."""

NOTE_SYSTEM = """You are drafting a short operational status note from a private markets desk to one limited partner. This is an internal draft for a named human approval. The software does not send it.

Return one JSON object only: {"subject":"","body":"","asks":[""]}

Body: 2 to 4 short paragraphs in plain language. State what is outstanding and what happens next. Reference the fund closing date and the specific documents or confirmations needed. Do not give investment, tax, or legal advice. Do not mention other LPs, fees, performance, or returns. Do not promise a closing or an allocation. Do not use em dashes.

asks: 1 to 3 short action items for the LP, each under 20 words."""


# ---------------------------------------------------------------------------
# Keys and model calls
# ---------------------------------------------------------------------------


def extraction_key(packet_id: str) -> str:
    return f"extract-{packet_id.lower()}"


def checklist_key(packet_id: str) -> str:
    return f"checklist-{packet_id.lower()}"


def note_key(packet_id: str) -> str:
    return f"note-{packet_id.lower()}"


def _record_json(packet: dict[str, Any]) -> str:
    record = packet.get("administrator_record") or {}
    return json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True)


def extraction_prompt(packet: dict[str, Any]) -> str:
    text = str(packet.get("text") or "")
    return (
        f"Fund: {packet.get('fund')}\n"
        f"Packet id: {packet.get('id')}\n"
        f"Packet received: {packet.get('received')}\n"
        "Administrator record, for exception checks only:\n"
        f"{_record_json(packet)}\n\n"
        "Packet text begins after the marker.\n"
        "--- PACKET START ---\n"
        f"{text}"
        "--- PACKET END ---\n\n"
        "Return the JSON object now."
    )


def _prompt_fields(extraction: dict[str, Any] | None) -> dict[str, Any]:
    if not extraction:
        return {}
    fields = extraction.get("fields") or {}
    return {
        key: ("Not returned" if fields.get(key) == MISSING else fields.get(key))
        for key in FIELD_KEYS
    }


def _prompt_exceptions(extraction: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not extraction:
        return []
    return [
        {
            "kind": item.get("kind"),
            "detail": item.get("detail"),
            "severity": item.get("severity"),
        }
        for item in extraction.get("exceptions", [])
    ]


def checklist_prompt(packet: dict[str, Any], extraction: dict[str, Any] | None) -> str:
    known = [
        {"kind": item.get("kind"), "severity": item.get("severity"), "detail": item.get("detail")}
        for item in (packet.get("known_exceptions") or [])
    ]
    body = {
        "packet_id": packet.get("id"),
        "lp_id": packet.get("lp_id"),
        "lp_name": (packet.get("administrator_record") or {}).get("lp_name"),
        "fund": packet.get("fund"),
        "final_close": (packet.get("administrator_record") or {}).get("final_close"),
        "received": packet.get("received"),
        "administrator_record": packet.get("administrator_record") or {},
        "extracted_fields": _prompt_fields(extraction),
        "model_exceptions": _prompt_exceptions(extraction),
        "administrator_issue_log": known,
        "extraction_ok": bool(extraction and extraction.get("ok")),
        "extraction_error": (extraction or {}).get("error", ""),
    }
    return (
        f"{json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True)}\n\n"
        "Packet text, for exact document names and blanks:\n"
        "--- PACKET START ---\n"
        f"{packet.get('text') or ''}"
        "--- PACKET END ---\n\n"
        "Return the JSON object now."
    )


def status_note_prompt(
    packet: dict[str, Any],
    extraction: dict[str, Any] | None,
    checklist: dict[str, Any] | None,
) -> str:
    known_open = [
        {"kind": item.get("kind"), "severity": item.get("severity"), "detail": item.get("detail")}
        for item in (packet.get("known_exceptions") or [])
    ]
    body = {
        "packet_id": packet.get("id"),
        "lp_name": (packet.get("administrator_record") or {}).get("lp_name"),
        "fund": packet.get("fund"),
        "final_close": (packet.get("administrator_record") or {}).get("final_close"),
        "received": packet.get("received"),
        "extracted_fields": _prompt_fields(extraction),
        "open_items": known_open,
        "checklist_status": (checklist or {}).get("status", ""),
        "checklist_summary": (checklist or {}).get("summary", ""),
        "checklist_items": [
            {"item": item.get("item"), "owner": item.get("owner"), "blocking": item.get("blocking")}
            for item in (checklist or {}).get("steps", [])
        ],
    }
    return (
        f"{json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True)}\n\n"
        "The note is to the limited partner named above. Address only what that LP must do. "
        "Return the JSON object now."
    )


def run_extraction(packet: dict[str, Any], *, refresh: bool = False) -> llm.LLMResult:
    return llm.run(
        MODULE,
        extraction_key(str(packet.get("id"))),
        EXTRACT_SYSTEM,
        extraction_prompt(packet),
        max_tokens=900,
        temperature=0.1,
        refresh=refresh,
    )


def run_checklist(packet: dict[str, Any], extraction: dict[str, Any] | None, *, refresh: bool = False) -> llm.LLMResult:
    return llm.run(
        MODULE,
        checklist_key(str(packet.get("id"))),
        CHECKLIST_SYSTEM,
        checklist_prompt(packet, extraction),
        max_tokens=900,
        temperature=0.2,
        refresh=refresh,
    )


def run_status_note(
    packet: dict[str, Any],
    extraction: dict[str, Any] | None,
    checklist: dict[str, Any] | None,
    *,
    refresh: bool = False,
) -> llm.LLMResult:
    return llm.run(
        MODULE,
        note_key(str(packet.get("id"))),
        NOTE_SYSTEM,
        status_note_prompt(packet, extraction, checklist),
        max_tokens=600,
        temperature=0.3,
        refresh=refresh,
    )


# ---------------------------------------------------------------------------
# Parsing and normalisation
# ---------------------------------------------------------------------------


def parse_json_object(text: str) -> tuple[dict[str, Any] | None, str]:
    if not text or not text.strip():
        return None, "The recording is empty."
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = chr(10).join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None, "No JSON object was found in the model output."
    candidate = cleaned[start : end + 1]
    try:
        payload = json.loads(candidate)
    except ValueError as exc:
        return None, f"JSON parse failed: {exc}"
    if not isinstance(payload, dict):
        return None, "The model output was not a JSON object."
    return payload, ""


def _clean_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if text.lower() in {"", "not stated", "none", "null", "n/a", "unknown"}:
        return "Not stated"
    return text


def normalize_money(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return MISSING
    if isinstance(value, (int, float)):
        return int(round(value))
    text = str(value).strip().replace(",", "")
    match = re.search(r"-?[0-9]+(?:[.][0-9]+)?", text)
    if not match:
        return MISSING
    try:
        return int(round(float(match.group(0))))
    except ValueError:
        return MISSING


def normalize_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text or text.lower() in {"not stated", "none", "null", "n/a"}:
        return "Not stated"
    text = text.replace("Sept ", "Sep ")
    for pattern in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    match = re.search(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text)
    if match:
        return match.group(0)
    return "Not stated"


def normalize_yesno(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    text = str(value or "").strip().lower()
    if not text:
        return "Not stated"
    if text.startswith("yes") or text in {"y", "true", "present", "provided", "attached", "executed"}:
        return "Yes"
    if text.startswith("no") or text in {"n", "false", "absent", "missing", "blank", "none", "none requested"}:
        return "No"
    if text.startswith("not ") or "pending" in text or "blank" in text:
        return "No" if ("pending" in text or "blank" in text) else "Not stated"
    return "Not stated"


def normalize_qualified(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "qualified" in text or re.search(r"(^|[^a-z])qp([^a-z]|$)", text):
        return "Qualified purchaser"
    if "accredited" in text:
        return "Accredited investor"
    return "Not stated"


def normalize_wire(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "international" in text:
        return "International wire"
    if "domestic" in text:
        return "Domestic wire"
    return "Not stated"


def normalize_field(key: str, value: Any) -> Any:
    if value is MISSING:
        return MISSING
    if key in MONEY_FIELDS:
        return normalize_money(value)
    if key in DATE_FIELDS:
        return normalize_date(value)
    if key in YESNO_FIELDS:
        return normalize_yesno(value)
    if key == "qualified_status":
        return normalize_qualified(value)
    if key == "wire_instructions":
        return normalize_wire(value)
    return _clean_text(value)


def field_equal(key: str, expected: Any, actual: Any) -> bool:
    if expected == MISSING or actual == MISSING:
        return expected == actual
    if key in MONEY_FIELDS:
        return normalize_money(expected) == normalize_money(actual)
    if key in DATE_FIELDS:
        return normalize_date(expected) == normalize_date(actual)
    if key in YESNO_FIELDS:
        return normalize_yesno(expected) == normalize_yesno(actual)
    if key == "qualified_status":
        return normalize_qualified(expected) == normalize_qualified(actual)
    if key == "wire_instructions":
        return normalize_wire(expected) == normalize_wire(actual)
    left = " ".join(str(expected or "").split()).lower().rstrip(".")
    right = " ".join(str(actual or "").split()).lower().rstrip(".")
    return left == right


def canonical_kind(raw: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(raw or "").strip().lower()).strip("_")
    if not slug:
        return ""
    if slug in KIND_LABELS:
        return slug
    if slug in KIND_ALIASES:
        return KIND_ALIASES[slug]
    return slug


def normalize_exceptions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            kind = canonical_kind(item)
            detail = ""
            severity = KIND_SEVERITY.get(kind, "medium")
        elif isinstance(item, dict):
            kind = canonical_kind(item.get("kind"))
            detail = _clean_text(item.get("detail"))
            severity = str(item.get("severity") or "").strip().lower()
            if severity not in SEVERITY_LABELS:
                severity = KIND_SEVERITY.get(kind, "medium")
        else:
            continue
        if not kind or kind in seen:
            continue
        seen.add(kind)
        result.append(
            {
                "kind": kind,
                "kind_label": kind_label(kind),
                "is_known": kind in KIND_LABELS,
                "detail": "" if detail == "Not stated" else detail,
                "severity": severity,
                "severity_label": SEVERITY_LABELS.get(severity, "Medium"),
            }
        )
    return result


def kind_label(kind: str) -> str:
    if kind in KIND_LABELS:
        return KIND_LABELS[kind]
    return "Unrecognised flag: " + str(kind).replace("_", " ")


def parse_extraction(text: str) -> dict[str, Any]:
    payload, error = parse_json_object(text)
    if payload is None:
        return {
            "ok": False,
            "error": error,
            "raw": text,
            "payload": {},
            "fields": {key: MISSING for key in FIELD_KEYS},
            "exceptions": [],
            "confidence": None,
            "warning": "",
        }
    raw_fields = payload.get("fields")
    warning = ""
    if not isinstance(raw_fields, dict):
        raw_fields = {}
        warning = "The model output had no fields object."
    fields: dict[str, Any] = {}
    for key in FIELD_KEYS:
        if key in raw_fields:
            fields[key] = normalize_field(key, raw_fields[key])
        else:
            fields[key] = MISSING
    confidence: float | None = None
    try:
        if payload.get("confidence") is not None:
            confidence = max(0.0, min(1.0, float(payload.get("confidence"))))
    except (TypeError, ValueError):
        confidence = None
    return {
        "ok": True,
        "error": "",
        "raw": text,
        "payload": payload,
        "fields": fields,
        "exceptions": normalize_exceptions(payload.get("exceptions")),
        "confidence": confidence,
        "warning": warning,
    }


def parse_checklist(text: str) -> dict[str, Any]:
    payload, error = parse_json_object(text)
    if payload is None:
        return {
            "ok": False,
            "error": error,
            "raw": text,
            "status": "review",
            "status_label": CHECKLIST_STATUS_LABELS["review"],
            "summary": "",
            "items": [],
        }
    status = str(payload.get("status") or "").strip().lower().replace(" ", "_")
    if status not in {"blocked", "ready_for_review", "ready_to_fund"}:
        status = "review"
    summary = _clean_text(payload.get("summary"))
    items: list[dict[str, Any]] = []
    raw_items = payload.get("checklist")
    if isinstance(raw_items, list):
        for raw in raw_items[:9]:
            if not isinstance(raw, dict):
                continue
            item = _clean_text(raw.get("item"))
            if item == "Not stated":
                continue
            owner = _clean_text(raw.get("owner"))
            if owner not in CHECKLIST_OWNERS:
                owner = "Ops"
            due = normalize_date(raw.get("due"))
            blocking = bool(raw.get("blocking"))
            items.append({"item": item, "owner": owner, "due": due, "blocking": blocking})
    return {
        "ok": True,
        "error": "",
        "raw": text,
        "status": status,
        "status_label": CHECKLIST_STATUS_LABELS.get(status, "Needs review"),
        "summary": "" if summary == "Not stated" else summary,
        "steps": items,
    }


def parse_note(text: str) -> dict[str, Any]:
    payload, error = parse_json_object(text)
    if payload is None:
        return {"ok": False, "error": error, "raw": text, "subject": "", "body": "", "asks": [], "paragraphs": []}
    subject = _clean_text(payload.get("subject"))
    body = str(payload.get("body") or "")
    body = body.replace(chr(13) + chr(10), chr(10)).strip()
    asks: list[str] = []
    raw_asks = payload.get("asks")
    if isinstance(raw_asks, list):
        for item in raw_asks[:4]:
            value = _clean_text(item)
            if value != "Not stated":
                asks.append(value)
    splitter = chr(10) + r"[ 	]*" + chr(10)
    paragraphs = [part.strip() for part in re.split(splitter, body) if part.strip()]
    if len(paragraphs) == 1:
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        if len(lines) > 1:
            paragraphs = lines
    return {
        "ok": bool(subject and body),
        "error": "" if (subject and body) else "The note was missing a subject or body.",
        "raw": text,
        "subject": "" if subject == "Not stated" else subject,
        "body": body,
        "asks": asks,
        "paragraphs": paragraphs,
    }


def note_flags(text: str) -> list[str]:
    checks = [
        ("recommendation", r"we recommend|you should (invest|buy|sell|allocate)"),
        ("promised return", r"guarantee[ds]?|promised return|expected return"),
        ("performance", r"performance|multiple"),
        ("other LPs", r"other (limited partners|LPs)|other investors"),
    ]
    lowered = str(text or "")
    found: list[str] = []
    for label, pattern in checks:
        if re.search(pattern, lowered, re.IGNORECASE):
            found.append(label)
    if re.search(r"(^|[^A-Za-z])IRR([^A-Za-z]|$)", str(text or "")):
        if "performance" not in found:
            found.append("performance")
    return found


# ---------------------------------------------------------------------------
# Evidence spans
# ---------------------------------------------------------------------------

FIELD_LABEL_PATTERNS = {
    "lp_name": [r"legal name", r"investor[ ]*:", r"^[ ]*name[ ]*:"],
    "entity_type": [r"entity type", r"form of entity"],
    "custodian": [r"custodian"],
    "subscription_date": [r"subscription date"],
    "closing_date": [r"closing date", r"anticipated closing", r"closing[ ]*:"],
    "side_letter": [r"side letter[ ]*:"],
}


EVIDENCE_PATTERNS = {
    "signature_present": [r"signature[ ]*:", r"signed by[ ]*:", r"/s/", r"signature", r"signer"],
    "tax_id_present": [r"TIN", r"EIN", r"tax id"],
    "bank_details_present": [r"ABA", r"account:", r"bank name", r"bank details"],
    "side_letter": [r"side letter"],
    "qualified_status": [r"qualified purchaser", r"accredited"],
    "wire_instructions": [r"wire instructions", r"wire type", r"domestic wire", r"international wire"],
}


def _date_tokens(value: str) -> list[str]:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return [value]
    return [
        parsed.isoformat(),
        f"{parsed:%B} {parsed.day}, {parsed.year}",
        f"{parsed:%b} {parsed.day}, {parsed.year}",
        f"{parsed.month:02d}/{parsed.day:02d}/{parsed.year}",
        f"{parsed.day} {parsed:%B} {parsed.year}",
        f"{parsed.day} {parsed:%b} {parsed.year}",
    ]


def _value_tokens(key: str, value: Any) -> list[str]:
    if value in {MISSING, "Not stated", "No"}:
        return []
    if key in MONEY_FIELDS:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return [str(value)]
        return [f"{number:,}", str(number)]
    if key in DATE_FIELDS:
        return _date_tokens(str(value))
    return [str(value)]


def locate_evidence(text: str, key: str, value: Any) -> dict[str, Any] | None:
    lines = str(text or "").splitlines()
    label_patterns = FIELD_LABEL_PATTERNS.get(key)
    if label_patterns:
        for pattern in label_patterns:
            for number, line in enumerate(lines, 1):
                if re.search(pattern, line, re.IGNORECASE):
                    return {
                        "line": number,
                        "text": line.strip(),
                        "absent": value in {"No", "Not stated"},
                    }
    patterns = EVIDENCE_PATTERNS.get(key)
    if patterns:
        for pattern in patterns:
            for number, line in enumerate(lines, 1):
                if re.search(pattern, line, re.IGNORECASE):
                    return {
                        "line": number,
                        "text": line.strip(),
                        "absent": value in {"No", "Not stated"},
                    }
    for token in _value_tokens(key, value):
        if not token:
            continue
        for number, line in enumerate(lines, 1):
            if token.lower() in line.lower():
                return {"line": number, "text": line.strip(), "absent": False}
    return None


# ---------------------------------------------------------------------------
# Exceptions, stages, formatting
# ---------------------------------------------------------------------------


def merge_exceptions(
    packet: dict[str, Any],
    extraction: dict[str, Any] | None,
    resolved: set[str],
) -> dict[str, Any]:
    known = [item for item in (packet.get("known_exceptions") or []) if isinstance(item, dict)]
    model = (extraction or {}).get("exceptions") or []
    model_by_kind = {
        str(item.get("kind")): item for item in model if isinstance(item, dict) and item.get("kind")
    }
    rows: list[dict[str, Any]] = []
    for issue in known:
        kind = str(issue.get("kind"))
        model_flag = model_by_kind.pop(kind, None)
        status = "approved" if kind in resolved else "open"
        rows.append(
            {
                "origin": "log",
                "kind": kind,
                "label": KIND_LABELS.get(kind, kind),
                "detail": str(issue.get("detail") or ""),
                "severity": str(issue.get("severity") or KIND_SEVERITY.get(kind, "medium")),
                "severity_label": SEVERITY_LABELS.get(str(issue.get("severity") or ""), "Medium"),
                "status": status,
                "model": "matched" if model_flag else "missed",
                "model_detail": str((model_flag or {}).get("detail") or ""),
            }
        )
    for kind, flag in model_by_kind.items():
        rows.append(
            {
                "origin": "model",
                "kind": kind,
                "label": kind_label(kind),
                "detail": str(flag.get("detail") or "The model flagged this issue. It is not in the administrator log."),
                "severity": str(flag.get("severity") or "medium"),
                "severity_label": SEVERITY_LABELS.get(str(flag.get("severity") or ""), "Medium"),
                "status": "model_only",
                "model": "extra",
                "model_detail": "",
            }
        )
    rows.sort(key=lambda row: (SEVERITY_RANK.get(str(row.get("severity")), 3), 0 if row.get("origin") == "log" else 1))
    matched = sum(1 for row in rows if row.get("origin") == "log" and row.get("model") == "matched")
    known_total = len(known)
    open_count = sum(1 for row in rows if row.get("origin") == "log" and row.get("status") == "open")
    extra = sum(1 for row in rows if row.get("origin") == "model")
    return {
        "rows": rows,
        "known_total": known_total,
        "matched": matched,
        "missed": known_total - matched,
        "open_count": open_count,
        "extra": extra,
    }


def stage_next(stage: str) -> str | None:
    if stage == "document_intake":
        return "exception_review"
    if stage == "exception_review":
        return "ready_to_fund"
    return None


def stage_advance_error(stage: str) -> str:
    if stage == "ready_to_fund":
        return "funded"
    if stage == "funded":
        return "funded"
    return "stage"


def parse_actor(value: str, team: list[dict[str, str]]) -> tuple[str, str]:
    text = str(value or "").strip()
    if "|" in text:
        name, role = text.split("|", 1)
    else:
        name, role = text, ""
    name = name.strip()
    role = role.strip()
    for member in team:
        if member.get("name") == name and member.get("role") == role:
            return name, role
    return "", ""


def format_money(value: Any) -> str:
    try:
        return f"USD {int(value):,}"
    except (TypeError, ValueError):
        return "Not stated"


def format_date(value: Any) -> str:
    try:
        parsed = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return "Not stated"
    return f"{parsed:%b} {parsed.day}, {parsed.year}"


def format_datetime(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "Not stated"
    hour = parsed.strftime("%I").lstrip("0") or "12"
    return f"{parsed:%b} {parsed.day}, {parsed.year}, {hour}:{parsed:%M %p} UTC"


def display_value(key: str, value: Any) -> str:
    if value == MISSING:
        return "Not returned"
    if key in MONEY_FIELDS and isinstance(value, int):
        return format_money(value)
    if key in DATE_FIELDS:
        return format_date(value)
    return str(value)


def confidence_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{round(value * 100)}%"
