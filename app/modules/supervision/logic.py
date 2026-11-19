"""Prompts, parsing, policy checks, and evidence for the supervision module.

The model drafts the marketing and classifies the claims. This file owns every
deterministic decision: source checks, policy checks, the approval gate, the
artifact hash, and the review-cycle model. The gate is code, not a model call.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date, datetime, timezone
from typing import Any

from app.platform import llm

from . import data

MODULE = "supervision"

FENCE = chr(96) * 3

FLAG_TYPES = ("performance", "testimonial", "promised_return")
TYPE_LABELS = {
    "factual": "Factual",
    "performance": "Performance",
    "testimonial": "Testimonial",
    "promised_return": "Promised return",
}
TYPE_TONES = {
    "factual": "neutral",
    "performance": "warn",
    "testimonial": "danger",
    "promised_return": "danger",
}
SUPPORT_LABELS = {"supported": "Supported", "unsupported": "Unsupported"}
SUPPORT_TONES = {"supported": "success", "unsupported": "danger"}

APPROVER_SEED = (
    ("marta", "Marta Ellsworth", "Chief Compliance Officer", True, "Independent of drafting."),
    ("owen", "Owen Baptiste", "Supervisory Principal", True, "Independent of drafting."),
    ("dana", "Dana Whitfield", "Adviser", False, "Drafted this piece. Self-review is refused."),
)

# ---------------------------------------------------------------------------
# Prompts. These strings are also shown on the page as the prompt pattern.
# ---------------------------------------------------------------------------

SYSTEM_GENERATION = """You write marketing copy for a US registered investment adviser. You write only from the approved fact sheet and the brief you are given.

Rules:
1. Every number, date, and product fact must appear on the fact sheet with the same value. Never estimate, average, round, or invent a figure.
2. Do not promise or forecast a return. Do not use "will earn", "guaranteed", "safe", or "risk-free".
3. Do not write a testimonial, a client quote, or a client story.
4. Do not name a client, a household, or an account.
5. Put the required disclosure ids in the "disclosures" array exactly as given. Do not paste the disclosure text into the body.
6. Plain, calm, factual prose. No hype adjectives.

Return strict JSON only. No markdown fence, no commentary:
{"headline": "short headline", "body": "paragraph one\n\nparagraph two", "disclosures": ["D-01"]}"""

SYSTEM_CLASSIFY = """You are a compliance claim classifier for a US registered investment adviser. You receive a marketing draft and the approved fact sheet. Split the draft into atomic claims.

Extract only claims a reviewer could check: a number, a product fact, a performance result, a statement about what clients say, or a promise. Skip greetings, calls to action, thanks, and process language that makes no checkable assertion. Claims come from the body, not the headline.

For each claim:
- type: "factual" for a statement that is not a performance or client statement, "performance" for a return, a comparison, or a result, "testimonial" for a client quote or a claim about what clients say, "promised_return" for a promise, guarantee, or forecast of a future return.
- support: "supported" only if every number and material statement appears on the cited fact or disclosure. If a claim restates a required disclosure, cite the disclosure id (for example D-02).
- source: a fact id, a disclosure id, or "none".

Set risk to low, medium, or high for the draft as a whole.

Return strict JSON only. No markdown fence, no commentary:
{"claims": [{"text": "claim as it appears in the draft", "type": "factual", "support": "supported", "source": "F-01"}], "risk": "low"}"""

SYSTEM_REVISE = """You revise marketing copy for a US registered investment adviser after compliance has refused approval. You keep the brief's structure and remove or correct every claim that failed.

Rules:
1. Use only numbers and product facts from the approved fact sheet, with the same values.
2. Remove any performance promise, guarantee, testimonial, or unsupported number. Do not replace one with another.
3. Keep the required disclosures in the "disclosures" array by id. Do not paste their text into the body.
4. Keep the piece within the brief's word limit.
5. In "revision_note", say in one sentence what changed.

Return strict JSON only:
{"headline": "short headline", "body": "paragraph one\n\nparagraph two", "disclosures": ["D-01"], "revision_note": "one sentence"}"""

SYSTEM_EVAL = """You are a compliance claim classifier for a US registered investment adviser. You receive a numbered list of claims and the approved fact sheet. Classify each claim exactly as it is written.

For each claim return its id, the text, a type ("factual", "performance", "testimonial", "promised_return"), a support value ("supported" or "unsupported"), and a source (a fact id or "none").

Return strict JSON only:
{"claims": [{"id": "E-01", "text": "...", "type": "factual", "support": "supported", "source": "F-01"}], "risk": "low"}"""


# ---------------------------------------------------------------------------
# Parsing and normalisation
# ---------------------------------------------------------------------------


def parse_json(text: str | None) -> tuple[Any, str | None]:
    """Parse model JSON defensively. Returns (object, error)."""

    if not text or not text.strip():
        return None, "The model returned an empty response."
    cleaned = text.strip()
    if cleaned.startswith(FENCE):
        cleaned = re.sub(r"^" + FENCE + r"[A-Za-z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*" + FENCE + r"$", "", cleaned).strip()
    try:
        return json.loads(cleaned), None
    except ValueError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start : end + 1]), None
        except ValueError:
            return None, "The model output was not valid JSON."
    return None, "No JSON object was found in the model output."


def normalise_type(value: Any) -> str:
    key = re.sub(r"[^a-z]+", "_", str(value or "").strip().lower()).strip("_")
    if key in {"factual", "fact", "statement", "claim"}:
        return "factual"
    if key in {"performance", "performance_claim", "market_performance", "return", "returns"}:
        return "performance"
    if key in {"testimonial", "testimonial_claim", "client_testimonial", "quote", "client_quote"}:
        return "testimonial"
    if key in {
        "promised_return",
        "promised_returns",
        "promise",
        "promise_of_return",
        "guarantee",
        "guaranteed_return",
        "return_promise",
    }:
        return "promised_return"
    return "factual"


def normalise_support(value: Any) -> str:
    key = str(value or "").strip().lower()
    return "supported" if key in {"supported", "support", "yes", "true", "cited"} else "unsupported"


def normalise_risk(value: Any) -> str:
    key = str(value or "").strip().lower()
    return key if key in {"low", "medium", "high"} else "unknown"


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'%$.,-]*", text or ""))


# ---------------------------------------------------------------------------
# Numbers and the fact sheet
# ---------------------------------------------------------------------------

_PERCENT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s?%")
_CURRENCY = re.compile(
    r"(?:USD|\$)\s?(\d[\d,]*(?:\.\d+)?)\s?(thousand|million|billion)?", re.IGNORECASE
)
_MULTIPLIERS = {"thousand": 1_000.0, "million": 1_000_000.0, "billion": 1_000_000_000.0}


def numeric_tokens(text: str) -> list[tuple[str, float]]:
    """Only percentages and currency amounts. Years and counts are left to the human."""

    tokens: list[tuple[str, float]] = []
    for match in _PERCENT.finditer(text or ""):
        tokens.append(("percent", float(match.group(1).replace(",", ""))))
    for match in _CURRENCY.finditer(text or ""):
        value = float(match.group(1).replace(",", ""))
        value *= _MULTIPLIERS.get((match.group(2) or "").lower(), 1.0)
        tokens.append(("currency", value))
    return tokens


def _same_number(left: tuple[str, float], right: tuple[str, float]) -> bool:
    if left[0] != right[0]:
        return False
    tolerance = max(0.005, abs(left[1]) * 1e-6)
    return abs(left[1] - right[1]) <= tolerance


def _fact_numbers(fact: dict[str, Any]) -> list[tuple[str, float]]:
    tokens: list[tuple[str, float]] = []
    for value in list(fact.get("values") or []) + [fact.get("statement") or ""]:
        tokens.extend(numeric_tokens(str(value)))
    return tokens


def fact_has_number(facts: dict[str, dict[str, Any]], token: tuple[str, float]) -> bool:
    return any(_same_number(token, known) for fact in facts.values() for known in _fact_numbers(fact))


def fact_supports_numbers(fact: dict[str, Any], tokens: list[tuple[str, float]]) -> bool:
    known = _fact_numbers(fact)
    return all(any(_same_number(token, item) for item in known) for token in tokens)


def render_token(token: tuple[str, float]) -> str:
    kind, value = token
    if kind == "percent":
        return "{:g}%".format(value)
    return "USD {:,.0f}".format(value)


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def fact_index(fact_sheet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {fact["id"]: fact for fact in fact_sheet.get("facts", [])}


def disclosure_index(fact_sheet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in fact_sheet.get("disclosures", [])}


def brief_facts(brief: dict[str, Any], fact_sheet: dict[str, Any]) -> list[dict[str, Any]]:
    index = fact_index(fact_sheet)
    return [index[fact_id] for fact_id in brief.get("fact_ids", []) if fact_id in index]


def brief_disclosures(brief: dict[str, Any], fact_sheet: dict[str, Any]) -> list[dict[str, Any]]:
    index = disclosure_index(fact_sheet)
    return [
        index[item_id]
        for item_id in brief.get("required_disclosures", [])
        if item_id in index
    ]


def brief_audience_label(brief: dict[str, Any]) -> str:
    if brief.get("audience_type") == "public":
        return "public social audience"
    bundle = data.briefs_bundle()
    for audience in bundle.get("audiences", []):
        if audience["id"] == brief.get("default_audience"):
            return "{count} client households with recorded consent to marketing".format(
                count=audience["count"]
            )
    return "clients with recorded consent"


def _fact_lines(facts: list[dict[str, Any]]) -> str:
    lines = []
    for fact in facts:
        values = ", ".join(fact.get("values") or []) or "no numeric values"
        lines.append(
            "{id} | {label} | {statement} | values: {values}".format(
                id=fact["id"], label=fact["label"], statement=fact["statement"], values=values
            )
        )
    return "\n".join(lines)


def _disclosure_lines(disclosures: list[dict[str, Any]]) -> str:
    return "\n".join("{id} {text}".format(**item) for item in disclosures)


def build_generation_prompt(brief: dict[str, Any], fact_sheet: dict[str, Any]) -> str:
    points = "\n".join(
        "{n}. {point}".format(n=index, point=point)
        for index, point in enumerate(brief.get("key_points", []), 1)
    )
    supplied = brief.get("must_use") or []
    supplied_block = (
        "\n".join('- "{line}"'.format(line=line) for line in supplied)
        if supplied
        else "None."
    )
    return (
        "Brief {id}: {title}\n"
        "Channel: {channel}\n"
        "Audience: {audience}\n"
        "Length: {limit}\n"
        "Objective: {objective}\n\n"
        "Key points:\n{points}\n\n"
        "Adviser-supplied lines that must appear in the body exactly once, word for word:\n"
        "{supplied}\n\n"
        "Tone: {tone}\n"
        "Required disclosures: {disclosures} (ids only, do not paste the text)\n\n"
        "Approved facts, the only numbers you may use:\n{facts}\n\n"
        "Required disclosure text, for reference only:\n{disclosure_text}\n\n"
        "Write the piece now and return only the JSON object."
    ).format(
        id=brief["id"],
        title=brief["title"],
        channel=brief["channel"],
        audience=brief_audience_label(brief),
        limit=brief.get("word_limit", "as short as the brief allows"),
        objective=brief.get("objective", ""),
        points=points,
        supplied=supplied_block,
        tone=brief.get("tone", "plain"),
        disclosures=", ".join(brief.get("required_disclosures", [])),
        facts=_fact_lines(brief_facts(brief, fact_sheet)),
        disclosure_text=_disclosure_lines(brief_disclosures(brief, fact_sheet)),
    )


def build_classification_prompt(
    brief: dict[str, Any], draft: dict[str, Any], fact_sheet: dict[str, Any]
) -> str:
    return (
        "Draft {id}: {title}\n"
        "Headline: {headline}\n\n"
        "Body:\n{body}\n\n"
        "Disclosures attached: {disclosures}\n\n"
        "Approved fact sheet:\n{facts}\n\n"
        "Required disclosure text:\n{disclosure_text}\n\n"
        "Classify every claim in the body. Use the claim text as it appears in the draft. "
        "Return only the JSON object."
    ).format(
        id=brief["id"],
        title=brief["title"],
        headline=draft.get("headline", ""),
        body=draft.get("body", ""),
        disclosures=", ".join(draft.get("disclosure_ids") or []) or "none",
        facts=_fact_lines(list(fact_index(fact_sheet).values())),
        disclosure_text=_disclosure_lines(list(disclosure_index(fact_sheet).values())),
    )


def build_revision_prompt(
    brief: dict[str, Any],
    draft: dict[str, Any],
    claims: list[dict[str, Any]],
    fact_sheet: dict[str, Any],
) -> str:
    failing = [
        claim
        for claim in claims
        if claim["support"] == "unsupported" or claim["type"] != "factual"
    ]
    failing_lines = "\n".join(
        "- {text} | type: {type} | support: {support} | source: {source}{note}".format(
            text=claim["text"],
            type=claim["type"],
            support=claim["support"],
            source=claim["source"],
            note=(" | " + claim["note"]) if claim.get("note") else "",
        )
        for claim in failing
    ) or "- No individual claim was flagged. The disclosure set was incomplete."
    return (
        "Brief {id}: {title}\n"
        "Channel: {channel}\n"
        "Audience: {audience}\n"
        "Length: {limit}\n"
        "Objective: {objective}\n\n"
        "The compliance reviewer refused this draft. Claims that failed:\n{failing}\n\n"
        "Current headline: {headline}\n\n"
        "Current body:\n{body}\n\n"
        "Approved facts:\n{facts}\n\n"
        "Required disclosures: {disclosures}\n\n"
        "Rewrite the piece so it can pass. Return only the JSON object."
    ).format(
        id=brief["id"],
        title=brief["title"],
        channel=brief["channel"],
        audience=brief_audience_label(brief),
        limit=brief.get("word_limit", ""),
        objective=brief.get("objective", ""),
        failing=failing_lines,
        headline=draft.get("headline", ""),
        body=draft.get("body", ""),
        facts=_fact_lines(list(fact_index(fact_sheet).values())),
        disclosures=", ".join(brief.get("required_disclosures", [])),
    )


def build_eval_prompt(cases: list[dict[str, Any]], fact_sheet: dict[str, Any]) -> str:
    lines = "\n".join(
        '{id} "{claim}"'.format(id=case["id"], claim=case["claim"]) for case in cases
    )
    return (
        "Claims to classify, one per line, id followed by the claim text:\n"
        "{lines}\n\n"
        "Approved fact sheet:\n{facts}\n\n"
        "Return one object per claim with its id. Return only the JSON object."
    ).format(lines=lines, facts=_fact_lines(list(fact_index(fact_sheet).values())))


# ---------------------------------------------------------------------------
# Recorded documents
# ---------------------------------------------------------------------------


def _provenance(result: Any) -> dict[str, Any]:
    usage = result.usage or {}
    return {
        "key": result.key,
        "model": result.model or "unknown",
        "mode": result.mode,
        "is_live": result.is_live,
        "created_at": result.created_at,
        "created_display": iso_display(result.created_at),
        "latency_ms": int(result.latency_ms or 0),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
    }


def draft_key(brief: dict[str, Any], version: int) -> str | None:
    return brief["draft_key"] if version == 1 else brief.get("revision_key")


def claims_key(brief: dict[str, Any], version: int) -> str | None:
    return brief["claims_key"] if version == 1 else brief.get("revision_claims_key")


def load_draft(brief: dict[str, Any], version: int = 1) -> dict[str, Any]:
    key = draft_key(brief, version)
    if not key:
        return {
            "ok": False,
            "missing": False,
            "key": None,
            "version": version,
            "error": "This brief has no recorded revision.",
        }
    result = llm.recorded(MODULE, key)
    if result is None:
        return {
            "ok": False,
            "missing": True,
            "key": key,
            "version": version,
            "error": "No recording yet for {key}. Run scripts/record_supervision.py live.".format(key=key),
        }
    parsed, error = parse_json(result.text)
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "missing": False,
            "key": key,
            "version": version,
            "error": error or "The recorded draft is not a JSON object.",
            "raw": result.text,
            "provenance": _provenance(result),
        }
    body = str(parsed.get("body") or "").strip()
    headline = str(parsed.get("headline") or "").strip()
    if not body:
        return {
            "ok": False,
            "missing": False,
            "key": key,
            "version": version,
            "error": "The recorded draft has no body text.",
            "raw": result.text,
            "provenance": _provenance(result),
        }
    raw_disclosures = parsed.get("disclosures")
    if not isinstance(raw_disclosures, list):
        raw_disclosures = []
    known = disclosure_index(data.fact_sheet())
    disclosures = []
    for item in raw_disclosures:
        item_id = str(item).strip()
        if not item_id:
            continue
        disclosures.append(
            {
                "id": item_id,
                "text": known.get(item_id, {}).get("text", "Unknown disclosure id."),
                "label": known.get(item_id, {}).get("label", "Unknown"),
                "known": item_id in known,
            }
        )
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()] or [body]
    return {
        "ok": True,
        "key": key,
        "version": version,
        "headline": headline,
        "body": body,
        "paragraphs": paragraphs,
        "disclosures": disclosures,
        "disclosure_ids": [item["id"] for item in disclosures],
        "word_count": word_count(body),
        "revision_note": str(parsed.get("revision_note") or "").strip(),
        "provenance": _provenance(result),
        "raw": result.text,
    }


def validate_claims(
    raw_claims: list[Any], fact_sheet: dict[str, Any]
) -> list[dict[str, Any]]:
    facts = fact_index(fact_sheet)
    disclosures = disclosure_index(fact_sheet)
    claims: list[dict[str, Any]] = []
    for index, item in enumerate(raw_claims, 1):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        raw_type = item.get("type")
        raw_support = item.get("support")
        claim_type = normalise_type(raw_type)
        support = normalise_support(raw_support)
        source = str(item.get("source") or "none").strip() or "none"
        note = ""
        fact = facts.get(source)
        disclosure = disclosures.get(source) if fact is None else None
        if support == "supported":
            if fact is None and disclosure is None:
                support = "unsupported"
                note = "The cited source is not on the fact sheet."
            elif fact is not None:
                missing = [
                    token
                    for token in numeric_tokens(text)
                    if not fact_supports_numbers(fact, [token])
                ]
                if missing:
                    support = "unsupported"
                    note = "The cited fact does not carry {value}.".format(
                        value=", ".join(render_token(token) for token in missing)
                    )
            elif numeric_tokens(text):
                support = "unsupported"
                note = "A disclosure does not carry numbers."
        claims.append(
            {
                "n": index,
                "label": "C{0}".format(index),
                "text": text,
                "type": claim_type,
                "type_label": TYPE_LABELS[claim_type],
                "type_tone": TYPE_TONES[claim_type],
                "support": support,
                "support_label": SUPPORT_LABELS[support],
                "support_tone": SUPPORT_TONES[support],
                "source": source,
                "source_kind": (
                    "fact" if fact else ("disclosure" if disclosure else "none")
                ),
                "source_label": (
                    "{id} {label}".format(id=source, label=(fact or disclosure).get("label", ""))
                    if (fact or disclosure)
                    else ("No source" if source in {"", "none"} else source + " (not on the sheet)")
                ),
                "source_statement": (fact or disclosure or {}).get("statement", "")
                or (disclosure or {}).get("text", ""),
                "note": note,
                "adjusted": bool(note),
                "model_type": str(raw_type or ""),
                "model_support": normalise_support(raw_support),
                "flagged": claim_type != "factual" or support == "unsupported",
            }
        )
    return claims


def load_claims(
    brief: dict[str, Any], fact_sheet: dict[str, Any], version: int = 1
) -> dict[str, Any]:
    key = claims_key(brief, version)
    if not key:
        return {
            "ok": False,
            "missing": False,
            "key": None,
            "version": version,
            "claims": [],
            "error": "No claim classification is recorded for this version.",
        }
    result = llm.recorded(MODULE, key)
    if result is None:
        return {
            "ok": False,
            "missing": True,
            "key": key,
            "version": version,
            "claims": [],
            "error": "No claim classification is recorded for {key}.".format(key=key),
        }
    parsed, error = parse_json(result.text)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("claims"), list):
        return {
            "ok": False,
            "missing": False,
            "key": key,
            "version": version,
            "claims": [],
            "error": error or "The recorded classification has no claims array.",
            "raw": result.text,
            "provenance": _provenance(result),
        }
    claims = validate_claims(parsed["claims"], fact_sheet)
    if not claims:
        return {
            "ok": False,
            "missing": False,
            "key": key,
            "version": version,
            "claims": [],
            "error": "The recorded classification returned no usable claims.",
            "raw": result.text,
            "provenance": _provenance(result),
        }
    return {
        "ok": True,
        "key": key,
        "version": version,
        "claims": claims,
        "risk": normalise_risk(parsed.get("risk")),
        "provenance": _provenance(result),
        "raw": result.text,
    }


# ---------------------------------------------------------------------------
# Policy checks and the gate
# ---------------------------------------------------------------------------


def _numbers_in_claims(claims: list[dict[str, Any]], supported_only: bool = True) -> list[tuple[str, float]]:
    tokens: list[tuple[str, float]] = []
    for claim in claims:
        if supported_only and claim["support"] != "supported":
            continue
        tokens.extend(numeric_tokens(claim["text"]))
    return tokens


def evaluate(
    brief: dict[str, Any],
    draft: dict[str, Any],
    claims_result: dict[str, Any],
    fact_sheet: dict[str, Any],
) -> dict[str, Any]:
    """Run the policy checks. Any fail blocks approval on the server."""

    facts = fact_index(fact_sheet)
    claims = claims_result.get("claims") or []
    checks: list[dict[str, Any]] = []

    def add(key: str, name: str, result: str, detail: str) -> None:
        checks.append({"key": key, "name": name, "result": result, "detail": detail})

    if not claims_result.get("ok"):
        add(
            "classification",
            "Claim classification",
            "fail",
            claims_result.get("error") or "The second pass did not return a usable classification.",
        )
    else:
        add(
            "classification",
            "Claim classification",
            "pass",
            "{n} claims classified, draft risk {risk}.".format(
                n=len(claims), risk=claims_result.get("risk", "unknown")
            ),
        )

    performance = [claim for claim in claims if claim["type"] == "performance"]
    if not performance:
        add("performance_claim", "Performance claim", "pass", "No performance claim in this draft.")
    elif any(claim["support"] == "unsupported" for claim in performance):
        bad = ", ".join(claim["label"] for claim in performance if claim["support"] == "unsupported")
        add(
            "performance_claim",
            "Performance claim",
            "fail",
            "A performance claim is not sourced to the fact sheet: {bad}.".format(bad=bad),
        )
    else:
        add(
            "performance_claim",
            "Performance claim",
            "warn",
            "{n} performance {word}, all sourced: {labels}. D-01 and D-02 must be attached.".format(
                n=len(performance),
                word="claim" if len(performance) == 1 else "claims",
                labels=", ".join(claim["label"] for claim in performance),
            ),
        )

    testimonials = [claim for claim in claims if claim["type"] == "testimonial"]
    if testimonials:
        add(
            "testimonial",
            "Testimonial",
            "fail",
            "Client testimonials are not used in firm marketing (D-05). Found: {labels}.".format(
                labels=", ".join(claim["label"] for claim in testimonials)
            ),
        )
    else:
        add("testimonial", "Testimonial", "pass", "No testimonial or client quote found.")

    promises = [claim for claim in claims if claim["type"] == "promised_return"]
    if promises:
        add(
            "promised_return",
            "Promised return",
            "fail",
            "A promise or guarantee of a return is not permitted. Found: {labels}.".format(
                labels=", ".join(claim["label"] for claim in promises)
            ),
        )
    else:
        add("promised_return", "Promised return", "pass", "No promise, guarantee, or forecast found.")

    required = list(brief.get("required_disclosures", []))
    attached = list(draft.get("disclosure_ids") or [])
    missing = [item for item in required if item not in attached]
    if missing:
        add(
            "missing_disclosure",
            "Missing disclosure",
            "fail",
            "Required disclosure {missing} is not attached. Required set: {required}.".format(
                missing=", ".join(missing), required=", ".join(required)
            ),
        )
    else:
        add(
            "missing_disclosure",
            "Missing disclosure",
            "pass",
            "All {n} required disclosures attached: {ids}.".format(
                n=len(required), ids=", ".join(required)
            ),
        )

    unsupported = [claim for claim in claims if claim["support"] == "unsupported"]
    hard_unsupported = [claim for claim in unsupported if numeric_tokens(claim["text"])]
    if hard_unsupported:
        add(
            "unsupported_claim",
            "Unsupported claim",
            "fail",
            "{n} {word} not supported. {m} {verb} a number and {verb2} approval: {items}.".format(
                n=len(unsupported),
                word="claim is" if len(unsupported) == 1 else "claims are",
                m=len(hard_unsupported),
                verb="carries" if len(hard_unsupported) == 1 else "carry",
                verb2="blocks" if len(hard_unsupported) == 1 else "block",
                items="; ".join(
                    "{label} \"{text}\"".format(label=claim["label"], text=_shorten(claim["text"]))
                    for claim in hard_unsupported
                ),
            ),
        )
    elif unsupported:
        add(
            "unsupported_claim",
            "Unsupported claim",
            "warn",
            "{n} qualitative {word} no fact sheet source: {items}. The reviewer clears these by judgment; none carries a number.".format(
                n=len(unsupported),
                word="claim has" if len(unsupported) == 1 else "claims have",
                items=", ".join(claim["label"] for claim in unsupported),
            ),
        )
    else:
        add("unsupported_claim", "Unsupported claim", "pass", "Every claim is sourced to the fact sheet.")

    body_tokens = numeric_tokens(draft.get("body") or "")
    unapproved: list[str] = []
    for token in body_tokens:
        if not fact_has_number(facts, token):
            rendered = render_token(token)
            if rendered not in unapproved:
                unapproved.append(rendered)
    if unapproved:
        add(
            "unapproved_number",
            "Number outside the fact sheet",
            "fail",
            "The body carries {what} which is not on the approved fact sheet.".format(
                what=", ".join(unapproved)
            ),
        )
    else:
        supported_numbers = _numbers_in_claims(claims)
        unclaimed: list[str] = []
        for token in body_tokens:
            if not any(_same_number(token, known) for known in supported_numbers):
                rendered = render_token(token)
                if rendered not in unclaimed:
                    unclaimed.append(rendered)
        if unclaimed:
            add(
                "unapproved_number",
                "Number outside the fact sheet",
                "warn",
                "On the fact sheet but not in a supported claim: {what}.".format(
                    what=", ".join(unclaimed)
                ),
            )
        else:
            add(
                "unapproved_number",
                "Number outside the fact sheet",
                "pass",
                "Every percentage and currency figure in the body is on the fact sheet and in a supported claim.",
            )

    failed = [check for check in checks if check["result"] == "fail"]
    warnings = [check for check in checks if check["result"] == "warn"]
    computed_risk = "high" if failed else ("medium" if warnings else "low")
    return {
        "checks": checks,
        "blocked": bool(failed),
        "blocking": failed,
        "passed": [check for check in checks if check["result"] == "pass"],
        "warnings": warnings,
        "risk_model": claims_result.get("risk", "unknown"),
        "risk_computed": computed_risk,
        "counts": {
            "total": len(checks),
            "pass": len([check for check in checks if check["result"] == "pass"]),
            "warn": len(warnings),
            "fail": len(failed),
        },
    }


def _shorten(text: str, limit: int = 96) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


# ---------------------------------------------------------------------------
# Evidence file
# ---------------------------------------------------------------------------


def approvals() -> list[dict[str, Any]]:
    adviser = data.briefs_bundle().get("adviser", "Dana Whitfield")
    people = []
    for key, name, role, selectable, note in APPROVER_SEED:
        if key == "dana":
            name = adviser
        people.append(
            {
                "id": key,
                "name": name,
                "role": role,
                "selectable": selectable,
                "label": "{name}, {role}".format(name=name, role=role),
                "note": note,
            }
        )
    return people


def find_approver(approver_id: str) -> dict[str, Any] | None:
    for person in approvals():
        if person["id"] == approver_id:
            return person
    return None


def retention_date(day: date, years: int = 5) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def artifact_hash(
    brief: dict[str, Any],
    version: int,
    draft: dict[str, Any],
    claims: list[dict[str, Any]],
    disclosure_ids: list[str],
    audience_id: str,
) -> str:
    payload = {
        "brief": brief["id"],
        "version": version,
        "channel": brief.get("channel", ""),
        "audience": audience_id,
        "headline": draft.get("headline", ""),
        "body": draft.get("body", ""),
        "disclosures": sorted(disclosure_ids),
        "claims": [
            {
                "text": claim["text"],
                "type": claim["type"],
                "support": claim["support"],
                "source": claim["source"],
            }
            for claim in claims
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def content_hash(
    brief: dict[str, Any],
    version: int,
    draft: dict[str, Any],
    claims: list[dict[str, Any]],
    audience_id: str,
) -> str:
    return artifact_hash(
        brief, version, draft, claims, list(draft.get("disclosure_ids") or []), audience_id
    )


def evidence_preview(
    brief: dict[str, Any],
    draft: dict[str, Any],
    claims: list[dict[str, Any]],
    version: int,
    audience_id: str,
    audience_label: str,
) -> dict[str, Any]:
    digest = content_hash(brief, version, draft, claims, audience_id)
    return {
        "version": version,
        "audience_id": audience_id,
        "audience": audience_label,
        "channel": brief.get("channel", ""),
        "disclosures": list(draft.get("disclosures") or []),
        "retention_until": retention_date(datetime.now(timezone.utc).date()).isoformat(),
        "hash": digest,
    }


def seal_evidence(
    brief: dict[str, Any],
    draft: dict[str, Any],
    claims: list[dict[str, Any]],
    checks: list[dict[str, Any]],
    version: int,
    approver: dict[str, Any],
    audience_id: str,
    audience_label: str,
    note: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    sealed_at = now or datetime.now(timezone.utc)
    digest = content_hash(brief, version, draft, claims, audience_id)
    stamp = sealed_at.isoformat(timespec="seconds")
    return {
        "record": "marketing evidence file",
        "status": "approved",
        "brief_id": brief["id"],
        "title": brief["title"],
        "approver": approver["name"],
        "approver_id": approver["id"],
        "role": approver["role"],
        "approved_at": stamp,
        "approved_display": iso_display(stamp),
        "version": version,
        "audience_id": audience_id,
        "audience": audience_label,
        "channel": brief.get("channel", ""),
        "disclosures": list(draft.get("disclosures") or []),
        "retention_until": retention_date(sealed_at.date()).isoformat(),
        "artifact_hash": digest,
        "note": note,
        "model": draft.get("provenance", {}).get("model", ""),
        "model_mode": draft.get("provenance", {}).get("mode", ""),
        "model_recorded_at": draft.get("provenance", {}).get("created_at", ""),
        "claims": [
            {
                "text": claim["text"],
                "type": claim["type"],
                "support": claim["support"],
                "source": claim["source"],
            }
            for claim in claims
        ],
        "policy_checks": [
            {"key": check["key"], "result": check["result"]} for check in checks
        ],
    }


# ---------------------------------------------------------------------------
# The modelled review cycle
# ---------------------------------------------------------------------------


def review_cycle_model() -> dict[str, Any]:
    """An assumption set, not a time study. Every step is checkable by hand."""

    pieces_per_year = 96
    baseline = {
        "pickup": 0.8,
        "first_pass": 1.4,
        "rework_days": 1.8,
        "rework_rate": 0.45,
        "signoff": 0.3,
    }
    loop = {
        "pickup": 0.8,
        "first_pass": 0.5,
        "rework_days": 1.8,
        "rework_rate": 0.15,
        "signoff": 0.3,
    }

    def total(model: dict[str, float]) -> float:
        return (
            model["pickup"]
            + model["first_pass"]
            + model["rework_days"] * model["rework_rate"]
            + model["signoff"]
        )

    baseline_days = total(baseline)
    loop_days = total(loop)
    saved_days = baseline_days - loop_days
    saved_pct = saved_days / baseline_days * 100
    returns_baseline = pieces_per_year * baseline["rework_rate"]
    returns_loop = pieces_per_year * loop["rework_rate"]
    return {
        "pieces_per_year": pieces_per_year,
        "baseline": baseline,
        "loop": loop,
        "baseline_days": round(baseline_days, 2),
        "loop_days": round(loop_days, 2),
        "saved_days": round(saved_days, 2),
        "saved_pct": round(saved_pct, 1),
        "cumulative_days_saved": round(saved_days * pieces_per_year, 1),
        "returns_baseline": round(returns_baseline, 1),
        "returns_loop": round(returns_loop, 1),
        "returns_avoided": round(returns_baseline - returns_loop, 1),
        "steps": [
            {
                "name": "Reviewer pickup",
                "baseline": "{:.1f} days".format(baseline["pickup"]),
                "loop": "{:.1f} days".format(loop["pickup"]),
                "note": "Unchanged. The queue does not get shorter.",
            },
            {
                "name": "First pass",
                "baseline": "{:.1f} days".format(baseline["first_pass"]),
                "loop": "{:.1f} days".format(loop["first_pass"]),
                "note": "Claims and disclosures arrive pre-checked, so the reviewer confirms rather than sources.",
            },
            {
                "name": "Rework loop",
                "baseline": "{:.2f} days".format(baseline["rework_days"] * baseline["rework_rate"]),
                "loop": "{:.2f} days".format(loop["rework_days"] * loop["rework_rate"]),
                "note": "1.8 days x 45% of pieces, then 1.8 days x 15% of pieces.",
            },
            {
                "name": "Sign-off and filing",
                "baseline": "{:.1f} days".format(baseline["signoff"]),
                "loop": "{:.1f} days".format(loop["signoff"]),
                "note": "The evidence file is assembled by the tool, not by hand.",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def iso_display(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def date_display(value: date | str | None) -> str:
    if value is None:
        return "Not set"
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value[:10])
        except ValueError:
            return value
    else:
        parsed = value
    return "{month} {day}, {year}".format(
        month=parsed.strftime("%b"), day=parsed.day, year=parsed.year
    )


def usd(value: int | float) -> str:
    return "USD {:,}".format(int(value))


def pct(value: float, digits: int = 1) -> str:
    return ("{:." + str(digits) + "f}%").format(value)


def annotate_paragraph(paragraph: str, claims: list[dict[str, Any]]) -> str:
    """Return escaped HTML for one paragraph with claim marks. Safe to render."""

    spans: list[tuple[int, int, dict[str, Any]]] = []
    lower = paragraph.lower()
    for claim in claims:
        needle = claim["text"].strip().rstrip(".").lower()
        if len(needle) < 12:
            continue
        start = lower.find(needle)
        if start < 0:
            continue
        end = start + len(needle)
        if any(
            not (end <= existing_start or start >= existing_end)
            for existing_start, existing_end, _ in spans
        ):
            continue
        spans.append((start, end, claim))
    spans.sort(key=lambda item: item[0])
    pieces: list[str] = []
    cursor = 0
    for start, end, claim in spans:
        pieces.append(html.escape(paragraph[cursor:start]))
        pieces.append(
            '<span class="sup-mark sup-mark--{tone}" title="{label} {type}, {support}">{text}</span>'
            '<sup class="sup-mark-num">{n}</sup>'.format(
                tone=claim["support_tone"] if claim["support"] == "unsupported" else claim["type_tone"],
                label=claim["label"],
                type=claim["type_label"],
                support=claim["support_label"],
                text=html.escape(paragraph[start:end]),
                n=claim["n"],
            )
        )
        cursor = end
    pieces.append(html.escape(paragraph[cursor:]))
    return "".join(pieces)


def claim_located(body: str, claim: dict[str, Any]) -> bool:
    needle = claim["text"].strip().rstrip(".").lower()
    return len(needle) >= 12 and needle in (body or "").lower()
