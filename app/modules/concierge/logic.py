"""Concierge logic: one model call per question, plus the deterministic checks.

The model explains the household record and cites a field for every number. It
does not advise and it does not move money. Two checks run after the call:

    citations   every cited path must exist in the record and the cited value
                must match the record value
    numbers     every number in the answer must appear in a citation value

Both run in code, not in the model, so a bad answer is caught even when the
model ignores the prompt.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.platform import audit, llm

MODULE = "concierge"
ADVISER = "Dana Whitfield"
ALLOWED_INTENTS = ("explain", "advice", "transaction", "unsupported")
ROUTE_INTENTS = ("advice", "transaction")

SYSTEM = (
    "You are a client-facing assistant inside a registered investment adviser's portal. "
    "You answer a client's question about that client's own household record, in plain English.\n"
    "\n"
    "Rules:\n"
    "1. Explain only. Never recommend a security, an allocation, a trade, or a money movement.\n"
    "2. Cite the record field behind every figure. Use only field paths from the field table.\n"
    "3. Copy every value exactly as the field table shows it, including the USD prefix and the commas. "
    "In the answer, refer to an account by its type and masked number, not by its internal id.\n"
    "4. Every number that appears in your answer must appear in one of your citation values.\n"
    "5. If the question asks whether to buy, sell, hold, switch, or rebalance, or asks for a recommendation, "
    'set intent to "advice", do not answer the investment question, and write a short route message saying '
    "the adviser will answer it.\n"
    "6. If the question asks you to move, transfer, withdraw, deposit, or trade money, set intent to "
    '"transaction" and write a short route message saying the adviser or the operations team will handle it. '
    "Do not repeat an amount from the client's question unless the record holds that exact value.\n"
    "7. If the record does not contain the facts, set intent to \"unsupported\" and say what is missing. Do not guess.\n"
    "8. Otherwise set intent to \"explain\".\n"
    "9. Do not use em dashes, markdown, or bullet points. Do not promise performance. Do not give tax or legal advice.\n"
    "10. This answer is a draft for the adviser to approve. Never say that an action has been taken.\n"
    "\n"
    "Return only JSON, no code fence, with this shape:\n"
    '{"intent": "explain" or "advice" or "transaction" or "unsupported", '
    '"answer": "plain text, at most 90 words", '
    '"citations": [{"field": "record.field.path", "value": "exact value string"}], '
    '"route_to_advisor": true or false}\n'
    "route_to_advisor is true for advice and transaction, false otherwise. "
    "Use an empty citations list when the answer contains no numbers."
)

NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
PATH_RE = re.compile(r"\b[a-z][a-z0-9_]*(?:\.[a-z0-9_-]+)+\b")
MASKED_RE = re.compile(r"\bxxxx-\d+\b", re.IGNORECASE)
ID_RE = re.compile(r"\b[A-Z]{1,3}-\d{1,4}\b")
ISO_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
QUARTER_RE = re.compile(r"(?<![a-z])q[1-4](?![0-9])", re.IGNORECASE)


def field_index(household: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {field["path"]: field for field in household["fields"]}


def field_table(household: dict[str, Any]) -> str:
    lines = ["path | label | value"]
    for field in household["fields"]:
        lines.append(f"{field['path']} | {field['label']} | {field['value']}")
    return "\n".join(lines)


def build_prompt(household: dict[str, Any], question: str) -> str:
    return (
        f"Household: {household['id']} {household['display_name']}\n"
        f"Consent status on the record: {household['consent_status']}\n"
        f"Record date: {next(field['value'] for field in household['fields'] if field['path'] == 'household.as_of')}\n"
        f"Question from the client: {question}\n"
        "\n"
        "Field table for this household. Use these paths and these values only.\n"
        f"{field_table(household)}\n"
        "\n"
        "Answer the question above as strict JSON."
    )


def normalise_number(token: str) -> str:
    cleaned = token.replace(",", "").strip()
    if not cleaned:
        return ""
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return ""
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def canonical(text: str) -> str:
    lowered = str(text or "").lower().replace("usd", " ")
    lowered = lowered.replace(",", " ").replace("%", " ")
    return " ".join(lowered.split())


def values_match(claimed: str, record_value: str) -> bool:
    left = canonical(claimed)
    right = canonical(record_value)
    if left == right:
        return True
    try:
        return Decimal(left) == Decimal(right)
    except (InvalidOperation, ValueError):
        return False


def strip_identifiers(text: str) -> str:
    """Remove ids, dates and field paths so only quantities are left."""

    cleaned = str(text or "")
    for pattern in (MASKED_RE, ID_RE, ISO_RE, PATH_RE, QUARTER_RE, YEAR_RE):
        cleaned = pattern.sub(" ", cleaned)
    return cleaned


def numbers_in(text: str) -> list[str]:
    found: list[str] = []
    for token in NUMBER_RE.findall(str(text or "")):
        normalised = normalise_number(token)
        if normalised and normalised not in found:
            found.append(normalised)
    return found


def resolve_citations(citations: list[dict[str, Any]], index: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        path = str(citation.get("field") or "").strip()
        claimed = str(citation.get("value") or "").strip()
        record = index.get(path)
        if record is None:
            resolved.append(
                {
                    "field": path,
                    "label": "not in the record",
                    "claimed": claimed,
                    "value": claimed,
                    "record_value": "",
                    "status": "unknown",
                    "note": "The field path is not in this household record.",
                }
            )
            continue
        status = "match" if values_match(claimed, record["value"]) else "mismatch"
        resolved.append(
            {
                "field": path,
                "label": record["label"],
                "claimed": claimed,
                "value": record["value"],
                "record_value": record["value"],
                "status": status,
                "note": "" if status == "match" else "The cited value differs from the record value.",
            }
        )
    return resolved


def guardrail(answer: str, citations: list[dict[str, Any]], index: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Check every number in the answer against the citation values."""

    covered: list[str] = []
    for citation in citations:
        record = index.get(str(citation.get("field") or "").strip())
        source = record["value"] if record is not None else str(citation.get("value") or "")
        covered.extend(numbers_in(source))

    checked = numbers_in(strip_identifiers(answer))
    uncovered = [token for token in checked if token not in covered]
    unresolved = [
        citation["field"]
        for citation in resolve_citations(citations, index)
        if citation["status"] != "match"
    ]
    return {
        "ok": not uncovered and not unresolved,
        "checked": len(checked),
        "covered": len([token for token in checked if token in covered]),
        "uncovered": uncovered,
        "unresolved": unresolved,
    }


def parse_answer(raw: str) -> dict[str, Any]:
    """Parse the model's JSON defensively. A failure keeps the raw text."""

    text = str(raw or "").strip()
    text = text.replace("```", "").strip()
    start = text.find("{")
    end = text.rfind("}")
    payload: dict[str, Any] = {}
    parse_error = ""
    if start >= 0 and end > start:
        try:
            candidate = json.loads(text[start : end + 1])
            if isinstance(candidate, dict):
                payload = candidate
            else:
                parse_error = "The model returned JSON that is not an object."
        except ValueError as exc:
            parse_error = f"The model did not return valid JSON: {exc}"
    else:
        parse_error = "No JSON object was found in the model output."

    intent = str(payload.get("intent") or "").strip().lower()
    if intent not in ALLOWED_INTENTS:
        if intent and not parse_error:
            parse_error = f"The model returned an intent outside the four allowed labels: {intent}."
        intent = "unsupported"

    answer = str(payload.get("answer") or "").strip()
    answer = answer.replace("**", "").replace("`", "")
    dash = chr(8212)
    endash = chr(8211)
    answer = answer.replace(" " + dash + " ", ", ").replace(" " + endash + " ", ", ")
    answer = answer.replace(dash, ", ").replace(endash, ", ")
    answer = answer.replace(" ,", ",").replace(" .", ".")
    answer = " ".join(answer.split())

    raw_citations = payload.get("citations")
    citations: list[dict[str, str]] = []
    if isinstance(raw_citations, list):
        for item in raw_citations:
            if isinstance(item, dict) and item.get("field"):
                citations.append(
                    {"field": str(item.get("field")).strip(), "value": str(item.get("value") or "").strip()}
                )

    route = bool(payload.get("route_to_advisor"))
    policy_notes: list[str] = []
    if intent in ROUTE_INTENTS and not route:
        route = True
        policy_notes.append("The route flag was forced on because the intent is advice or transaction.")
    if intent not in ROUTE_INTENTS and route:
        policy_notes.append("The model set the route flag on an explain answer. The flag is kept.")

    return {
        "intent": intent,
        "answer": answer,
        "citations": citations,
        "route_to_advisor": route,
        "parse_error": parse_error,
        "policy_notes": policy_notes,
        "raw": str(raw or ""),
    }


def provenance(result: llm.LLMResult) -> dict[str, Any]:
    usage = result.usage or {}
    return {
        "model": result.model,
        "mode": result.mode,
        "recorded_live": result.mode == llm.MODE_LIVE,
        "is_live": result.is_live,
        "created_at": result.created_at,
        "latency_ms": result.latency_ms,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "key": result.key,
    }


def ask(
    household: dict[str, Any],
    question: str,
    *,
    key: str,
    refresh: bool = False,
    audit_live: bool = True,
) -> dict[str, Any]:
    """One model call, parsed, checked, and ready for the page."""

    result = llm.run(
        MODULE,
        key,
        SYSTEM,
        build_prompt(household, question),
        max_tokens=700,
        temperature=0.2,
        refresh=refresh,
    )
    if result.is_live and audit_live:
        audit.record(
            MODULE,
            ADVISER,
            "live_run",
            "question",
            key,
            f"Fresh live call for {household['id']} with {result.model}, {result.latency_ms} ms.",
        )
    parsed = parse_answer(result.text)
    index = field_index(household)
    citations = resolve_citations(parsed["citations"], index)
    check = guardrail(parsed["answer"], parsed["citations"], index)
    return {
        "household_id": household["id"],
        "household_name": household["display_name"],
        "question": question,
        "intent": parsed["intent"],
        "answer": parsed["answer"],
        "citations": citations,
        "route_to_advisor": parsed["route_to_advisor"],
        "route_message": parsed["answer"] if parsed["route_to_advisor"] else "",
        "parse_error": parsed["parse_error"],
        "policy_notes": parsed["policy_notes"],
        "guardrail": check,
        "provenance": provenance(result),
        "key": key,
        "raw": parsed["raw"],
        "ok": not parsed["parse_error"] and check["ok"] and bool(parsed["answer"]),
    }


def question_key(question_id: str, household_id: str) -> str:
    return f"{question_id}-{household_id.lower()}"


def route_free_text(text: str, questions_data: dict[str, Any]) -> dict[str, Any]:
    """Map free text to the closest recorded question. Deterministic, no model."""

    lowered = " ".join(str(text or "").lower().split())
    if not lowered:
        return {"question_id": "", "topic": "", "matched": ""}
    words = set(re.split("[^a-z0-9]+", lowered))
    for rule in questions_data["router"]:
        for keyword in rule["keywords"]:
            if keyword in words or (" " in keyword and keyword in lowered):
                return {"question_id": rule["question_id"], "topic": rule["topic"], "matched": keyword}
    return {"question_id": "", "topic": "", "matched": ""}


def blocked_reason(household: dict[str, Any]) -> str:
    if household["consent_status"] == "received":
        return ""
    if household["consent_status"] == "pending":
        return (
            f"Broker Protocol consent is pending for {household['id']}. Protocol data may be used only for "
            "transition preparation until client consent is received, so this answer can be held internally "
            "but not released to the client."
        )
    return (
        f"{household['id']} has no consent on file. A client-facing answer cannot be released until consent is recorded."
    )


def evidence_preview(household: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    return {
        "approver": ADVISER,
        "role": "Pending",
        "approved_at": "",
        "version": "v1",
        "audience": "Client portal, household only",
        "channel": "Portal message (simulated, not sent)",
        "disclosure": record["disclosure"],
        "retention_until": "",
    }


def bundle_hash(bundle: dict[str, Any]) -> str:
    payload = {
        "household_id": bundle["household_id"],
        "question": bundle["question"],
        "intent": bundle["intent"],
        "answer": bundle["answer"],
        "citations": [{"field": item["field"], "value": item["value"]} for item in bundle["citations"]],
        "model": bundle["provenance"]["model"],
        "recorded_at": bundle["provenance"]["created_at"],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
